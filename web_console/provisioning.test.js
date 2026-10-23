/*
 * Off-hardware unit tests for provisioning.js (run: node web_console/provisioning.test.js).
 * Covers the parts that do NOT need a real ESP32: X25519, AES-256-CTR
 * (NIST KAT + cross-message counter continuity), protobuf round-trips, and the
 * full Security1 handshake + encrypted config exchange against a simulated
 * device that mirrors the firmware's algorithm. The BLE transport itself is the
 * only untested layer (hardware-gated).
 */
const P = require("./provisioning.js");

let failures = 0;
function check(name, cond) {
  if (cond) {
    console.log("  ok  -", name);
  } else {
    console.error("FAIL  -", name);
    failures++;
  }
}
const hex = P.bytesToHex;

async function testX25519() {
  // RFC 7748 §5.2 test vector.
  const scalar = P.hexToBytes(
    "a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4"
  );
  const u = P.hexToBytes(
    "e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c"
  );
  const out = P.x25519(scalar, u);
  check(
    "X25519 RFC7748 vector",
    hex(out) === "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552"
  );
}

async function testCtr() {
  // NIST SP800-38A AES-256-CTR, first block.
  const key = P.hexToBytes(
    "603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4"
  );
  const iv = P.hexToBytes("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff");
  const pt = P.hexToBytes("6bc1bee22e409f96e93d7e117393172a");
  const cipher = await P.CtrCipher.create(key, iv);
  const ct = await cipher.process(pt);
  check(
    "AES-256-CTR NIST KAT block 1",
    hex(ct) === "601ec313775789a5b7a7f504bbf3d228"
  );

  // Counter continuity: 30 bytes in one shot == 14 + 16 across two process()
  // calls (validates nc_off / partial-block carry like mbedtls).
  const data = new Uint8Array(30);
  for (let i = 0; i < 30; i++) data[i] = i * 7;
  const cA = await P.CtrCipher.create(key, iv);
  const whole = await cA.process(data);
  const cB = await P.CtrCipher.create(key, iv);
  const part1 = await cB.process(data.slice(0, 14));
  const part2 = await cB.process(data.slice(14));
  const joined = new Uint8Array(30);
  joined.set(part1, 0);
  joined.set(part2, 14);
  check("AES-CTR cross-call continuity (14+16==30)", hex(whole) === hex(joined));
}

function testProtobuf() {
  // SetConfig round-trips through our writer and a generic decode.
  const enc = P.encodeSetConfig("MyWiFi", "secretpass");
  const top = P.decodeMessage(enc);
  // WiFiConfigPayload: msg(1)=2, cmd_set_config(12)
  check("SetConfig msg type", top[1][0] === 2);
  const cmd = P.decodeMessage(top[12][0]);
  check("SetConfig ssid", new TextDecoder().decode(cmd[1][0]) === "MyWiFi");
  check("SetConfig passphrase", new TextDecoder().decode(cmd[2][0]) === "secretpass");

  // RespSetConfig { status=0 } wrapped in WiFiConfigPayload field 13.
  const respInner = new P.PBWriter().uint(1, 0).finish();
  const respMsg = new P.PBWriter().uint(1, 3).message(13, respInner).finish();
  check("decodeRespStatus", P.decodeRespStatus(respMsg).status === 0);

  // RespGetStatus connected: status=0, sta_state=0, connected{ip4_addr,ssid}.
  const connected = new P.PBWriter()
    .bytes(1, new TextEncoder().encode("192.168.1.50"))
    .bytes(4, new TextEncoder().encode("MyWiFi"))
    .finish();
  const respGet = new P.PBWriter().uint(1, 0).uint(2, 0).message(11, connected).finish();
  const wrapped = new P.PBWriter().uint(1, 1).message(11, respGet).finish();
  const status = P.decodeRespGetStatus(wrapped);
  check(
    "decodeRespGetStatus connected",
    status.staState === 0 &&
      status.connected.ip === "192.168.1.50" &&
      status.connected.ssid === "MyWiFi"
  );
}

// A JS stand-in for the device side of protocomm security1 + wifi_config, used
// to exercise the whole client handshake without hardware.
function makeFakeDevice(pop) {
  const devPriv = new Uint8Array(32);
  globalThis.crypto.getRandomValues(devPriv);
  const devPub = P.x25519Base(devPriv);
  const deviceRandom = new Uint8Array(16);
  globalThis.crypto.getRandomValues(deviceRandom);
  let cipher = null;
  let clientPub = null;

  async function deviceCipher(sharedKey) {
    return P.CtrCipher.create(sharedKey, deviceRandom);
  }

  return {
    async request(endpoint, payload) {
      if (endpoint === "prov-session") {
        const session = P.decodeMessage(payload);
        const sec1 = P.decodeMessage(session[11][0]);
        if (sec1[20]) {
          // Command0: client pubkey in sc0.field1
          clientPub = P.decodeMessage(sec1[20][0])[1][0];
          let shared = P.x25519(devPriv, clientPub);
          if (pop) {
            const digest = await P.sha256(new TextEncoder().encode(pop));
            const mixed = new Uint8Array(32);
            for (let i = 0; i < 32; i++) mixed[i] = shared[i] ^ digest[i];
            shared = mixed;
          }
          cipher = await deviceCipher(shared);
          // SessionResp0 { status=1->? Success=0; device_pubkey=2; device_random=3 }
          const resp0 = new P.PBWriter()
            .uint(1, 0)
            .bytes(2, devPub)
            .bytes(3, deviceRandom)
            .finish();
          const sec1Resp = new P.PBWriter().uint(1, 1).message(21, resp0).finish();
          return new P.PBWriter().uint(2, 1).message(11, sec1Resp).finish();
        }
        if (sec1[22]) {
          // Command1: client_verify_data in sc1.field1
          const clientVerify = P.decodeMessage(sec1[22][0])[1][0];
          const decrypted = await cipher.process(clientVerify); // == devPub
          if (hex(decrypted) !== hex(devPub)) throw new Error("device: client verify mismatch");
          const deviceVerify = await cipher.process(clientPub);
          const resp1 = new P.PBWriter().uint(1, 0).bytes(2, deviceVerify).finish();
          const sec1Resp = new P.PBWriter().uint(1, 3).message(23, resp1).finish();
          return new P.PBWriter().uint(2, 1).message(11, sec1Resp).finish();
        }
        throw new Error("device: unexpected session message");
      }
      if (endpoint === "prov-config") {
        const plain = await cipher.process(payload);
        const cfg = P.decodeMessage(plain);
        const msgType = cfg[1][0];
        let respPlain;
        if (msgType === 2) {
          // SetConfig -> RespSetConfig status=0 (field 13)
          const inner = new P.PBWriter().uint(1, 0).finish();
          respPlain = new P.PBWriter().uint(1, 3).message(13, inner).finish();
        } else if (msgType === 4) {
          // ApplyConfig -> RespApplyConfig status=0 (field 15)
          const inner = new P.PBWriter().uint(1, 0).finish();
          respPlain = new P.PBWriter().uint(1, 5).message(15, inner).finish();
        } else {
          // GetStatus -> connected
          const connected = new P.PBWriter()
            .bytes(1, new TextEncoder().encode("10.0.0.7"))
            .bytes(4, new TextEncoder().encode("MyWiFi"))
            .finish();
          const respGet = new P.PBWriter().uint(1, 0).uint(2, 0).message(11, connected).finish();
          respPlain = new P.PBWriter().uint(1, 1).message(11, respGet).finish();
        }
        return cipher.process(respPlain);
      }
      throw new Error("device: unknown endpoint " + endpoint);
    },
  };
}

async function testHandshake() {
  const pop = "vr-prov-1234";
  const device = makeFakeDevice(pop);
  const session = new P.Security1Session(device, pop);
  await session.establish();
  check("Security1 handshake establishes", session.cipher !== null);

  const setResp = P.decodeRespStatus(
    await session.secureRequest("prov-config", P.encodeSetConfig("MyWiFi", "secretpass"))
  );
  check("encrypted SetConfig round-trip", setResp.status === 0);

  const applyResp = P.decodeRespStatus(
    await session.secureRequest("prov-config", P.encodeApplyConfig())
  );
  check("encrypted ApplyConfig round-trip", applyResp.status === 0);

  const status = P.decodeRespGetStatus(
    await session.secureRequest("prov-config", P.encodeGetStatus())
  );
  check("encrypted GetStatus shows connected", status.connected && status.connected.ip === "10.0.0.7");

  // Wrong PoP must fail device verification.
  let rejected = false;
  try {
    const dev2 = makeFakeDevice("vr-prov-1234");
    const bad = new P.Security1Session(dev2, "wrong-pop");
    await bad.establish();
  } catch (_e) {
    rejected = true;
  }
  check("wrong PoP rejected", rejected);
}

(async () => {
  await testX25519();
  await testCtr();
  testProtobuf();
  await testHandshake();
  if (failures) {
    console.error(`\n${failures} check(s) FAILED`);
    process.exit(1);
  }
  console.log("\nall provisioning.js checks passed");
})();
