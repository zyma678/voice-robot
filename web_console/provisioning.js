/*
 * Web Bluetooth Wi-Fi provisioning client for the ESP32-S3 voice robot.
 *
 * Speaks Espressif protocomm over BLE GATT (security scheme 1):
 *   prov-session  endpoint -> X25519 + AES-256-CTR + PoP handshake (sec1.proto)
 *   prov-config   endpoint -> SetConfig / ApplyConfig / GetStatus (wifi_config.proto)
 *
 * The firmware pins the provisioning service UUID (see PROV_SERVICE_UUID below /
 * wifi_client.c s_prov_service_uuid) because Web Bluetooth cannot enumerate
 * services it has not been told about up front.
 *
 * Dual-mode: a classic browser <script> (attaches window.VRProvisioning and
 * self-inits the console tab) AND a CommonJS module (Node unit tests import the
 * pure crypto/protobuf logic). No build step, no third-party deps.
 */
(function (global, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.VRProvisioning = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Must match s_prov_service_uuid in firmware/main/wifi_client.c.
  const PROV_SERVICE_UUID = "021a9004-0382-4aea-bff4-6b3f1c5adfb4";
  const USER_DESC_DESCRIPTOR = 0x2901; // GATT Characteristic User Description.

  const subtle =
    (typeof globalThis !== "undefined" &&
      globalThis.crypto &&
      globalThis.crypto.subtle) ||
    null;

  // ---- small byte helpers ------------------------------------------------
  const te = new TextEncoder();
  const td = new TextDecoder();
  const utf8 = (s) => te.encode(s);
  const fromUtf8 = (u8) => td.decode(u8);

  function concatBytes(arrays) {
    let total = 0;
    for (const a of arrays) total += a.length;
    const out = new Uint8Array(total);
    let off = 0;
    for (const a of arrays) {
      out.set(a, off);
      off += a.length;
    }
    return out;
  }

  function bytesEqual(a, b) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }

  function bytesToHex(u8) {
    let s = "";
    for (const b of u8) s += b.toString(16).padStart(2, "0");
    return s;
  }

  function hexToBytes(hex) {
    const out = new Uint8Array(hex.length / 2);
    for (let i = 0; i < out.length; i++) {
      out[i] = parseInt(hex.substr(i * 2, 2), 16);
    }
    return out;
  }

  // ---- X25519 (RFC 7748, BigInt Montgomery ladder) -----------------------
  const P25519 = (1n << 255n) - 19n;
  const A24 = 121665n;

  function fpow(base, exp, mod) {
    let result = 1n;
    base %= mod;
    while (exp > 0n) {
      if (exp & 1n) result = (result * base) % mod;
      base = (base * base) % mod;
      exp >>= 1n;
    }
    return result;
  }

  function decodeLE(u8) {
    let n = 0n;
    for (let i = u8.length - 1; i >= 0; i--) n = (n << 8n) | BigInt(u8[i]);
    return n;
  }

  function encodeLE32(n) {
    const out = new Uint8Array(32);
    for (let i = 0; i < 32; i++) {
      out[i] = Number(n & 0xffn);
      n >>= 8n;
    }
    return out;
  }

  function clampScalar(k) {
    const e = k.slice(0, 32);
    e[0] &= 248;
    e[31] &= 127;
    e[31] |= 64;
    return decodeLE(e);
  }

  // X25519 scalar multiplication: scalar (32B Uint8Array), uCoord (32B Uint8Array).
  function x25519(scalarBytes, uBytes) {
    const k = clampScalar(scalarBytes);
    const u = decodeLE(uBytes) % P25519;
    let x1 = u;
    let x2 = 1n;
    let z2 = 0n;
    let x3 = u;
    let z3 = 1n;
    let swap = 0n;
    const p = P25519;
    for (let t = 254; t >= 0; t--) {
      const kt = (k >> BigInt(t)) & 1n;
      swap ^= kt;
      if (swap) {
        [x2, x3] = [x3, x2];
        [z2, z3] = [z3, z2];
      }
      swap = kt;
      const a = (x2 + z2) % p;
      const aa = (a * a) % p;
      const b = (x2 - z2 + p) % p;
      const bb = (b * b) % p;
      const e = (aa - bb + p) % p;
      const c = (x3 + z3) % p;
      const d = (x3 - z3 + p) % p;
      const da = (d * a) % p;
      const cb = (c * b) % p;
      x3 = (da + cb) % p;
      x3 = (x3 * x3) % p;
      z3 = (da - cb + p) % p;
      z3 = (x1 * ((z3 * z3) % p)) % p;
      x2 = (aa * bb) % p;
      z2 = (e * ((aa + (A24 * e) % p) % p)) % p;
    }
    if (swap) {
      [x2, x3] = [x3, x2];
      [z2, z3] = [z3, z2];
    }
    const res = (x2 * fpow(z2, p - 2n, p)) % p;
    return encodeLE32(res);
  }

  function x25519Base(scalarBytes) {
    const base = new Uint8Array(32);
    base[0] = 9;
    return x25519(scalarBytes, base);
  }

  // ---- minimal protobuf reader / writer ----------------------------------
  function PBWriter() {
    this.parts = [];
  }
  PBWriter.prototype.varint = function (value) {
    let v = BigInt(value);
    const bytes = [];
    do {
      let byte = Number(v & 0x7fn);
      v >>= 7n;
      if (v > 0n) byte |= 0x80;
      bytes.push(byte);
    } while (v > 0n);
    this.parts.push(Uint8Array.from(bytes));
    return this;
  };
  PBWriter.prototype.tag = function (field, wire) {
    return this.varint((field << 3) | wire);
  };
  PBWriter.prototype.uint = function (field, value) {
    this.tag(field, 0);
    return this.varint(value);
  };
  PBWriter.prototype.bytes = function (field, u8) {
    this.tag(field, 2);
    this.varint(u8.length);
    this.parts.push(u8);
    return this;
  };
  PBWriter.prototype.message = function (field, u8) {
    return this.bytes(field, u8);
  };
  PBWriter.prototype.finish = function () {
    return concatBytes(this.parts);
  };

  // Decode a protobuf message into { field: [values...] }.
  // Varint fields -> Number; length-delimited -> Uint8Array.
  function decodeMessage(buf) {
    const fields = {};
    let pos = 0;
    function readVarint() {
      let shift = 0n;
      let result = 0n;
      while (true) {
        const byte = buf[pos++];
        result |= BigInt(byte & 0x7f) << shift;
        if ((byte & 0x80) === 0) break;
        shift += 7n;
      }
      return result;
    }
    function push(field, value) {
      (fields[field] || (fields[field] = [])).push(value);
    }
    while (pos < buf.length) {
      const tag = Number(readVarint());
      const field = tag >>> 3;
      const wire = tag & 7;
      if (wire === 0) {
        push(field, Number(readVarint()));
      } else if (wire === 2) {
        const len = Number(readVarint());
        push(field, buf.slice(pos, pos + len));
        pos += len;
      } else if (wire === 5) {
        push(field, buf.slice(pos, pos + 4));
        pos += 4;
      } else if (wire === 1) {
        push(field, buf.slice(pos, pos + 8));
        pos += 8;
      } else {
        throw new Error("unsupported wire type " + wire);
      }
    }
    return fields;
  }

  const first = (fields, n) => (fields[n] ? fields[n][0] : undefined);

  // ---- message codecs (sec1.proto + wifi_config.proto) -------------------
  // session.proto: SecSchemeVersion { SecScheme1 = 1 }
  // SessionData { sec_ver=2; sec1=11 }
  // Sec1Payload { msg=1; sc0=20; sr0=21; sc1=22; sr1=23 }
  const Sec1MsgType = {
    Command0: 0,
    Response0: 1,
    Command1: 2,
    Response1: 3,
  };

  function encodeSessionCmd0(clientPubkey) {
    const cmd0 = new PBWriter().bytes(1, clientPubkey).finish();
    const sec1 = new PBWriter()
      .uint(1, Sec1MsgType.Command0)
      .message(20, cmd0)
      .finish();
    return new PBWriter().uint(2, 1).message(11, sec1).finish();
  }

  function encodeSessionCmd1(clientVerifyData) {
    const cmd1 = new PBWriter().bytes(1, clientVerifyData).finish();
    const sec1 = new PBWriter()
      .uint(1, Sec1MsgType.Command1)
      .message(22, cmd1)
      .finish();
    return new PBWriter().uint(2, 1).message(11, sec1).finish();
  }

  function decodeSec1Payload(buf) {
    const session = decodeMessage(buf);
    const sec1Bytes = first(session, 11);
    if (!sec1Bytes) throw new Error("response missing sec1 payload");
    const sec1 = decodeMessage(sec1Bytes);
    const resp0Bytes = first(sec1, 21);
    const resp1Bytes = first(sec1, 23);
    if (resp0Bytes) {
      const r = decodeMessage(resp0Bytes);
      return {
        kind: "resp0",
        status: first(r, 1) || 0,
        devicePubkey: first(r, 2),
        deviceRandom: first(r, 3),
      };
    }
    if (resp1Bytes) {
      const r = decodeMessage(resp1Bytes);
      return {
        kind: "resp1",
        status: first(r, 1) || 0,
        deviceVerifyData: first(r, 2),
      };
    }
    throw new Error("unexpected sec1 response");
  }

  // wifi_config.proto WiFiConfigMsgType
  const WifiCfgMsg = {
    CmdGetStatus: 0,
    CmdSetConfig: 2,
    CmdApplyConfig: 4,
  };
  // WifiStationState
  const StaState = { Connected: 0, Connecting: 1, Disconnected: 2, Failed: 3 };

  function encodeSetConfig(ssid, passphrase) {
    const cmd = new PBWriter()
      .bytes(1, utf8(ssid))
      .bytes(2, utf8(passphrase))
      .finish();
    return new PBWriter()
      .uint(1, WifiCfgMsg.CmdSetConfig)
      .message(12, cmd)
      .finish();
  }

  function encodeApplyConfig() {
    const cmd = new PBWriter().finish(); // empty CmdApplyConfig
    return new PBWriter()
      .uint(1, WifiCfgMsg.CmdApplyConfig)
      .message(14, cmd)
      .finish();
  }

  function encodeGetStatus() {
    const cmd = new PBWriter().finish(); // empty CmdGetStatus
    return new PBWriter()
      .uint(1, WifiCfgMsg.CmdGetStatus)
      .message(10, cmd)
      .finish();
  }

  function decodeRespStatus(buf) {
    // RespSetConfig (13) / RespApplyConfig (15): { status=1 }
    const payload = decodeMessage(buf);
    const respBytes = first(payload, 13) || first(payload, 15);
    if (!respBytes) return { status: -1 };
    return { status: first(decodeMessage(respBytes), 1) || 0 };
  }

  function decodeRespGetStatus(buf) {
    const payload = decodeMessage(buf);
    const respBytes = first(payload, 11); // resp_get_status
    if (!respBytes) return { status: -1, staState: StaState.Disconnected };
    const r = decodeMessage(respBytes);
    const result = {
      status: first(r, 1) || 0,
      staState: first(r, 2) || 0,
      failReason: r[10] ? first(r, 10) : undefined,
      connected: undefined,
    };
    const connBytes = first(r, 11); // WifiConnectedState
    if (connBytes) {
      const c = decodeMessage(connBytes);
      const ip = first(c, 1);
      const ssid = first(c, 4);
      result.connected = {
        ip: ip ? fromUtf8(ip) : "",
        ssid: ssid ? fromUtf8(ssid) : "",
      };
    }
    return result;
  }

  // ---- AES-256-CTR matching mbedtls (continuous counter, nc_off) ---------
  // WebCrypto has no raw AES block / ECB, so derive each keystream block as
  // AES-CTR(counter=block) over 16 zero bytes, and run CTR + partial-block
  // offset ourselves so the counter stays continuous across messages exactly
  // like the device's single mbedtls cipher context.
  function CtrCipher(key, iv) {
    this.key = key; // CryptoKey (AES-CTR, 256-bit)
    this.counter = iv.slice(0); // 16-byte Uint8Array, big-endian
    this.keystream = new Uint8Array(16);
    this.ncOff = 0;
  }
  CtrCipher.create = async function (rawKey, iv) {
    const key = await subtle.importKey("raw", rawKey, { name: "AES-CTR" }, false, [
      "encrypt",
    ]);
    return new CtrCipher(key, iv);
  };
  function incrementBE(counter) {
    for (let i = counter.length - 1; i >= 0; i--) {
      counter[i] = (counter[i] + 1) & 0xff;
      if (counter[i] !== 0) break;
    }
  }
  CtrCipher.prototype._block = async function (counterBlock) {
    const zeros = new Uint8Array(16);
    const ks = await subtle.encrypt(
      { name: "AES-CTR", counter: counterBlock, length: 128 },
      this.key,
      zeros
    );
    return new Uint8Array(ks);
  };
  // CTR is symmetric: process() both encrypts and decrypts.
  CtrCipher.prototype.process = async function (data) {
    const out = new Uint8Array(data.length);
    for (let i = 0; i < data.length; i++) {
      if (this.ncOff === 0) {
        this.keystream = await this._block(this.counter);
        incrementBE(this.counter);
      }
      out[i] = data[i] ^ this.keystream[this.ncOff];
      this.ncOff = (this.ncOff + 1) & 0x0f;
    }
    return out;
  };

  async function sha256(u8) {
    return new Uint8Array(await subtle.digest("SHA-256", u8));
  }

  // ---- Security1 session over a transport --------------------------------
  // transport.request(endpointName, Uint8Array) -> Promise<Uint8Array>
  function Security1Session(transport, pop) {
    this.transport = transport;
    this.pop = pop || "";
    this.cipher = null;
    this.clientPubkey = null;
  }

  Security1Session.prototype.establish = async function () {
    // Step 0: client keypair.
    const clientPriv = new Uint8Array(32);
    globalThis.crypto.getRandomValues(clientPriv);
    this.clientPubkey = x25519Base(clientPriv);

    const resp0Bytes = await this.transport.request(
      "prov-session",
      encodeSessionCmd0(this.clientPubkey)
    );
    const resp0 = decodeSec1Payload(resp0Bytes);
    if (resp0.kind !== "resp0") throw new Error("bad session response 0");

    // Shared secret, then XOR with SHA256(pop) when a PoP is set.
    let sharedKey = x25519(clientPriv, resp0.devicePubkey);
    if (this.pop) {
      const digest = await sha256(utf8(this.pop));
      const mixed = new Uint8Array(32);
      for (let i = 0; i < 32; i++) mixed[i] = sharedKey[i] ^ digest[i];
      sharedKey = mixed;
    }

    this.cipher = await CtrCipher.create(sharedKey, resp0.deviceRandom);

    // Step 1: client proves possession by encrypting the device pubkey; device
    // replies with the encrypted client pubkey, which must decrypt to ours.
    const clientVerify = await this.cipher.process(resp0.devicePubkey);
    const resp1Bytes = await this.transport.request(
      "prov-session",
      encodeSessionCmd1(clientVerify)
    );
    const resp1 = decodeSec1Payload(resp1Bytes);
    if (resp1.kind !== "resp1") throw new Error("bad session response 1");
    const decrypted = await this.cipher.process(resp1.deviceVerifyData);
    if (!bytesEqual(decrypted, this.clientPubkey)) {
      throw new Error("device verification failed (wrong PoP?)");
    }
  };

  // Encrypted request on the prov-config endpoint (continues the CTR stream).
  Security1Session.prototype.secureRequest = async function (endpoint, plaintext) {
    const encrypted = await this.cipher.process(plaintext);
    const respCipher = await this.transport.request(endpoint, encrypted);
    return this.cipher.process(respCipher);
  };

  // ---- BLE transport (browser only) --------------------------------------
  async function connectBleTransport(namePrefix, log) {
    if (!global.navigator || !global.navigator.bluetooth) {
      throw new Error(
        "当前浏览器不支持 Web Bluetooth，请用 Chrome/Edge 并通过 localhost 或 HTTPS 打开。"
      );
    }
    const device = await global.navigator.bluetooth.requestDevice({
      filters: [{ namePrefix: namePrefix || "PROV_" }],
      optionalServices: [PROV_SERVICE_UUID],
    });
    log("info", `选中设备 ${device.name || "(未命名)"}`);
    const server = await device.gatt.connect();
    const service = await server.getPrimaryService(PROV_SERVICE_UUID);
    const characteristics = await service.getCharacteristics();

    // Map endpoint name -> characteristic via the 0x2901 user-description.
    const endpoints = {};
    for (const ch of characteristics) {
      try {
        const desc = await ch.getDescriptor(USER_DESC_DESCRIPTOR);
        const value = await desc.readValue();
        const name = td.decode(value).replace(/ +$/, "");
        if (name) endpoints[name] = ch;
      } catch (_e) {
        /* characteristic without a user description: skip */
      }
    }
    if (!endpoints["prov-session"] || !endpoints["prov-config"]) {
      throw new Error(
        "未发现 prov-session/prov-config 端点；确认固件已进入配网模式并 pin 了服务 UUID。"
      );
    }
    log("info", `发现端点：${Object.keys(endpoints).join(", ")}`);

    return {
      device,
      disconnect() {
        if (device.gatt.connected) device.gatt.disconnect();
      },
      async request(name, payload) {
        const ch = endpoints[name];
        if (!ch) throw new Error("unknown endpoint " + name);
        await ch.writeValueWithResponse(payload);
        const resp = await ch.readValue();
        return new Uint8Array(resp.buffer);
      },
    };
  }

  // ---- full provisioning flow --------------------------------------------
  async function runProvisioning(opts, log) {
    log = log || (() => {});
    const transport = await connectBleTransport(opts.namePrefix, log);
    try {
      log("info", "建立加密会话 (Security1)…");
      const session = new Security1Session(transport, opts.pop);
      await session.establish();
      log("ok", "加密会话已建立");

      log("info", `下发 Wi-Fi 凭据：${opts.ssid}`);
      const setResp = decodeRespStatus(
        await session.secureRequest("prov-config", encodeSetConfig(opts.ssid, opts.password))
      );
      if (setResp.status !== 0) throw new Error("SetConfig 失败 status=" + setResp.status);

      const applyResp = decodeRespStatus(
        await session.secureRequest("prov-config", encodeApplyConfig())
      );
      if (applyResp.status !== 0) throw new Error("ApplyConfig 失败 status=" + applyResp.status);
      log("info", "已下发，等待设备连接 Wi-Fi…");

      // Poll status until connected or failed (up to ~30s).
      for (let attempt = 0; attempt < 15; attempt++) {
        await new Promise((r) => setTimeout(r, 2000));
        const status = decodeRespGetStatus(
          await session.secureRequest("prov-config", encodeGetStatus())
        );
        if (status.staState === StaState.Connected) {
          const where = status.connected
            ? `${status.connected.ssid} @ ${status.connected.ip}`
            : "";
          log("ok", `设备已联网 ${where}`);
          return { connected: true, detail: status.connected };
        }
        if (status.staState === StaState.Failed) {
          log("error", "设备连接失败（检查 SSID/密码/2.4GHz）");
          return { connected: false, failReason: status.failReason };
        }
        log("info", `连接中…(${attempt + 1})`);
      }
      log("error", "等待超时，未确认联网");
      return { connected: false, timeout: true };
    } finally {
      transport.disconnect();
    }
  }

  // ---- console tab glue (browser only) -----------------------------------
  function initProvisioningUI() {
    if (typeof document === "undefined") return;
    const form = document.getElementById("prov-form");
    if (!form) return;
    const logEl = document.getElementById("prov-log");
    const btn = document.getElementById("prov-start");

    function log(level, message) {
      if (!logEl) return;
      const line = document.createElement("div");
      line.className = "prov-log-line prov-" + level;
      const ts = new Date().toLocaleTimeString();
      line.textContent = `[${ts}] ${message}`;
      logEl.appendChild(line);
      logEl.scrollTop = logEl.scrollHeight;
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const ssid = (document.getElementById("prov-ssid").value || "").trim();
      const password = document.getElementById("prov-password").value || "";
      const pop = (document.getElementById("prov-pop").value || "").trim();
      const namePrefix = (document.getElementById("prov-name-prefix").value || "PROV_").trim();
      if (!ssid) {
        log("error", "请输入 Wi-Fi 名称 (SSID)");
        return;
      }
      if (btn) btn.disabled = true;
      try {
        await runProvisioning({ ssid, password, pop, namePrefix }, log);
      } catch (error) {
        log("error", error && error.message ? error.message : String(error));
      } finally {
        if (btn) btn.disabled = false;
      }
    });
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", initProvisioningUI);
    } else {
      initProvisioningUI();
    }
  }

  return {
    PROV_SERVICE_UUID,
    // crypto / encoding (exported for unit tests)
    x25519,
    x25519Base,
    bytesToHex,
    hexToBytes,
    CtrCipher,
    sha256,
    // protobuf codecs
    PBWriter,
    decodeMessage,
    encodeSessionCmd0,
    encodeSessionCmd1,
    decodeSec1Payload,
    encodeSetConfig,
    encodeApplyConfig,
    encodeGetStatus,
    decodeRespStatus,
    decodeRespGetStatus,
    // flow
    Security1Session,
    runProvisioning,
    initProvisioningUI,
  };
});
