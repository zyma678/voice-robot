const state = {
  socket: null,
  authenticated: false,
  collectingAudio: false,
  lastReply: "--",
  selectedUser: null,
  activeTab: "vocab",
  chatRequest: null,
  wsStreamingReply: "",
  devicesTimer: null,
  monitorSource: null,
  monitorDeviceId: "",
  deviceVolume: 0,
  previousDeviceVolume: 85,
  deviceConversationOn: false,
  metrics: {
    days: 7,
    groupBy: "user_id,scenario,vendor",
  },
  memorySection: "profile",
  selectedMemoryItemId: null,
  // 生字管理：录入的字列表 + 每个字的字形可用性缓存（available: true/false/undefined=未知）
  // glyphs: 每个字的描红字形 payload（按需缓存，用户触发描红后保留）。
  // explanations: 每个字预生成的「组词 + 拆解写法解读」（{word, explanation, source}），
  // 由 /api/vocab/explanation 提前生成、可在网格内编辑；下发时作为 TTS 文案随描红一起发到真机。
  // generating: 每个字解读生成中的标志，用于禁用按钮 / 显示状态。
  vocab: {
    characters: [],
    groups: {},
    statuses: {},
    activeGroup: "",
    activeStatusFilter: "all",
    promptTemplate: "",
    availability: {},
    glyphs: {},
    explanations: {},
    generating: {},
    dispatching: false,
    serverLoaded: false,
  },
  // 成语学习：录入的成语列表（按分组）+ 每条成语的解读/故事内容（contents）。
  // glyphs: 每个成语单字的楷体字形 payload（按需缓存，用于大字展示与打印）。
  // contents: 每条成语预生成的 {pinyin, explanation, story, hasExplanationTts, hasStoryTts}，
  // 由 /api/idiom/content 提前生成、可在卡片内编辑；下发时作为 TTS 文案随大字一起发到真机。
  idiom: {
    idioms: [],
    groups: {},
    statuses: {},
    activeGroup: "",
    activeStatusFilter: "all",
    explanationTemplate: "",
    storyTemplate: "",
    glyphs: {},
    contents: {},
    generating: {},
    dispatching: false,
    serverLoaded: false,
  },
  memoryPayloads: {
    profile: null,
    botProfile: null,
    turns: [],
    vocabulary: { entries: [], history: [] },
    summaries: [],
    entries: [],
    audit: [],
  },
  scenes: [],
  llmSettings: {
    configured: false,
    model: "",
    modelSource: "unset",
    baseUrl: "",
    baseUrlSource: "unset",
    apiKeySource: "unset",
    systemPrompt: "",
    systemPromptSource: "unset",
    userPromptTemplate: "",
    userPromptSource: "unset",
    masked: "",
    revealed: "",
    isVisible: false,
    isEditing: false,
  },
  speechSettings: {
    configured: false,
    asrWsUrl: "",
    asrWsUrlSource: "unset",
    asrResourceId: "",
    asrResourceIdSource: "unset",
    asrSampleRateHz: 16000,
    asrApiKeyMasked: "",
    asrApiKeyRevealed: "",
    ttsWsUrl: "",
    ttsWsUrlSource: "unset",
    ttsResourceId: "",
    ttsResourceIdSource: "unset",
    ttsVoiceType: "",
    ttsVoiceTypeSource: "unset",
    ttsEncoding: "",
    ttsSampleRateHz: 16000,
    ttsTokenMasked: "",
    ttsTokenRevealed: "",
    isVisible: false,
    isEditing: false,
  },
  speechRtc: {
    running: false,
    micState: "idle",
    vadState: "idle",
    playbackState: "idle",
    wsState: "disconnected",
    captions: "",
    audioContext: null,
    mediaStream: null,
    mediaRecorder: null,
    sourceNode: null,
    processorNode: null,
    playbackQueue: [],
    playbackTime: 0,
    playbackSources: [],
    vadActive: false,
    vadLastVoiceAt: 0,
    vadLastSendAt: 0,
    vadSilenceMs: 700,
    vadThreshold: 0.02,
    pcmChunks: [],
    preRollChunks: [],
    lastBargeInAt: 0,
    lastBargeInLatencyMs: null,
    replayBubble: null,
  },
  conversation: {
    currentAssistantBubble: null,
    streamedText: "",
    logCollapsed: false,
    turnNo: 0,
  },
  gateway: {
    bleState: "disconnected",
    cloudState: "disconnected",
    bleDevice: null,
    bleServer: null,
    controlCharacteristic: null,
    statusCharacteristic: null,
    statusDecoder: null,
    cloudSocket: null,
    cloudAuthenticated: false,
    pendingAuth: false,
    lastStatusText: "--",
    volume: 0,
  },
};

const BLE_GATEWAY_SERVICE_UUID = "6f4d5a9e-7d76-6f69-6365-626f74000001";
const BLE_GATEWAY_CONTROL_UUID = "6f4d5a9e-7d76-6f69-6365-626f74000002";
const BLE_GATEWAY_STATUS_UUID = "6f4d5a9e-7d76-6f69-6365-626f74000003";
const VOCAB_DEFAULT_GROUP_NAME = "一年级（下）生字";
const VOCAB_GROUPS_STORAGE_KEY = "voice-robot-vocab-groups";
const VOCAB_STATUS_LEARNED = "learned";
const VOCAB_STATUS_UNLEARNED = "unlearned";
const VOCAB_STATUS_NEW = "new";
const VOCAB_LEARNED_COLOR = "#f2c94c";
const VOCAB_UNLEARNED_COLOR = "#ffffff";
const VOCAB_NEW_COLOR = "#b07aed";
const IDIOM_DEFAULT_GROUP_NAME = "成语 · 第一批";
const IDIOM_GROUPS_STORAGE_KEY = "voice-robot-idiom-groups";

function qs(selector) {
  return document.querySelector(selector);
}

function qsa(selector) {
  return Array.from(document.querySelectorAll(selector));
}

const THEME_KEY = "voice-robot-theme";

function applyTheme(theme) {
  const normalized = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", normalized);
  const toggle = qs("#theme-toggle");
  if (toggle) {
    toggle.textContent = normalized === "light" ? "暗色" : "浅色";
    toggle.title = normalized === "light" ? "切换到暗色主题" : "切换到亮色主题";
  }
}

function initTheme() {
  let stored = null;
  try {
    stored = localStorage.getItem(THEME_KEY);
  } catch (error) {
    stored = null;
  }
  applyTheme(stored || "dark");
}

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme");
  const next = current === "light" ? "dark" : "light";
  applyTheme(next);
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch (error) {
    // ignore persistence failures (e.g. private mode)
  }
}

function showToast(message, kind = "info", timeout = 3200) {
  const stack = qs("#toast-stack");
  if (!stack || !message) {
    return;
  }
  const toast = document.createElement("div");
  toast.className = `toast ${kind}`;
  toast.textContent = message;
  stack.append(toast);
  const remove = () => {
    toast.classList.add("is-leaving");
    setTimeout(() => toast.remove(), 200);
  };
  setTimeout(remove, timeout);
  toast.addEventListener("click", remove);
}

function setButtonLoading(selector, loading) {
  const button = qs(selector);
  if (button) {
    button.classList.toggle("is-loading", Boolean(loading));
    button.disabled = Boolean(loading);
  }
}

function setText(selector, value) {
  const element = qs(selector);
  if (element) {
    element.textContent = value;
  }
}

function setBanner(selector, message) {
  const element = qs(selector);
  if (!element) {
    return;
  }
  if (!message) {
    element.hidden = true;
    element.textContent = "";
    return;
  }
  element.hidden = false;
  element.textContent = message;
}

function appendGatewayLog(kind, payload) {
  const container = qs("#gateway-log");
  if (!container) {
    return;
  }
  const item = document.createElement("div");
  item.className = `log-entry ${kind}`;
  if (payload !== null && typeof payload === "object") {
    item.append(buildJsonTree(payload, true));
  } else {
    item.textContent = String(payload);
  }
  container.prepend(item);
}

function appendLog(kind, payload) {
  const container = qs("#message-log");
  if (!container) {
    return;
  }
  const item = document.createElement("div");
  item.className = `log-entry ${kind}`;
  if (payload !== null && typeof payload === "object") {
    item.append(buildJsonTree(payload, true));
  } else {
    item.textContent = String(payload);
  }
  container.prepend(item);
}

function gatewayWsUrl() {
  return websocketUrl();
}

function gatewayDeviceId() {
  const input = qs("#gateway-device-id");
  return (input && input.value.trim()) || "esp32-001";
}

function gatewayId() {
  const input = qs("#gateway-id");
  return (input && input.value.trim()) || "web-gateway-local";
}

function gatewayToken() {
  return resolveAuthToken("#gateway-token");
}

function updateGatewayUi() {
  const gateway = state.gateway;
  setText("#gateway-ble-state", gateway.bleState);
  setText("#gateway-cloud-state", gateway.cloudState);
  setText("#gateway-last-status", gateway.lastStatusText || "--");
  setText("#gateway-target-name", `VR-${gatewayDeviceId()}`);
  setText(
    "#gateway-cloud-note",
    gateway.cloudAuthenticated ? `gateway_id=${gatewayId()}` : "等待连接"
  );
  setText("#gateway-volume-value", String(gateway.volume));
  const slider = qs("#gateway-volume-slider");
  if (slider && slider.value !== String(gateway.volume)) {
    slider.value = String(gateway.volume);
  }
  const bleConnected = Boolean(gateway.controlCharacteristic && gateway.bleServer);
  const cloudOpen =
    Boolean(gateway.cloudSocket) &&
    gateway.cloudSocket.readyState === WebSocket.OPEN &&
    gateway.cloudAuthenticated;
  const bleButton = qs("#gateway-connect-ble");
  const cloudButton = qs("#gateway-connect-cloud");
  const volumeButton = qs("#gateway-send-volume");
  if (bleButton) {
    bleButton.textContent = bleConnected ? "BLE 已连接" : "连接 BLE";
  }
  if (cloudButton) {
    cloudButton.textContent = cloudOpen ? "云端已连接" : "连接云端";
  }
  if (volumeButton) {
    volumeButton.disabled = !bleConnected;
  }
}

function updateGatewayState(patch = {}) {
  Object.assign(state.gateway, patch);
  updateGatewayUi();
}

function decodeGatewayStatus(dataView) {
  const gateway = state.gateway;
  if (!gateway.statusDecoder) {
    gateway.statusDecoder = new TextDecoder("utf-8");
  }
  try {
    const text = gateway.statusDecoder.decode(dataView.buffer.slice(
      dataView.byteOffset,
      dataView.byteOffset + dataView.byteLength
    ));
    gateway.lastStatusText = text || "--";
    appendGatewayLog("incoming", text || "--");
    try {
      const payload = JSON.parse(text);
      if (payload.type === "volume" && Number.isFinite(Number(payload.volume))) {
        gateway.volume = Math.max(0, Math.min(100, Number(payload.volume)));
      }
    } catch (error) {
      // keep raw text only
    }
    updateGatewayUi();
  } catch (error) {
    appendGatewayLog("error", `gateway status decode failed: ${error.message}`);
  }
}

async function ensureGatewayStatusRead() {
  const characteristic = state.gateway.statusCharacteristic;
  if (!characteristic || typeof characteristic.readValue !== "function") {
    return;
  }
  try {
    const value = await characteristic.readValue();
    decodeGatewayStatus(value);
  } catch (error) {
    appendGatewayLog("error", `gateway status read failed: ${error.message}`);
  }
}

async function connectGatewayBle() {
  if (!navigator.bluetooth || typeof navigator.bluetooth.requestDevice !== "function") {
    const message = "当前浏览器不支持 Web Bluetooth，请使用 Chrome 并通过 localhost / HTTPS 打开。";
    setBanner("#gateway-banner", message);
    appendGatewayLog("error", message);
    showToast(message, "error");
    return;
  }
  setBanner("#gateway-banner", "");
  updateGatewayState({ bleState: "connecting" });
  try {
    const expectedName = `VR-${gatewayDeviceId()}`;
    const device = await navigator.bluetooth.requestDevice({
      filters: [{ namePrefix: "VR-" }],
      optionalServices: [BLE_GATEWAY_SERVICE_UUID],
    });
    const server = await device.gatt.connect();
    const service = await server.getPrimaryService(BLE_GATEWAY_SERVICE_UUID);
    const controlCharacteristic = await service.getCharacteristic(
      BLE_GATEWAY_CONTROL_UUID
    );
    const statusCharacteristic = await service.getCharacteristic(
      BLE_GATEWAY_STATUS_UUID
    );
    device.addEventListener("gattserverdisconnected", () => {
      appendGatewayLog("error", "BLE disconnected");
      updateGatewayState({
        bleState: "disconnected",
        bleDevice: null,
        bleServer: null,
        controlCharacteristic: null,
        statusCharacteristic: null,
      });
    });
    if (typeof statusCharacteristic.startNotifications === "function") {
      await statusCharacteristic.startNotifications();
      statusCharacteristic.addEventListener("characteristicvaluechanged", (event) => {
        const value = event?.target?.value;
        if (value) {
          decodeGatewayStatus(value);
        }
      });
    }
    updateGatewayState({
      bleState: "connected",
      bleDevice: device,
      bleServer: server,
      controlCharacteristic,
      statusCharacteristic,
    });
    appendGatewayLog("incoming", {
      type: "ble_connected",
      requested_name: expectedName,
      actual_name: device.name || "",
    });
    await ensureGatewayStatusRead();
    showToast(`BLE 已连接 ${device.name || expectedName}`, "success");
  } catch (error) {
    updateGatewayState({
      bleState: "error",
      bleDevice: null,
      bleServer: null,
      controlCharacteristic: null,
      statusCharacteristic: null,
    });
    setBanner("#gateway-banner", `BLE 连接失败：${error.message}`);
    appendGatewayLog("error", `BLE connect failed: ${error.message}`);
    showToast(`BLE 连接失败：${error.message}`, "error");
  }
}

function sendGatewayCloudAuth() {
  const socket = state.gateway.cloudSocket;
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return false;
  }
  const payload = {
    type: "auth",
    device_id: gatewayDeviceId(),
    token: gatewayToken(),
    transport: "ble_mobile_gateway",
    gateway_id: gatewayId(),
  };
  socket.send(JSON.stringify(payload));
  state.gateway.pendingAuth = true;
  appendGatewayLog("outgoing", payload);
  return true;
}

async function connectGatewayCloud() {
  const existing = state.gateway.cloudSocket;
  if (
    existing &&
    (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)
  ) {
    sendGatewayCloudAuth();
    return;
  }
  setBanner("#gateway-banner", "");
  updateGatewayState({ cloudState: "connecting", cloudAuthenticated: false });
  const socket = new WebSocket(gatewayWsUrl());
  state.gateway.cloudSocket = socket;
  socket.addEventListener("open", () => {
    updateGatewayState({ cloudState: "connected" });
    appendGatewayLog("incoming", "gateway cloud socket connected");
    sendGatewayCloudAuth();
  });
  socket.addEventListener("close", (event) => {
    appendGatewayLog("error", `gateway cloud disconnected (${event.code})`);
    if (state.gateway.cloudSocket === socket) {
      state.gateway.cloudSocket = null;
    }
    updateGatewayState({
      cloudState: "disconnected",
      cloudAuthenticated: false,
      pendingAuth: false,
    });
  });
  socket.addEventListener("error", () => {
    setBanner("#gateway-banner", "网关云端连接失败，请确认服务已启动。");
    appendGatewayLog("error", "gateway cloud websocket error");
    updateGatewayState({
      cloudState: "error",
      cloudAuthenticated: false,
      pendingAuth: false,
    });
  });
  socket.addEventListener("message", async (event) => {
    if (typeof event.data !== "string") {
      return;
    }
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch (error) {
      appendGatewayLog("error", `gateway invalid JSON: ${event.data}`);
      return;
    }
    appendGatewayLog("incoming", payload);
    if (payload.type === "auth_ok") {
      updateGatewayState({
        cloudState: "connected",
        cloudAuthenticated: true,
        pendingAuth: false,
      });
      showToast("手机网关云端鉴权成功", "success");
      return;
    }
    if (payload.type === "error") {
      updateGatewayState({
        cloudAuthenticated: false,
        pendingAuth: false,
      });
      setBanner("#gateway-banner", payload.message || "云端返回错误");
      return;
    }
    if (payload.type === "set_volume") {
      const volume = Math.max(0, Math.min(100, Number(payload.volume) || 0));
      state.gateway.volume = volume;
      updateGatewayUi();
      try {
        await sendGatewayControlCommand({ type: "set_volume", volume });
      } catch (error) {
        setBanner("#gateway-banner", `云端 set_volume 转发 BLE 失败：${error.message}`);
        appendGatewayLog("error", `forward set_volume failed: ${error.message}`);
      }
      return;
    }
    if (payload.type === "say") {
      appendGatewayLog(
        "incoming",
        "当前原型阶段收到 say 命令仅记录日志，完整语音链路留到下一阶段。"
      );
    }
  });
}

async function sendGatewayControlCommand(command) {
  const characteristic = state.gateway.controlCharacteristic;
  if (!characteristic || typeof characteristic.writeValue !== "function") {
    throw new Error("BLE control characteristic not ready");
  }
  const bytes = new TextEncoder().encode(JSON.stringify(command));
  await characteristic.writeValue(bytes);
  appendGatewayLog("outgoing", command);
}

async function sendGatewayVolume() {
  const volume = Math.max(0, Math.min(100, Number(state.gateway.volume) || 0));
  state.gateway.volume = volume;
  updateGatewayUi();
  try {
    await sendGatewayControlCommand({ type: "set_volume", volume });
    showToast(`BLE 音量命令已发送：${volume}`, "success");
  } catch (error) {
    setBanner("#gateway-banner", `发送 BLE 音量失败：${error.message}`);
    appendGatewayLog("error", `gateway volume failed: ${error.message}`);
    showToast(`发送 BLE 音量失败：${error.message}`, "error");
  }
}

async function dispatchGatewayColorTest(color) {
  const deviceInput = qs("#gateway-device-id");
  const deviceId = (deviceInput && deviceInput.value.trim()) || selectedDeviceId();
  const normalized = color || "bars";
  if (!deviceId) {
    showToast("没有可下发颜色自检的在线硬件设备", "error");
    return;
  }
  try {
    await sendJson(`/api/devices/${encodeURIComponent(deviceId)}/color-test`, "POST", {
      color: normalized,
    });
    appendGatewayLog("info", `color test dispatched: ${normalized}`);
    showToast(`已下发颜色自检：${normalized}`, "success");
  } catch (error) {
    setBanner("#gateway-banner", `颜色自检下发失败：${error.message}`);
    appendGatewayLog("error", `color test failed: ${error.message}`);
    showToast(`颜色自检失败：${error.message}`, "error");
  }
}

// ===== JSON 可折叠树渲染 =====
function jsonValuePreview(value) {
  if (Array.isArray(value)) {
    return `Array(${value.length})`;
  }
  return `Object(${Object.keys(value).length})`;
}

function buildJsonLeaf(value) {
  const span = document.createElement("span");
  let cls = "json-value";
  if (typeof value === "string") {
    cls += " json-string";
    span.textContent = JSON.stringify(value);
  } else if (typeof value === "number") {
    cls += " json-number";
    span.textContent = String(value);
  } else if (typeof value === "boolean") {
    cls += " json-boolean";
    span.textContent = String(value);
  } else if (value === null) {
    cls += " json-null";
    span.textContent = "null";
  } else {
    span.textContent = String(value);
  }
  span.className = cls;
  return span;
}

function buildJsonNode(keyLabel, value, expanded = false) {
  const isContainer = value !== null && typeof value === "object";
  const node = document.createElement("div");
  node.className = "json-node";

  const line = document.createElement("div");
  line.className = "json-line";

  if (isContainer) {
    const toggle = document.createElement("span");
    toggle.className = "json-toggle";
    toggle.textContent = expanded ? "▾" : "▸";
    line.append(toggle);
    line.classList.add("json-collapsible");
  } else {
    const spacer = document.createElement("span");
    spacer.className = "json-toggle json-toggle-leaf";
    line.append(spacer);
  }

  if (keyLabel !== null) {
    const key = document.createElement("span");
    key.className = "json-key";
    key.textContent = `${keyLabel}: `;
    line.append(key);
  }

  if (isContainer) {
    const preview = document.createElement("span");
    preview.className = "json-preview";
    preview.textContent = jsonValuePreview(value);
    line.append(preview);

    const children = document.createElement("div");
    children.className = "json-children";
    children.hidden = !expanded;
    const entries = Array.isArray(value)
      ? value.map((item, index) => [index, item])
      : Object.entries(value);
    entries.forEach(([childKey, childValue]) => {
      children.append(buildJsonNode(childKey, childValue, false));
    });

    line.addEventListener("click", (event) => {
      event.stopPropagation();
      const collapsed = children.hidden;
      children.hidden = !collapsed;
      line.querySelector(".json-toggle").textContent = collapsed ? "▾" : "▸";
    });

    node.append(line, children);
  } else {
    line.append(buildJsonLeaf(value));
    node.append(line);
  }
  return node;
}

function buildJsonTree(payload, rootExpanded = true) {
  const wrapper = document.createElement("div");
  wrapper.className = "json-tree";
  wrapper.append(buildJsonNode(null, payload, rootExpanded));
  return wrapper;
}

// ===== 结构化回合日志（明确轮次 / 输入prompt / 模型输出） =====
const LOG_EVENT_META = {
  prompt: { label: "输入 Prompt", kind: "log-prompt" },
  output: { label: "模型输出", kind: "log-output" },
  asr: { label: "语音识别", kind: "log-asr" },
  tts: { label: "语音合成", kind: "log-tts" },
  error: { label: "错误", kind: "log-error" },
  system: { label: "系统", kind: "log-system" },
};

function appendTurnLog(category, title, detail, { expanded = false } = {}) {
  const container = qs("#message-log");
  if (!container) {
    return;
  }
  const meta = LOG_EVENT_META[category] || LOG_EVENT_META.system;
  const entry = document.createElement("div");
  entry.className = `log-entry ${meta.kind}`;

  const head = document.createElement("div");
  head.className = "log-head";

  const turnBadge = document.createElement("span");
  turnBadge.className = "log-turn-badge";
  turnBadge.textContent =
    state.conversation.turnNo > 0 ? `第 ${state.conversation.turnNo} 轮` : "会话";
  head.append(turnBadge);

  const typeBadge = document.createElement("span");
  typeBadge.className = "log-type-badge";
  typeBadge.textContent = meta.label;
  head.append(typeBadge);

  const time = document.createElement("span");
  time.className = "log-time";
  time.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  head.append(time);

  if (title) {
    const titleEl = document.createElement("span");
    titleEl.className = "log-title";
    titleEl.textContent = title;
    head.append(titleEl);
  }

  entry.append(head);

  if (detail !== undefined && detail !== null && detail !== "") {
    const body = document.createElement("div");
    body.className = "log-body";
    if (typeof detail === "object") {
      body.append(buildJsonTree(detail, expanded));
    } else {
      body.textContent = String(detail);
    }
    entry.append(body);
  }

  container.prepend(entry);
}

// ===== Conversation: 气泡渲染 =====
function clearThreadEmptyHint() {
  const hint = qs("#thread-empty");
  if (hint) {
    hint.remove();
  }
}

function scrollThreadToBottom() {
  const thread = qs("#conversation-thread");
  if (thread) {
    thread.scrollTop = thread.scrollHeight;
  }
}

function formatBubbleTime(date) {
  const today = new Date();
  const sameDay =
    date.getFullYear() === today.getFullYear() &&
    date.getMonth() === today.getMonth() &&
    date.getDate() === today.getDate();
  const time = date.toLocaleTimeString("zh-CN", { hour12: false });
  if (sameDay) {
    return time;
  }
  const md = date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
  return `${md} ${time}`;
}

function appendBubbleTime(thread, role, createdAt) {
  const date = createdAt ? new Date(createdAt) : new Date();
  if (Number.isNaN(date.getTime())) {
    return;
  }
  const timeEl = document.createElement("div");
  timeEl.className = `bubble-time bubble-time-${role === "user" ? "user" : "assistant"}`;
  timeEl.textContent = formatBubbleTime(date);
  thread.append(timeEl);
}

function appendBubble({
  role,
  kind = "text",
  text = "",
  transcript = "",
  turnId = "",
  ttsUrl = "",
  ttsAudioFormat = "",
  ttsSampleRateHz = 0,
  createdAt = "",
}) {
  const thread = qs("#conversation-thread");
  if (!thread) {
    return null;
  }
  clearThreadEmptyHint();
  appendBubbleTime(thread, role, createdAt);
  const bubble = document.createElement("div");
  bubble.className = `bubble bubble-${role === "user" ? "user" : "assistant"} ${kind}`;
  const content = document.createElement("div");
  content.className = "bubble-content";
  let actions = null;
  if (role !== "user") {
    bubble.setAttribute("data-turn-id", turnId || "");
    bubble.setAttribute("data-tts-url", ttsUrl || "");
    bubble.setAttribute("data-tts-audio-format", ttsAudioFormat || "");
    bubble.setAttribute("data-tts-sample-rate-hz", String(ttsSampleRateHz || ""));
    actions = document.createElement("div");
    actions.className = "bubble-actions";
  }
  if (kind === "voice") {
    const badge = document.createElement("div");
    badge.className = "bubble-badge";
    badge.textContent = role === "user" ? "语音消息" : "语音回复";
    content.append(badge);
  }
  const body = document.createElement("div");
  body.className = "bubble-body";
  body.textContent = text;
  content.append(body);
  // Voice transcripts are shown as a sub-line beneath the bubble body.
  if (transcript) {
    const transcriptEl = document.createElement("div");
    transcriptEl.className = "bubble-transcript";
    transcriptEl.textContent = transcript;
    content.append(transcriptEl);
  }
  bubble.append(content);
  if (role !== "user") {
    actions.append(createTtsReplayButton(bubble));
    bubble.append(actions);
    if (ttsUrl) {
      bubble.classList.add("has-tts");
      setBubbleReplayState(bubble, "ready");
    }
  }
  thread.append(bubble);
  scrollThreadToBottom();
  return bubble;
}

function createTtsReplayButton(bubble) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "bubble-tts-button";
  button.hidden = true;
  button.disabled = true;
  button.setAttribute("data-tts-replay-button", "true");
  button.textContent = "重听";
  button.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    try {
      await replayAssistantTts(bubble, button);
    } catch (error) {
      setBanner("#conv-banner", `重听语音失败：${error.message}`);
      showToast(`重听语音失败：${error.message}`, "error");
      appendTurnLog("error", `重听语音失败：${error.message}`);
    }
  });
  return button;
}

function setBubbleReplayState(bubble, stateValue = "idle") {
  if (!bubble) {
    return;
  }
  const button = bubble.querySelector('[data-tts-replay-button="true"]');
  if (!button) {
    return;
  }
  bubble.classList.remove(
    "tts-ready",
    "tts-loading",
    "tts-playing",
    "tts-error",
    "is-playing"
  );
  bubble.classList.add(`tts-${stateValue}`);
  if (stateValue === "ready") {
    button.hidden = false;
    button.disabled = false;
    button.textContent = "重听";
    button.title = "播放这条回复的语音";
  } else if (stateValue === "loading") {
    button.hidden = false;
    button.disabled = true;
    button.textContent = "加载中...";
    button.title = "正在加载语音";
  } else if (stateValue === "playing") {
    button.hidden = false;
    button.disabled = false;
    button.textContent = "播放中...";
    button.title = "当前语音播放中";
  } else if (stateValue === "error") {
    button.hidden = false;
    button.disabled = false;
    button.textContent = "重试";
    button.title = "上次播放失败，点击重试";
  } else {
    button.hidden = true;
    button.disabled = true;
    button.textContent = "重听";
    button.title = "暂无可重听语音";
  }
}

function attachTtsToAssistantBubble(
  bubble,
  { turnId = "", ttsUrl = "", ttsAudioFormat = "", ttsSampleRateHz = 0 } = {}
) {
  if (!bubble) {
    return;
  }
  if (turnId) {
    bubble.setAttribute("data-turn-id", turnId);
  }
  if (ttsUrl) {
    bubble.setAttribute("data-tts-url", ttsUrl);
    bubble.classList.add("has-tts");
    setBubbleReplayState(bubble, "ready");
  }
  if (ttsAudioFormat) {
    bubble.setAttribute("data-tts-audio-format", ttsAudioFormat);
  }
  if (ttsSampleRateHz) {
    bubble.setAttribute("data-tts-sample-rate-hz", String(ttsSampleRateHz));
  }
}

async function ensurePlaybackAudioContext() {
  const rtc = state.speechRtc;
  if (!rtc.audioContext) {
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) {
      return null;
    }
    rtc.audioContext = new Ctor();
  }
  if (rtc.audioContext.state === "suspended") {
    try {
      await rtc.audioContext.resume();
    } catch (error) {
      // ignore — first user gesture may not have happened yet
    }
  }
  return rtc.audioContext;
}

function ensurePlaybackAudioContextSync() {
  const rtc = state.speechRtc;
  if (!rtc.audioContext) {
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) {
      return null;
    }
    rtc.audioContext = new Ctor();
  }
  if (rtc.audioContext.state === "suspended") {
    // Best-effort async resume; do not block the caller.
    rtc.audioContext.resume().catch(() => {});
  }
  return rtc.audioContext;
}

async function replayAssistantTts(bubble, button = null) {
  const ttsUrl = bubble ? bubble.getAttribute("data-tts-url") || "" : "";
  if (!ttsUrl) {
    setBanner("#conv-banner", "当前回复暂无可重听语音。");
    showToast("当前回复暂无可重听语音", "info");
    return;
  }
  try {
    setBubbleReplayState(bubble, "loading");
    const response = await fetch(ttsUrl);
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`${ttsUrl} -> ${response.status}${detail ? ` ${detail}` : ""}`);
    }
    const sampleRate =
      Number(
        bubble.getAttribute("data-tts-sample-rate-hz") ||
          response.headers.get("X-TTS-Sample-Rate-Hz") ||
          state.speechSettings.ttsSampleRateHz ||
          16000
      ) ||
      16000;
    await ensurePlaybackAudioContext();
    stopPlayback("idle");
    state.speechRtc.replayBubble = bubble;
    setBubbleReplayState(bubble, "playing");
    state.speechRtc.ttsReplaySampleRateHz = sampleRate;
    schedulePcmPlayback(await response.arrayBuffer(), {
      sampleRateHz: sampleRate,
      replayBubble: bubble,
    });
    appendTurnLog("tts", "重听历史 TTS", {
      turn_id: bubble.getAttribute("data-turn-id") || "",
      tts_url: ttsUrl,
      sample_rate_hz: sampleRate,
    });
  } catch (error) {
    setBubbleReplayState(bubble, "error");
    throw error;
  } finally {
    if (button && bubble.classList.contains("tts-loading")) {
      setBubbleReplayState(bubble, "ready");
    }
  }
}

function updateBubble(bubble, text, { streaming = false } = {}) {
  if (!bubble) {
    return;
  }
  const body = bubble.querySelector(".bubble-body");
  if (body) {
    body.textContent = text;
    body.classList.toggle("is-streaming", Boolean(streaming));
  }
  scrollThreadToBottom();
}

function appendUserBubble(text, kind = "text", createdAt = "") {
  return appendBubble({ role: "user", kind, text, createdAt });
}

function getOrCreateAssistantBubble(kind = "text") {
  if (!state.conversation.currentAssistantBubble) {
    state.conversation.currentAssistantBubble = appendBubble({
      role: "assistant",
      kind,
      text: "",
    });
    state.conversation.streamedText = "";
  }
  return state.conversation.currentAssistantBubble;
}

function setAssistantPlaying(playing) {
  const bubble = state.conversation.currentAssistantBubble;
  if (bubble) {
    setBubbleReplayState(bubble, playing ? "playing" : bubble.classList.contains("has-tts") ? "ready" : "idle");
  }
}

function convUserId() {
  const input = qs("#conv-user-id");
  return (input && input.value) || "default-user";
}

function safeNumber(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  return 0;
}

function formatMoney(value) {
  const amount = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return amount.toFixed(4);
}

function formatMetricNumber(value, maximumFractionDigits = 2) {
  const amount = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return amount.toLocaleString("zh-CN", {
    minimumFractionDigits: 0,
    maximumFractionDigits,
  });
}

function renderTokenMetricsTable(rows, groupByFields) {
  const container = qs("#metrics-table");
  if (!container) {
    return;
  }
  container.innerHTML = "";

  const header = document.createElement("div");
  header.className = "table-row header";
  const cols = [
    ...groupByFields,
    "calls",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
  ];
  cols.forEach((col) => {
    const cell = document.createElement("div");
    cell.className = "cell";
    cell.textContent = col;
    header.append(cell);
  });
  container.append(header);

  rows.slice(0, 200).forEach((row) => {
    const node = document.createElement("div");
    node.className = "table-row";
    cols.forEach((col) => {
      const cell = document.createElement("div");
      cell.className = "cell";
      // data-label 用于窄屏卡片化时显示「字段名：值」
      cell.dataset.label = col;
      cell.textContent =
        row[col] === undefined || row[col] === null ? "" : String(row[col]);
      node.append(cell);
    });
    container.append(node);
  });
}

async function loadTokenMetrics() {
  setBanner("#metrics-banner", "");
  const daysInput = qs("#metrics-days");
  const groupInput = qs("#metrics-group-by");
  const inPriceInput = qs("#metrics-in-price");
  const outPriceInput = qs("#metrics-out-price");
  const asrPriceInput = qs("#metrics-asr-hour-price");
  const ttsPriceInput = qs("#metrics-tts-10k-price");
  const budgetInput = qs("#metrics-budget");
  const days = Number((daysInput && daysInput.value) || "7");
  const groupBy = ((groupInput && groupInput.value) || "").trim();
  const params = new URLSearchParams();
  if (groupBy) {
    params.set("group_by", groupBy);
  }
  params.set("days", String(days >= 1 ? days : 7));

  const data = await fetchJson(`/api/metrics/token-usage?${params.toString()}`);
  let interactions = {};
  let interactionMetricsAvailable = true;
  try {
    interactions = await fetchJson(
      `/api/metrics/interactions?days=${String(days >= 1 ? days : 7)}`
    );
  } catch (error) {
    interactionMetricsAvailable = false;
    interactions = {};
    setBanner(
      "#metrics-banner",
      `语音成本指标加载失败，ASR/TTS 费用不可用：${error.message}`
    );
  }
  const total = data.total || {};
  const rows = Array.isArray(data.rows) ? data.rows : [];
  const groupByFields = Array.isArray(data.group_by) ? data.group_by : [];
  const billedModels = Array.isArray(data.models)
    ? data.models.filter((model) => typeof model === "string" && model.trim())
    : [];

  const calls =
    typeof total.provider_calls === "number"
      ? safeNumber(total.provider_calls)
      : safeNumber(total.calls);
  const successfulCalls =
    typeof total.provider_successful_calls === "number"
      ? safeNumber(total.provider_successful_calls)
      : typeof total.successful_calls === "number"
      ? safeNumber(total.successful_calls)
      : calls;
  const failedCalls =
    typeof total.provider_failed_calls === "number"
      ? safeNumber(total.provider_failed_calls)
      : safeNumber(total.failed_calls);
  const successRate =
    typeof total.provider_success_rate === "number"
      ? safeNumber(total.provider_success_rate)
      : typeof total.success_rate === "number"
      ? safeNumber(total.success_rate)
      : calls > 0
      ? (successfulCalls / calls) * 100
      : 0;
  const latencySamples =
    typeof total.provider_latency_samples === "number"
      ? safeNumber(total.provider_latency_samples)
      : safeNumber(total.latency_samples);
  const avgLatencyMs =
    typeof total.provider_avg_latency_ms === "number"
      ? safeNumber(total.provider_avg_latency_ms)
      : safeNumber(total.avg_latency_ms);
  const p95LatencyMs =
    typeof total.provider_p95_latency_ms === "number"
      ? safeNumber(total.provider_p95_latency_ms)
      : safeNumber(total.p95_latency_ms);
  const promptTokens = safeNumber(total.prompt_tokens);
  const completionTokens = safeNumber(total.completion_tokens);
  const totalTokens = safeNumber(total.total_tokens);
  const avgPromptTokens =
    typeof total.avg_prompt_tokens === "number"
      ? safeNumber(total.avg_prompt_tokens)
      : calls > 0
      ? promptTokens / calls
      : 0;

  const inPrice = Number((inPriceInput && inPriceInput.value) || "0.2");
  const outPrice = Number((outPriceInput && outPriceInput.value) || "2");
  const asrHourPrice = Number((asrPriceInput && asrPriceInput.value) || "4.5");
  const tts10kPrice = Number((ttsPriceInput && ttsPriceInput.value) || "5");
  const budget = Number((budgetInput && budgetInput.value) || "0");

  const safeInPrice = Number.isFinite(inPrice) ? inPrice : 0;
  const safeOutPrice = Number.isFinite(outPrice) ? outPrice : 0;
  const safeAsrPrice = Number.isFinite(asrHourPrice) ? asrHourPrice : 0;
  const safeTtsPrice = Number.isFinite(tts10kPrice) ? tts10kPrice : 0;
  const asrMetrics = interactions.asr || {};
  const ttsMetrics = interactions.tts || {};
  const asrSeconds = safeNumber(asrMetrics.billable_audio_seconds);
  const ttsBillableChars = safeNumber(ttsMetrics.billable_chars);
  const ttsCacheSavedChars = safeNumber(ttsMetrics.cache_saved_chars);
  const ttsCacheRate = safeNumber(ttsMetrics.cache_hit_rate);

  const llmCostEstimable = billedModels.length <= 1;
  const estimatedLlmCost = llmCostEstimable
    ? (promptTokens / 1_000_000) * safeInPrice +
      (completionTokens / 1_000_000) * safeOutPrice
    : NaN;
  const estimatedAsrCost = (asrSeconds / 3600) * safeAsrPrice;
  const estimatedTtsCost = (ttsBillableChars / 10_000) * safeTtsPrice;
  const estimatedTtsSaving = (ttsCacheSavedChars / 10_000) * safeTtsPrice;
  const estimatedCost = interactionMetricsAvailable && llmCostEstimable
    ? estimatedLlmCost + estimatedAsrCost + estimatedTtsCost
    : NaN;
  const remainingBudget =
    Number.isFinite(budget) && budget > 0 ? budget - estimatedCost : NaN;

  setText("#metrics-total-calls", String(calls));
  setText(
    "#metrics-models-note",
    billedModels.length > 0
      ? `统计期间模型：${billedModels.join("、")}${
          billedModels.length > 1
            ? "（多模型不能套用单一价格，费用显示为 --）"
            : ""
        }`
      : "统计期间模型：暂无 Ark 调用"
  );
  const skippedVoiceEvents =
    safeNumber(asrMetrics.legacy_events_skipped) +
    safeNumber(ttsMetrics.legacy_events_skipped);
  setText(
    "#metrics-voice-coverage",
    interactionMetricsAvailable
      ? `语音指标：新版样本 ${safeNumber(asrMetrics.calls) + safeNumber(ttsMetrics.calls)} 条，已忽略旧版 ${skippedVoiceEvents} 条`
      : "语音指标：不可用（未按 0 计算）"
  );
  setText(
    "#metrics-success-rate",
    calls > 0 ? `${formatMetricNumber(successRate, 1)}%` : "--"
  );
  setText(
    "#metrics-success-breakdown",
    calls > 0 ? `成功 ${successfulCalls} / 失败 ${failedCalls}` : "暂无调用"
  );
  setText(
    "#metrics-avg-latency",
    latencySamples > 0 ? `${formatMetricNumber(avgLatencyMs)} ms` : "--"
  );
  setText(
    "#metrics-p95-latency",
    latencySamples > 0 ? `${formatMetricNumber(p95LatencyMs)} ms` : "--"
  );
  setText("#metrics-avg-prompt-tokens", formatMetricNumber(avgPromptTokens));
  setText("#metrics-total-prompt", String(promptTokens));
  setText("#metrics-total-completion", String(completionTokens));
  setText("#metrics-total-tokens", String(totalTokens));
  setText(
    "#metrics-llm-cost",
    Number.isFinite(estimatedLlmCost) ? formatMoney(estimatedLlmCost) : "--"
  );
  setText(
    "#metrics-asr-seconds",
    interactionMetricsAvailable ? `${formatMetricNumber(asrSeconds)} s` : "--"
  );
  setText(
    "#metrics-tts-billable-chars",
    interactionMetricsAvailable ? formatMetricNumber(ttsBillableChars) : "--"
  );
  setText(
    "#metrics-tts-cache-rate",
    interactionMetricsAvailable && safeNumber(ttsMetrics.playback_calls) > 0
      ? `${formatMetricNumber(ttsCacheRate, 1)}%`
      : "--"
  );
  setText(
    "#metrics-tts-cache-saving",
    interactionMetricsAvailable ? formatMoney(estimatedTtsSaving) : "--"
  );
  setText(
    "#metrics-total-cost",
    Number.isFinite(estimatedCost) ? formatMoney(estimatedCost) : "--"
  );
  setText(
    "#metrics-remaining-budget",
    Number.isFinite(remainingBudget) ? formatMoney(remainingBudget) : "--"
  );
  setText(
    "#metrics-table-meta",
    `rows=${rows.length} | group_by=${groupByFields.join(",") || "(none)"}`
  );
  const successCard = qs("#metrics-success-card");
  if (successCard) {
    successCard.classList.toggle("is-ok", calls > 0 && successRate >= 95);
    successCard.classList.toggle(
      "is-warn",
      calls > 0 && successRate >= 80 && successRate < 95
    );
    successCard.classList.toggle("is-error", calls > 0 && successRate < 80);
  }
  renderTokenMetricsTable(rows, groupByFields);
  loadMetricsCharts(days);
}

async function loadMetricsCharts(days) {
  try {
    const data = await fetchJson(`/api/metrics/token-usage/daily?days=${days}`);
    renderLineChart("chart-daily-calls", data.daily || [], "calls");
    renderLineChart("chart-daily-tokens", data.daily || [], "total_tokens");
    renderPieChart("chart-scenario-calls", data.by_scenario || [], "calls");
    renderPieChart("chart-scenario-tokens", data.by_scenario || [], "total_tokens");
  } catch (_) {}
}

const CHART_COLORS = [
  "#58a6ff", "#f0883e", "#56d364", "#d2a8ff",
  "#ff7b72", "#79c0ff", "#ffa657", "#7ee787",
];

function getChartTooltip() {
  let el = qs("#chart-tooltip");
  if (!el) {
    el = document.createElement("div");
    el.id = "chart-tooltip";
    el.className = "chart-tooltip";
    document.body.appendChild(el);
  }
  return el;
}

function showChartTooltip(canvas, e, html) {
  const tip = getChartTooltip();
  tip.innerHTML = html;
  tip.classList.add("visible");
  const rect = canvas.getBoundingClientRect();
  let left = rect.left + e.offsetX + 12;
  let top = rect.top + e.offsetY - 8 + window.scrollY;
  if (left + 160 > window.innerWidth) left = rect.left + e.offsetX - 160;
  tip.style.left = left + "px";
  tip.style.top = top + "px";
}

function hideChartTooltip() {
  const tip = qs("#chart-tooltip");
  if (tip) tip.classList.remove("visible");
}

function fmtVal(v) {
  return v >= 10000 ? (v / 1000).toFixed(1) + "k" : String(v);
}

function renderLineChart(canvasId, daily, field) {
  const canvas = qs(`#${canvasId}`);
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const cw = canvas.clientWidth;
  const ch = canvas.clientHeight;
  canvas.width = cw * dpr;
  canvas.height = ch * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  const padL = 52, padR = 16, padT = 16, padB = 36;
  const plotW = cw - padL - padR;
  const plotH = ch - padT - padB;

  ctx.clearRect(0, 0, cw, ch);

  const textColor = getComputedStyle(document.documentElement).getPropertyValue("--text-muted").trim() || "#8b949e";
  const gridColor = getComputedStyle(document.documentElement).getPropertyValue("--border").trim() || "#21262d";

  if (!daily.length) {
    ctx.fillStyle = textColor;
    ctx.font = "13px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("暂无数据", cw / 2, ch / 2);
    return;
  }

  const values = daily.map((d) => d[field] || 0);
  const maxVal = Math.max(...values, 1);
  const labels = daily.map((d) => (d.date || "").slice(5));
  const fullDates = daily.map((d) => d.date || "");

  ctx.strokeStyle = gridColor;
  ctx.lineWidth = 0.5;
  ctx.font = "11px sans-serif";
  ctx.fillStyle = textColor;
  ctx.textAlign = "right";
  const ySteps = 4;
  for (let i = 0; i <= ySteps; i++) {
    const y = padT + plotH - (plotH * i) / ySteps;
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(padL + plotW, y);
    ctx.stroke();
    const val = Math.round((maxVal * i) / ySteps);
    ctx.fillText(fmtVal(val), padL - 6, y + 4);
  }

  const points = values.map((v, i) => ({
    x: padL + (daily.length === 1 ? plotW / 2 : (plotW * i) / (daily.length - 1)),
    y: padT + plotH - (plotH * v) / maxVal,
  }));

  ctx.beginPath();
  ctx.moveTo(points[0].x, padT + plotH);
  points.forEach((p) => ctx.lineTo(p.x, p.y));
  ctx.lineTo(points[points.length - 1].x, padT + plotH);
  ctx.closePath();
  ctx.fillStyle = "rgba(88,166,255,0.12)";
  ctx.fill();

  ctx.beginPath();
  points.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
  ctx.strokeStyle = "#58a6ff";
  ctx.lineWidth = 2;
  ctx.stroke();

  points.forEach((p) => {
    ctx.beginPath();
    ctx.arc(p.x, p.y, 3, 0, Math.PI * 2);
    ctx.fillStyle = "#58a6ff";
    ctx.fill();
  });

  ctx.fillStyle = textColor;
  ctx.font = "11px sans-serif";
  ctx.textAlign = "center";
  const maxLabels = Math.floor(plotW / 50);
  const step = Math.max(1, Math.ceil(labels.length / maxLabels));
  labels.forEach((label, i) => {
    if (i % step === 0 || i === labels.length - 1) {
      ctx.fillText(label, points[i].x, padT + plotH + 20);
    }
  });

  // Hover interaction
  const redrawHighlight = (hoverIdx) => {
    const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
    canvas._baseImage = imgData;
    ctx.putImageData(imgData, 0, 0);
    if (hoverIdx >= 0 && hoverIdx < points.length) {
      const p = points[hoverIdx];
      ctx.strokeStyle = gridColor;
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(p.x, padT);
      ctx.lineTo(p.x, padT + plotH);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.arc(p.x, p.y, 5, 0, Math.PI * 2);
      ctx.fillStyle = "#58a6ff";
      ctx.fill();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  };
  canvas._baseImage = ctx.getImageData(0, 0, canvas.width, canvas.height);

  canvas.onmousemove = (e) => {
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX - rect.left) * (canvas.width / rect.width / dpr);
    let closest = -1, minDist = 20;
    points.forEach((p, i) => {
      const d = Math.abs(mx - p.x);
      if (d < minDist) { minDist = d; closest = i; }
    });
    if (canvas._baseImage) ctx.putImageData(canvas._baseImage, 0, 0);
    if (closest >= 0) {
      redrawHighlight(closest);
      canvas._baseImage = null;
      canvas._baseImage = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const d = daily[closest];
      const html = `<strong>${fullDates[closest]}</strong><br>${field === "calls" ? "调用" : "Token"}: ${fmtVal(values[closest])}`;
      showChartTooltip(canvas, e, html);
      canvas.style.cursor = "crosshair";
    } else {
      hideChartTooltip();
      canvas.style.cursor = "";
    }
  };
  canvas.onmouseleave = () => {
    if (canvas._baseImage) ctx.putImageData(canvas._baseImage, 0, 0);
    hideChartTooltip();
    canvas.style.cursor = "";
  };
}

function renderPieChart(canvasId, scenarios, field) {
  const canvas = qs(`#${canvasId}`);
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const cw = canvas.clientWidth;
  const ch = canvas.clientHeight;
  canvas.width = cw * dpr;
  canvas.height = ch * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  ctx.clearRect(0, 0, cw, ch);

  const textColor = getComputedStyle(document.documentElement).getPropertyValue("--text-muted").trim() || "#8b949e";

  if (!scenarios.length) {
    ctx.fillStyle = textColor;
    ctx.font = "13px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("暂无数据", cw / 2, ch / 2);
    return;
  }

  const total = scenarios.reduce((s, d) => s + (d[field] || 0), 0);
  if (total === 0) {
    ctx.fillStyle = textColor;
    ctx.font = "13px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("暂无数据", cw / 2, ch / 2);
    return;
  }

  const cx = cw * 0.35;
  const cy = ch / 2;
  const radius = Math.min(cx - 16, cy - 16, 100);

  const slices = [];
  let startAngle = -Math.PI / 2;
  scenarios.forEach((item, i) => {
    const val = item[field] || 0;
    const sliceAngle = (val / total) * Math.PI * 2;
    slices.push({ start: startAngle, end: startAngle + sliceAngle, item, i, val });
    startAngle += sliceAngle;
  });

  const drawPie = (highlightIdx) => {
    ctx.clearRect(0, 0, cw, ch);
    slices.forEach((s) => {
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      const r = s.i === highlightIdx ? radius + 6 : radius;
      ctx.arc(cx, cy, r, s.start, s.end);
      ctx.closePath();
      ctx.fillStyle = CHART_COLORS[s.i % CHART_COLORS.length];
      ctx.fill();
      if (s.i === highlightIdx) {
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2;
        ctx.stroke();
      }
    });
    const legendX = cx + radius + 24;
    const legendY0 = Math.max(16, cy - scenarios.length * 10);
    ctx.font = "12px sans-serif";
    ctx.textAlign = "left";
    scenarios.forEach((item, i) => {
      const y = legendY0 + i * 22;
      ctx.fillStyle = CHART_COLORS[i % CHART_COLORS.length];
      ctx.fillRect(legendX, y, 12, 12);
      ctx.fillStyle = i === highlightIdx ? "#ffffff" : textColor;
      ctx.font = i === highlightIdx ? "bold 12px sans-serif" : "12px sans-serif";
      const pct = ((item[field] / total) * 100).toFixed(1);
      ctx.fillText(`${item.scenario || "unknown"} (${pct}%)`, legendX + 18, y + 10);
    });
  };

  drawPie(-1);

  canvas.onmousemove = (e) => {
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX - rect.left) * (cw / rect.width) - cx;
    const my = (e.clientY - rect.top) * (ch / rect.height) - cy;
    const dist = Math.sqrt(mx * mx + my * my);
    let hit = -1;
    if (dist <= radius + 6) {
      let angle = Math.atan2(my, mx);
      if (angle < -Math.PI / 2) angle += Math.PI * 2;
      for (const s of slices) {
        let start = s.start, end = s.end;
        if (angle >= start && angle < end) { hit = s.i; break; }
      }
    }
    drawPie(hit);
    if (hit >= 0) {
      const s = slices[hit];
      const pct = ((s.val / total) * 100).toFixed(1);
      showChartTooltip(canvas, e, `<strong>${s.item.scenario || "unknown"}</strong><br>${field === "calls" ? "调用" : "Token"}: ${fmtVal(s.val)} (${pct}%)`);
      canvas.style.cursor = "pointer";
    } else {
      hideChartTooltip();
      canvas.style.cursor = "";
    }
  };
  canvas.onmouseleave = () => {
    drawPie(-1);
    hideChartTooltip();
    canvas.style.cursor = "";
  };
}

function websocketStateLabel() {
  if (!state.socket) {
    return "disconnected";
  }
  switch (state.socket.readyState) {
    case WebSocket.CONNECTING:
      return "connecting";
    case WebSocket.OPEN:
      return "connected";
    case WebSocket.CLOSING:
      return "closing";
    default:
      return "disconnected";
  }
}

function updateDebugState() {
  const wsLabel = websocketStateLabel();
  setText("#ws-pill", `ws: ${wsLabel}`);
  setText("#conv-ws-state", `ws: ${wsLabel}`);
  const wsPill = qs("#ws-pill");
  if (wsPill) {
    wsPill.classList.toggle("is-ok", wsLabel === "connected");
    wsPill.classList.toggle("is-warn", wsLabel === "connecting" || wsLabel === "closing");
    wsPill.classList.toggle("is-error", wsLabel === "disconnected");
  }
}

function updateSpeechRtcState() {
  const rtc = state.speechRtc;
  setText("#conv-mic-state", `mic: ${rtc.micState}`);
  setText("#conv-vad-state", `vad: ${rtc.vadState}`);
  setText("#conv-playback-state", `play: ${rtc.playbackState}`);
  setText("#conv-ws-state", `ws: ${rtc.wsState}`);
  setText(
    "#speech-barge-in-latency",
    typeof state.speechRtc.lastBargeInLatencyMs === "number"
      ? String(state.speechRtc.lastBargeInLatencyMs)
      : "--"
  );
  setText(
    "#conv-barge-in-latency-meta",
    typeof state.speechRtc.lastBargeInLatencyMs === "number"
      ? `barge-in: ${state.speechRtc.lastBargeInLatencyMs}ms`
      : "barge-in: --"
  );
  const micButton = qs("#conv-mic");
  if (micButton) {
    micButton.classList.toggle("is-recording", rtc.running);
    micButton.setAttribute("aria-pressed", String(rtc.running));
    micButton.textContent = rtc.running ? "⏹ 停止" : "🎙 语音";
  }
  // hidden placeholders kept in sync so existing toggle logic stays valid
  const startButton = qs("#speech-start");
  const stopButton = qs("#speech-stop");
  if (startButton) {
    startButton.disabled = rtc.running;
    startButton.textContent = rtc.running ? "运行中" : "开始";
    startButton.classList.toggle("secondary", rtc.running);
    startButton.classList.toggle("primary", !rtc.running);
  }
  if (stopButton) {
    stopButton.disabled = !rtc.running;
    stopButton.textContent = rtc.running ? "停止" : "已停止";
    stopButton.classList.toggle("primary", rtc.running);
    stopButton.classList.toggle("secondary", !rtc.running);
  }
}

function rmsOfPcm16(pcm16) {
  if (!pcm16 || !pcm16.length) {
    return 0;
  }
  let sum = 0;
  for (let i = 0; i < pcm16.length; i++) {
    const v = pcm16[i] / 32768;
    sum += v * v;
  }
  return Math.sqrt(sum / pcm16.length);
}

function downsampleTo16k(float32, inputRate) {
  if (inputRate === 16000) {
    return float32;
  }
  const ratio = inputRate / 16000;
  const newLength = Math.floor(float32.length / ratio);
  const result = new Float32Array(newLength);
  let offset = 0;
  for (let i = 0; i < newLength; i++) {
    const nextOffset = Math.floor((i + 1) * ratio);
    let acc = 0;
    let count = 0;
    for (let j = offset; j < nextOffset && j < float32.length; j++) {
      acc += float32[j];
      count += 1;
    }
    result[i] = count ? acc / count : 0;
    offset = nextOffset;
  }
  return result;
}

function floatToPcm16(float32) {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    let s = float32[i];
    if (s > 1) s = 1;
    if (s < -1) s = -1;
    out[i] = s < 0 ? s * 32768 : s * 32767;
  }
  return out;
}

function int16ToBytes(int16) {
  return new Uint8Array(int16.buffer, int16.byteOffset, int16.byteLength);
}

function ensureSpeechSocket() {
  connectWebSocket();
  state.speechRtc.wsState = websocketStateLabel();
  updateSpeechRtcState();
  return state.socket && state.socket.readyState === WebSocket.OPEN;
}

function schedulePcmPlayback(pcmBytes, options = {}) {
  const rtc = state.speechRtc;
  ensurePlaybackAudioContextSync();
  if (!rtc.audioContext) {
    return;
  }
  const bytes = new Uint8Array(pcmBytes);
  if (bytes.byteLength % 2 !== 0) {
    return;
  }
  const replayBubble = options.replayBubble || rtc.replayBubble || null;
  const samples = bytes.byteLength / 2;
  const sampleRate =
    Number(options.sampleRateHz || state.speechSettings.ttsSampleRateHz || 16000) || 16000;
  const buffer = rtc.audioContext.createBuffer(1, samples, sampleRate);
  const channel = buffer.getChannelData(0);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for (let i = 0; i < samples; i++) {
    channel[i] = view.getInt16(i * 2, true) / 32768;
  }

  const source = rtc.audioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(rtc.audioContext.destination);
  const now = rtc.audioContext.currentTime;
  if (!rtc.playbackTime || rtc.playbackTime < now) {
    rtc.playbackTime = now;
  }
  source.start(rtc.playbackTime);
  rtc.playbackTime += buffer.duration;
  rtc.playbackState = "playing";
  rtc.playbackSources.push(source);
  updateSpeechRtcState();
  source.addEventListener("ended", () => {
    rtc.playbackSources = rtc.playbackSources.filter((node) => node !== source);
    const current = rtc.audioContext.currentTime;
    if (
      rtc.playbackState === "playing" &&
      rtc.playbackTime <= current + 0.01
    ) {
      rtc.playbackState = "idle";
      // Only reset the explicit replay bubble; do not touch unrelated has-tts bubbles.
      if (replayBubble) {
        setBubbleReplayState(replayBubble, "ready");
      }
      rtc.replayBubble = null;
      updateSpeechRtcState();
    }
  });
}

function stopPlayback(nextState = "idle") {
  const rtc = state.speechRtc;
  rtc.playbackSources.forEach((source) => {
    try {
      source.stop(0);
    } catch (error) {
      // ignore
    }
  });
  rtc.playbackSources = [];
  rtc.playbackTime = 0;
  rtc.playbackState = nextState;
  // Only reset the explicit replay bubble — do not touch other has-tts bubbles.
  if (rtc.replayBubble) {
    setBubbleReplayState(
      rtc.replayBubble,
      nextState === "error" ? "error" : "ready"
    );
  }
  rtc.replayBubble = null;
  updateSpeechRtcState();
}

function micUnavailableMessage() {
  const origin = window.location.origin;
  if (!window.isSecureContext) {
    return (
      `麦克风不可用：当前页面 ${origin} 是非安全上下文。浏览器只在 HTTPS 或 ` +
      `http://localhost / http://127.0.0.1 下开放麦克风。请改用 localhost 访问` +
      `（如 http://localhost:8090/console），或用 HTTPS（如 cloudflared 隧道）。`
    );
  }
  return "麦克风不可用：当前浏览器不支持 navigator.mediaDevices.getUserMedia。";
}

// Guarded getUserMedia: navigator.mediaDevices is undefined in insecure contexts
// (HTTP on a LAN IP), which otherwise throws a cryptic "reading 'getUserMedia'".
async function getMicStream(constraints) {
  if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
    throw new Error(micUnavailableMessage());
  }
  return navigator.mediaDevices.getUserMedia(constraints);
}

async function startSpeechRtc() {
  const rtc = state.speechRtc;
  if (rtc.running) {
    return;
  }
  rtc.running = true;
  rtc.captions = "";
  rtc.micState = "starting";
  rtc.vadState = "idle";
  rtc.playbackState = "idle";
  rtc.wsState = websocketStateLabel();
  updateSpeechRtcState();
  setBanner("#conv-banner", "");

  if (!(await ensureSpeechSocketReady())) {
    rtc.running = false;
    rtc.micState = "error";
    setBanner("#conv-banner", "WebSocket 未连接或鉴权未发送，无法开始语音联调。");
    updateSpeechRtcState();
    return;
  }

  try {
    // Use PCM + client VAD for web debugging so speech end automatically sends audio_end.
    // Hardware can still use codec=opus on the same /ws protocol later.
    // Reuse the shared AudioContext (also used for TTS playback) and make sure
    // it is resumed: it may have been created in a suspended state before any
    // user gesture, in which case onaudioprocess never fires and speaking the
    // mic produces no reaction at all.
    const audioContext = await ensurePlaybackAudioContext();
    if (!audioContext) {
      rtc.running = false;
      rtc.micState = "error";
      setBanner("#conv-banner", "当前浏览器不支持 AudioContext，无法采集麦克风。");
      updateSpeechRtcState();
      return;
    }
    rtc.vadThreshold = Number(qs("#speech-vad-threshold").value || "0.02");
    rtc.vadSilenceMs = Number(qs("#speech-vad-silence-ms").value || "700");
    rtc.mediaStream = await getMicStream({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    rtc.sourceNode = rtc.audioContext.createMediaStreamSource(rtc.mediaStream);
    // ScriptProcessor is deprecated but works for quick in-browser testing without extra bundling.
    rtc.processorNode = rtc.audioContext.createScriptProcessor(4096, 1, 1);
    rtc.sourceNode.connect(rtc.processorNode);
    rtc.processorNode.connect(rtc.audioContext.destination);

    rtc.micState = "running";
    rtc.vadState = "listening";
    updateSpeechRtcState();

    rtc.processorNode.onaudioprocess = (event) => {
      if (!rtc.running) return;
      const input = event.inputBuffer.getChannelData(0);
      const down = downsampleTo16k(input, rtc.audioContext.sampleRate);
      const pcm16 = floatToPcm16(down);
      const rms = rmsOfPcm16(pcm16);
      const nowMs = Date.now();
      const threshold = Number.isFinite(rtc.vadThreshold) ? rtc.vadThreshold : 0.02;
      const silenceMs = Number.isFinite(rtc.vadSilenceMs) ? rtc.vadSilenceMs : 700;

      if (rms >= threshold) {
        rtc.vadLastVoiceAt = nowMs;
        if (!rtc.vadActive) {
          rtc.vadActive = true;
          // Seed the segment with the pre-roll buffer so the onset of speech
          // (which crosses the VAD threshold only mid-frame) is not clipped.
          // Clipped onsets produce very short, low-energy clips that Volc
          // rejects as "Normal silence audio".
          rtc.pcmChunks = rtc.preRollChunks.splice(0, rtc.preRollChunks.length);
          rtc.vadState = "speaking";
          // barge-in before starting new audio capture
          rtc.lastBargeInAt = Date.now();
          rtc.lastBargeInLatencyMs = null;
          sendControl({ type: "barge_in", reason: "vad_start" });
          sendControl({
            type: "audio_start",
            user_id: convUserId(),
            codec: "pcm_s16le",
            sample_rate_hz: 16000,
          });
          state.collectingAudio = true;
          updateDebugState();
          updateSpeechRtcState();
        }
      } else if (!rtc.vadActive) {
        // Keep a short rolling pre-roll (~320ms) of the most recent frames.
        rtc.preRollChunks.push(int16ToBytes(pcm16).slice());
        if (rtc.preRollChunks.length > 8) {
          rtc.preRollChunks.shift();
        }
      }

      if (rtc.vadActive) {
        rtc.pcmChunks.push(int16ToBytes(pcm16).slice());
        // send at most ~50ms to reduce overhead
        if (nowMs - rtc.vadLastSendAt > 50) {
          rtc.vadLastSendAt = nowMs;
          for (const chunk of rtc.pcmChunks.splice(0, rtc.pcmChunks.length)) {
            if (state.socket && state.socket.readyState === WebSocket.OPEN) {
              state.socket.send(chunk);
            }
          }
        }
        if (nowMs - rtc.vadLastVoiceAt > silenceMs) {
          // end segment
          rtc.vadActive = false;
          rtc.vadState = "listening";
          // flush pending chunks
          for (const chunk of rtc.pcmChunks.splice(0, rtc.pcmChunks.length)) {
            if (state.socket && state.socket.readyState === WebSocket.OPEN) {
              state.socket.send(chunk);
            }
          }
          sendControl({ type: "audio_end" });
          state.collectingAudio = false;
          updateDebugState();
          updateSpeechRtcState();
        }
      }
    };
  } catch (error) {
    rtc.running = false;
    rtc.micState = "error";
    rtc.vadState = "error";
    setBanner("#conv-banner", `语音联调启动失败：${error.message}`);
    appendLog("error", `speech rtc start failed: ${error.message}`);
    updateSpeechRtcState();
  }
}

async function stopSpeechRtc() {
  const rtc = state.speechRtc;
  rtc.running = false;
  rtc.vadActive = false;
  rtc.pcmChunks = [];
  rtc.preRollChunks = [];
  rtc.vadState = "idle";
  rtc.micState = "stopped";
  try {
    if (rtc.mediaRecorder && rtc.mediaRecorder.state !== "inactive") {
      rtc.mediaRecorder.stop();
    }
    if (rtc.processorNode) {
      rtc.processorNode.disconnect();
      rtc.processorNode.onaudioprocess = null;
    }
    if (rtc.sourceNode) {
      rtc.sourceNode.disconnect();
    }
    if (rtc.mediaStream) {
      rtc.mediaStream.getTracks().forEach((t) => t.stop());
    }
  } catch (error) {
    // ignore
  } finally {
    rtc.mediaRecorder = null;
    rtc.processorNode = null;
    rtc.sourceNode = null;
    rtc.mediaStream = null;
  }
  stopPlayback();
  updateSpeechRtcState();
}

function clearSpeechRtc() {
  state.speechRtc.captions = "";
  setBanner("#conv-banner", "");
  updateSpeechRtcState();
}

async function fetchJson(url) {
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${url} -> ${response.status}${detail ? ` ${detail}` : ""}`);
  }
  return response.json();
}

async function sendJson(url, method, payload) {
  const response = await fetch(url, {
    method,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${url} -> ${response.status}${detail ? ` ${detail}` : ""}`);
  }
  return response.json();
}

function switchTab(tabName) {
  state.activeTab = tabName;
  document.querySelectorAll("[data-tab-target]").forEach((button) => {
    const active = button.dataset.tabTarget === tabName;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `tab-${tabName}`);
  });
  if (tabName === "metrics") {
    loadTokenMetrics().catch((error) => {
      setBanner("#metrics-banner", `Token 统计加载失败：${error.message}`);
      appendLog("error", `metrics load failed: ${error.message}`);
    });
  }
  if (tabName === "gateway") {
    updateGatewayUi();
  }
  if (tabName === "scenes") {
    loadScenesTab().catch((error) => {
      setBanner("#scene-default-banner", `场景配置加载失败：${error.message}`);
      appendLog("error", `scenes load failed: ${error.message}`);
    });
  }
}

function resetMemoryView(message = "请选择一条记忆") {
  qs("#memory-object-list").innerHTML = "";
  setMemoryEditorMeta(message);
  setMemoryEditorValue("");
  updateMemoryDeleteControl();
}

function setMemoryControlsEnabled(enabled) {
  qs("#memory-user-select").disabled = !enabled;
}

function activeMemorySection() {
  return state.memorySection || "profile";
}

function setMemoryEditorMeta(value) {
  setText("#memory-editor-meta", value || "请选择记忆对象");
}

function setMemoryEditorValue(value) {
  qs("#memory-json-editor").value =
    typeof value === "string" ? value : JSON.stringify(value ?? {}, null, 2);
}

function updateMemoryDeleteControl() {
  const section = activeMemorySection();
  const button = qs("#delete-memory-item");
  if (button) {
    const canDelete =
      Boolean(state.selectedMemoryItemId) &&
      !["profile", "bot-profile", "vocabulary"].includes(section);
    button.disabled = !canDelete;
  }
  const deleteAllButton = qs("#delete-all-turns");
  if (deleteAllButton) {
    const isTurns = section === "turns";
    deleteAllButton.hidden = !isTurns;
    const turns = state.memoryPayloads.turns;
    deleteAllButton.disabled =
      !isTurns || !Array.isArray(turns) || turns.length === 0;
  }
}

function getMemoryEditorJson() {
  const raw = qs("#memory-json-editor").value.trim();
  if (!raw) {
    return {};
  }
  return JSON.parse(raw);
}

function sourceLabel(source) {
  if (source === "runtime_override") {
    return "runtime override";
  }
  if (source === "env") {
    return "env";
  }
  if (source === "default") {
    return "default";
  }
  return "unset";
}

function renderLLMSettings() {
  const {
    configured,
    model,
    modelSource,
    baseUrl,
    baseUrlSource,
    apiKeySource,
    systemPrompt,
    systemPromptSource,
    userPromptTemplate,
    userPromptSource,
    masked,
    revealed,
    isVisible,
    isEditing,
  } =
    state.llmSettings;
  setText("#llm-model-source", sourceLabel(modelSource));
  setText("#llm-base-url-source", sourceLabel(baseUrlSource));
  setText("#llm-api-key-source", sourceLabel(apiKeySource));
  setText("#llm-user-prompt-source", sourceLabel(userPromptSource));
  setText("#llm-api-key-configured", String(configured));

  const display = qs("#llm-api-key-display");
  const editor = qs("#llm-api-key-input");
  const modelInput = qs("#llm-model-input");
  const baseUrlInput = qs("#llm-base-url-input");
  const userPromptInput = qs("#llm-user-prompt-input");
  const editToggle = qs("#llm-edit-toggle");
  const visibilityToggle = qs("#llm-visibility-toggle");
  const saveButton = qs("#llm-save");
  const cancelButton = qs("#llm-cancel");

  const displayValue = configured
    ? isVisible
      ? revealed || masked || "(empty)"
      : masked || "(hidden)"
    : "未配置";

  display.textContent = displayValue;
  display.hidden = isEditing;
  editor.hidden = !isEditing;
  editor.value = revealed;
  modelInput.value = model;
  baseUrlInput.value = baseUrl;
  userPromptInput.value = userPromptTemplate;
  modelInput.disabled = !isEditing;
  baseUrlInput.disabled = !isEditing;
  userPromptInput.disabled = !isEditing;
  editor.disabled = !isEditing;

  editToggle.hidden = isEditing;
  visibilityToggle.hidden = isEditing;
  saveButton.hidden = !isEditing;
  cancelButton.hidden = !isEditing;
  visibilityToggle.textContent = isVisible ? "隐藏" : "显示";
}

function renderSpeechSettings() {
  const {
    configured,
    asrWsUrl,
    asrWsUrlSource,
    asrResourceId,
    asrResourceIdSource,
    asrApiKeyMasked,
    asrApiKeyRevealed,
    ttsWsUrl,
    ttsWsUrlSource,
    ttsResourceId,
    ttsVoiceType,
    ttsVoiceTypeSource,
    ttsEncoding,
    ttsSampleRateHz,
    ttsTokenMasked,
    ttsTokenRevealed,
    isVisible,
    isEditing,
  } = state.speechSettings;

  setText("#speech-asr-source", sourceLabel(asrWsUrlSource));
  setText("#speech-tts-source", sourceLabel(ttsWsUrlSource));
  setText("#speech-api-key-configured", String(configured));
  setText("#speech-asr-resource-source", sourceLabel(asrResourceIdSource));
  setText("#speech-tts-voice-source", sourceLabel(ttsVoiceTypeSource || "unset"));
  setText("#speech-tts-sample-rate", String(ttsSampleRateHz || 16000));

  const asrTokenDisplay = qs("#speech-asr-key-display");
  const asrTokenInput = qs("#speech-asr-token-input");
  const ttsTokenDisplay = qs("#speech-tts-token-display");
  const ttsTokenInput = qs("#speech-tts-token-input");
  const inputs = [
    ["#speech-asr-ws-url-input", asrWsUrl],
    ["#speech-asr-resource-id-input", asrResourceId],
    ["#speech-tts-ws-url-input", ttsWsUrl],
    ["#speech-tts-resource-id-input", ttsResourceId],
    ["#speech-tts-voice-input", ttsVoiceType],
    ["#speech-tts-encoding-input", ttsEncoding],
    ["#speech-tts-sample-rate-input", String(ttsSampleRateHz || 16000)],
  ];
  const editToggle = qs("#speech-edit-toggle");
  const visibilityToggle = qs("#speech-visibility-toggle");
  const saveButton = qs("#speech-save");
  const cancelButton = qs("#speech-cancel");

  asrTokenDisplay.textContent = isVisible
    ? asrApiKeyRevealed || asrApiKeyMasked || "(empty)"
    : asrApiKeyMasked || "(hidden)";
  asrTokenDisplay.hidden = isEditing;
  asrTokenInput.hidden = !isEditing;
  asrTokenInput.value = asrApiKeyRevealed;
  ttsTokenDisplay.textContent = isVisible
    ? ttsTokenRevealed || ttsTokenMasked || "(empty)"
    : ttsTokenMasked || "(hidden)";
  ttsTokenDisplay.hidden = isEditing;
  ttsTokenInput.hidden = !isEditing;
  ttsTokenInput.value = ttsTokenRevealed;
  for (const [selector, value] of inputs) {
    const input = qs(selector);
    input.value = value;
    input.disabled = !isEditing;
  }
  asrTokenInput.disabled = !isEditing;
  ttsTokenInput.disabled = !isEditing;

  editToggle.hidden = isEditing;
  visibilityToggle.hidden = isEditing;
  saveButton.hidden = !isEditing;
  cancelButton.hidden = !isEditing;
  visibilityToggle.textContent = isVisible ? "隐藏" : "显示";
}

async function loadLLMSettings({ reveal = false } = {}) {
  setBanner("#llm-banner", "");
  try {
    const data = await fetchJson(`/api/settings/llm${reveal ? "?reveal=true" : ""}`);
    state.llmSettings.configured = Boolean(data.configured);
    state.llmSettings.model = data.model || "";
    state.llmSettings.modelSource = data.model_source || "unset";
    state.llmSettings.baseUrl = data.base_url || "";
    state.llmSettings.baseUrlSource = data.base_url_source || "unset";
    state.llmSettings.apiKeySource = data.api_key_source || "unset";
    state.llmSettings.systemPrompt = data.system_prompt || "";
    state.llmSettings.systemPromptSource = data.system_prompt_source || "unset";
    state.llmSettings.userPromptTemplate = data.user_prompt_template || "";
    state.llmSettings.userPromptSource = data.user_prompt_source || "unset";
    state.llmSettings.masked = data.api_key_masked || "";
    state.llmSettings.revealed = reveal ? data.api_key || "" : "";
    state.llmSettings.isVisible = reveal;
    renderLLMSettings();
  } catch (error) {
    setBanner("#llm-banner", `模型配置加载失败：${error.message}`);
    appendLog("error", `llm settings load failed: ${error.message}`);
  }
}

async function loadSpeechSettings({ reveal = false } = {}) {
  setBanner("#speech-banner", "");
  try {
    const data = await fetchJson(
      `/api/settings/speech${reveal ? "?reveal=true" : ""}`
    );
    state.speechSettings.configured = Boolean(data.configured);
    state.speechSettings.asrWsUrl = data.asr_ws_url || "";
    state.speechSettings.asrWsUrlSource = data.asr_ws_url_source || "unset";
    state.speechSettings.asrResourceId = data.asr_resource_id || "";
    state.speechSettings.asrResourceIdSource = data.asr_resource_id_source || "unset";
    state.speechSettings.asrSampleRateHz = Number(data.asr_sample_rate_hz || 16000) || 16000;
    state.speechSettings.asrApiKeyMasked = data.asr_api_key_masked || "";
    state.speechSettings.asrApiKeyRevealed = reveal ? data.asr_api_key || "" : "";
    state.speechSettings.ttsWsUrl = data.tts_ws_url || "";
    state.speechSettings.ttsWsUrlSource = data.tts_ws_url_source || "unset";
    state.speechSettings.ttsResourceId = data.tts_resource_id || "";
    state.speechSettings.ttsResourceIdSource = data.tts_resource_id_source || "unset";
    state.speechSettings.ttsVoiceType = data.tts_voice_type || "";
    state.speechSettings.ttsVoiceTypeSource = data.tts_voice_type_source || "unset";
    state.speechSettings.ttsEncoding = data.tts_encoding || "";
    state.speechSettings.ttsSampleRateHz = Number(data.tts_sample_rate_hz || 16000) || 16000;
    state.speechSettings.ttsTokenMasked = data.tts_api_key_masked || "";
    state.speechSettings.ttsTokenRevealed = reveal ? data.tts_api_key || "" : "";
    state.speechSettings.isVisible = reveal;
    renderSpeechSettings();
  } catch (error) {
    setBanner("#speech-banner", `语音配置加载失败：${error.message}`);
    appendLog("error", `speech settings load failed: ${error.message}`);
  }
}

async function saveLLMSettings() {
  const editor = qs("#llm-api-key-input");
  setBanner("#llm-banner", "");
  try {
    const data = await sendJson("/api/settings/llm", "PUT", {
      model: qs("#llm-model-input").value,
      base_url: qs("#llm-base-url-input").value,
      // base system prompt is edited in the 「场景」 tab; preserve it here so this
      // all-or-nothing PUT does not wipe it (it is saved via saveSceneBasePrompt).
      system_prompt: state.llmSettings.systemPrompt,
      user_prompt_template: qs("#llm-user-prompt-input").value,
      api_key: editor.value,
    });
    state.llmSettings.configured = Boolean(data.configured);
    state.llmSettings.model = data.model || "";
    state.llmSettings.modelSource = data.model_source || "unset";
    state.llmSettings.baseUrl = data.base_url || "";
    state.llmSettings.baseUrlSource = data.base_url_source || "unset";
    state.llmSettings.apiKeySource = data.api_key_source || "unset";
    state.llmSettings.systemPrompt = data.system_prompt || "";
    state.llmSettings.systemPromptSource = data.system_prompt_source || "unset";
    state.llmSettings.userPromptTemplate = data.user_prompt_template || "";
    state.llmSettings.userPromptSource = data.user_prompt_source || "unset";
    state.llmSettings.masked = data.api_key_masked || "";
    state.llmSettings.revealed = editor.value.trim();
    state.llmSettings.isEditing = false;
    state.llmSettings.isVisible = false;
    renderLLMSettings();
    setBanner("#llm-banner", "LLM 配置已保存，下一轮文本或语音输入会自动使用最新配置，无需重连。");
    showToast("LLM 配置已保存", "success");
    await loadStatus();
  } catch (error) {
    setBanner("#llm-banner", `模型配置保存失败：${error.message}`);
    appendLog("error", `llm settings save failed: ${error.message}`);
    showToast(`模型配置保存失败：${error.message}`, "error");
  }
}

async function saveSpeechSettings() {
  setBanner("#speech-banner", "");
  try {
    const data = await sendJson("/api/settings/speech", "PUT", {
      mode: "split",
      asr_api_key: qs("#speech-asr-token-input").value,
      asr_resource_id: qs("#speech-asr-resource-id-input").value,
      asr_ws_url: qs("#speech-asr-ws-url-input").value,
      asr_sample_rate_hz: 16000,
      tts_api_key: qs("#speech-tts-token-input").value,
      tts_resource_id: qs("#speech-tts-resource-id-input").value,
      tts_ws_url: qs("#speech-tts-ws-url-input").value,
      tts_sample_rate_hz:
        Number(qs("#speech-tts-sample-rate-input").value || "16000") || 16000,
      tts_encoding: qs("#speech-tts-encoding-input").value || "pcm",
      tts_voice_type: qs("#speech-tts-voice-input").value,
    });
    state.speechSettings.configured = Boolean(data.configured);
    state.speechSettings.asrWsUrl = data.asr_ws_url || "";
    state.speechSettings.asrWsUrlSource = data.asr_ws_url_source || "unset";
    state.speechSettings.asrResourceId = data.asr_resource_id || "";
    state.speechSettings.asrResourceIdSource = data.asr_resource_id_source || "unset";
    state.speechSettings.asrSampleRateHz = Number(data.asr_sample_rate_hz || 16000) || 16000;
    state.speechSettings.asrApiKeyMasked = data.asr_api_key_masked || "";
    state.speechSettings.asrApiKeyRevealed = qs("#speech-asr-token-input").value.trim();
    state.speechSettings.ttsWsUrl = data.tts_ws_url || "";
    state.speechSettings.ttsWsUrlSource = data.tts_ws_url_source || "unset";
    state.speechSettings.ttsResourceId = data.tts_resource_id || "";
    state.speechSettings.ttsResourceIdSource = data.tts_resource_id_source || "unset";
    state.speechSettings.ttsVoiceType = data.tts_voice_type || "";
    state.speechSettings.ttsVoiceTypeSource = data.tts_voice_type_source || "unset";
    state.speechSettings.ttsEncoding = data.tts_encoding || "";
    state.speechSettings.ttsSampleRateHz = Number(data.tts_sample_rate_hz || 16000) || 16000;
    state.speechSettings.ttsTokenMasked = data.tts_api_key_masked || "";
    state.speechSettings.ttsTokenRevealed = qs("#speech-tts-token-input").value.trim();
    state.speechSettings.isEditing = false;
    state.speechSettings.isVisible = false;
    renderSpeechSettings();
    setBanner("#speech-banner", "语音配置已保存，下一轮文本或语音输入会自动使用最新配置，无需重连。");
    showToast("语音配置已保存", "success");
    await loadStatus();
  } catch (error) {
    setBanner("#speech-banner", `语音配置保存失败：${error.message}`);
    appendLog("error", `speech settings save failed: ${error.message}`);
    showToast(`语音配置保存失败：${error.message}`, "error");
  }
}

async function enterLLMEditMode() {
  if (!state.llmSettings.isVisible) {
    try {
      const data = await fetchJson("/api/settings/llm?reveal=true");
      state.llmSettings.configured = Boolean(data.configured);
      state.llmSettings.model = data.model || "";
      state.llmSettings.modelSource = data.model_source || "unset";
      state.llmSettings.baseUrl = data.base_url || "";
      state.llmSettings.baseUrlSource = data.base_url_source || "unset";
      state.llmSettings.apiKeySource = data.api_key_source || "unset";
      state.llmSettings.systemPrompt = data.system_prompt || "";
      state.llmSettings.systemPromptSource = data.system_prompt_source || "unset";
      state.llmSettings.userPromptTemplate = data.user_prompt_template || "";
      state.llmSettings.userPromptSource = data.user_prompt_source || "unset";
      state.llmSettings.masked = data.api_key_masked || "";
      state.llmSettings.revealed = data.api_key || "";
    } catch (error) {
      setBanner("#llm-banner", `模型配置加载失败：${error.message}`);
      appendLog("error", `llm settings reveal failed: ${error.message}`);
      return;
    }
  }
  state.llmSettings.isEditing = true;
  renderLLMSettings();
  qs("#llm-api-key-input").focus();
}

async function enterSpeechEditMode() {
  if (!state.speechSettings.isVisible) {
    try {
      const data = await fetchJson("/api/settings/speech?reveal=true");
      state.speechSettings.configured = Boolean(data.configured);
      state.speechSettings.asrWsUrl = data.asr_ws_url || "";
      state.speechSettings.asrWsUrlSource = data.asr_ws_url_source || "unset";
      state.speechSettings.asrResourceId = data.asr_resource_id || "";
      state.speechSettings.asrResourceIdSource = data.asr_resource_id_source || "unset";
      state.speechSettings.asrSampleRateHz = Number(data.asr_sample_rate_hz || 16000) || 16000;
      state.speechSettings.asrApiKeyMasked = data.asr_api_key_masked || "";
      state.speechSettings.asrApiKeyRevealed = data.asr_api_key || "";
      state.speechSettings.ttsWsUrl = data.tts_ws_url || "";
      state.speechSettings.ttsWsUrlSource = data.tts_ws_url_source || "unset";
      state.speechSettings.ttsResourceId = data.tts_resource_id || "";
      state.speechSettings.ttsResourceIdSource = data.tts_resource_id_source || "unset";
      state.speechSettings.ttsVoiceType = data.tts_voice_type || "";
      state.speechSettings.ttsVoiceTypeSource = data.tts_voice_type_source || "unset";
      state.speechSettings.ttsEncoding = data.tts_encoding || "";
      state.speechSettings.ttsSampleRateHz = Number(data.tts_sample_rate_hz || 16000) || 16000;
      state.speechSettings.ttsTokenMasked = data.tts_api_key_masked || "";
      state.speechSettings.ttsTokenRevealed = data.tts_api_key || "";
    } catch (error) {
      setBanner("#speech-banner", `语音配置加载失败：${error.message}`);
      appendLog("error", `speech settings reveal failed: ${error.message}`);
      return;
    }
  }
  state.speechSettings.isEditing = true;
  renderSpeechSettings();
  qs("#speech-asr-ws-url-input").focus();
}

function cancelLLMEditMode() {
  state.llmSettings.isEditing = false;
  if (!state.llmSettings.isVisible) {
    state.llmSettings.revealed = "";
  }
  renderLLMSettings();
}

function cancelSpeechEditMode() {
  state.speechSettings.isEditing = false;
  if (!state.speechSettings.isVisible) {
    state.speechSettings.asrApiKeyRevealed = "";
    state.speechSettings.ttsTokenRevealed = "";
  }
  renderSpeechSettings();
}

const LOG_COLLAPSE_KEY = "voice-robot-log-collapsed";

function setLogPaneCollapsed(collapsed) {
  state.conversation.logCollapsed = Boolean(collapsed);
  const layout = qs("#conversation-layout");
  if (layout) {
    layout.classList.toggle("log-collapsed", state.conversation.logCollapsed);
  }
  const collapseButton = qs("#conv-log-collapse");
  if (collapseButton) {
    collapseButton.textContent = state.conversation.logCollapsed ? "展开" : "收起";
  }
  try {
    localStorage.setItem(LOG_COLLAPSE_KEY, state.conversation.logCollapsed ? "1" : "0");
  } catch (error) {
    // ignore persistence failures
  }
}

function toggleLogPane() {
  setLogPaneCollapsed(!state.conversation.logCollapsed);
}

function initLogPane() {
  let stored = null;
  try {
    stored = localStorage.getItem(LOG_COLLAPSE_KEY);
  } catch (error) {
    stored = null;
  }
  setLogPaneCollapsed(stored === "1");
}

async function sendTextInput() {
  const input = qs("#conv-input");
  const text = (input && input.value.trim()) || "";
  if (!text) {
    setBanner("#conv-banner", "请输入要发送的文本。");
    showToast("请输入要发送的文本。", "error");
    return;
  }
  // The user gesture here is required for browsers to allow AudioContext to
  // resume — prepare it now so subsequent TTS playback can start without delay.
  await ensurePlaybackAudioContext();
  setBanner("#conv-banner", "");
  setButtonLoading("#conv-send", true);
  try {
    const ready = await ensureSpeechSocketReady();
    if (!ready) {
      setBanner("#conv-banner", "WebSocket 未连接或鉴权失败，无法发送。");
      showToast("WebSocket 未连接或鉴权失败。", "error");
      return;
    }
    appendUserBubble(text, "text");
    if (input) {
      input.value = "";
      autoResizeComposer();
    }
    state.conversation.currentAssistantBubble = null;
    state.conversation.streamedText = "";
    sendControl({
      type: "text_input",
      user_id: convUserId(),
      text,
    });
  } finally {
    setButtonLoading("#conv-send", false);
  }
}

function toggleMic() {
  if (state.speechRtc.running) {
    stopSpeechRtc();
  } else {
    startSpeechRtc();
  }
}

function autoResizeComposer() {
  const input = qs("#conv-input");
  if (!input) {
    return;
  }
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
}

function clearThread(showHint = true) {
  const thread = qs("#conversation-thread");
  if (!thread) {
    return;
  }
  thread.innerHTML = "";
  state.conversation.currentAssistantBubble = null;
  state.conversation.streamedText = "";
  if (showHint) {
    const hint = document.createElement("div");
    hint.className = "thread-empty";
    hint.id = "thread-empty";
    hint.textContent =
      "开始对话：输入文本回车发送，或点击麦克风开启连续语音。";
    thread.append(hint);
  }
}

async function loadConversationUsers() {
  const select = qs("#conv-history-user");
  if (!select) {
    return;
  }
  try {
    const data = await fetchJson("/api/memory/users");
    const users = Array.isArray(data.users) ? data.users : [];
    const previous = select.value;
    select.innerHTML = "";
    if (!users.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "暂无用户";
      select.append(option);
      return;
    }
    users.forEach((userId) => {
      const option = document.createElement("option");
      option.value = userId;
      option.textContent = userId;
      select.append(option);
    });
    const current = convUserId();
    select.value = users.includes(previous)
      ? previous
      : users.includes(current)
      ? current
      : users[0];
  } catch (error) {
    appendLog("error", `conversation users load failed: ${error.message}`);
  }
}

async function loadConversationHistory(options = {}) {
  const { silent = false } = options;
  const select = qs("#conv-history-user");
  const userId = (select && select.value) || "";
  if (!userId) {
    setBanner("#conv-banner", "请选择要加载历史对话的用户。");
    if (!silent) {
      showToast("请选择用户。", "error");
    }
    return;
  }
  setBanner("#conv-banner", "");
  setButtonLoading("#conv-load-history", true);
  try {
    const data = await fetchJson(
      `/api/memory/users/${encodeURIComponent(userId)}/turns?limit=200`
    );
    const turns = Array.isArray(data.turns) ? data.turns : [];
    turns.sort((a, b) => (a.sequence || 0) - (b.sequence || 0));
    clearThread(false);
    // switch active user to the loaded one
    const userInput = qs("#conv-user-id");
    if (userInput) {
      userInput.value = userId;
    }
    if (!turns.length) {
      clearThread(true);
      if (!silent) {
        showToast(`用户 ${userId} 暂无历史对话`, "info");
      }
      return;
    }
    turns.forEach((turn) => {
      if (turn.user_text) {
        appendUserBubble(turn.user_text, "text", turn.created_at || "");
      }
      if (turn.assistant_text) {
        appendBubble({
          role: "assistant",
          kind: "text",
          text: turn.assistant_text,
          turnId: turn.turn_id || "",
          createdAt: turn.created_at || "",
          ttsUrl: turn.tts_byte_length
            ? `/api/memory/users/${encodeURIComponent(userId)}/turns/${encodeURIComponent(
                turn.turn_id
              )}/tts`
            : "",
          ttsAudioFormat: turn.tts_audio_format || "",
          ttsSampleRateHz: turn.tts_sample_rate_hz || 0,
        });
      }
    });
    if (!silent) {
      showToast(`已加载 ${userId} 的 ${turns.length} 轮历史对话`, "success");
    }
  } catch (error) {
    setBanner("#conv-banner", `历史对话加载失败：${error.message}`);
    appendLog("error", `conversation history load failed: ${error.message}`);
    if (!silent) {
      showToast(`历史对话加载失败：${error.message}`, "error");
    }
  } finally {
    setButtonLoading("#conv-load-history", false);
  }
}

function startNewSession() {
  clearThread(true);
  state.conversation.turnNo = 0;
  setBanner("#conv-banner", "");
  showToast("已开始新对话", "info");
}

async function sendTextChat() {
  const text = qs("#chat-input").value.trim();
  if (!text) {
    setBanner("#chat-banner", "请输入要发送的文本。");
    showToast("请输入要发送的文本。", "error");
    return;
  }
  if (state.chatRequest) {
    state.chatRequest.abort();
  }
  const controller = new AbortController();
  state.chatRequest = controller;
  setBanner("#chat-banner", "");
  setText("#chat-stream-state", "streaming");
  const chatOutput = qs("#chat-output");
  chatOutput.textContent = "";
  chatOutput.classList.add("is-streaming");
  setButtonLoading("#send-text-chat", true);
  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {
        Accept: "application/x-ndjson",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        user_id: qs("#chat-user-id").value || "default-user",
        text,
      }),
      signal: controller.signal,
    });
    if (!response.ok || !response.body) {
      const detail = await response.text();
      throw new Error(
        `/api/chat/stream -> ${response.status}${detail ? ` ${detail}` : ""}`
      );
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let reply = "";
    let replySource = "";
    let fallbackReason = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) {
          continue;
        }
        const event = JSON.parse(line);
        if (event.type === "prompt") {
          appendLog("incoming", {
            type: "llm_prompt",
            ...(event.prompt_debug || {}),
          });
        } else if (event.type === "start") {
          replySource = event.reply_source || "";
          fallbackReason = event.fallback_reason || "";
          appendLog("incoming", {
            type: "chat_stream_start",
            source: replySource,
            detail: fallbackReason,
          });
        } else if (event.type === "delta") {
          reply += event.text || "";
          qs("#chat-output").textContent = reply || "--";
        } else if (event.type === "end") {
          reply = event.reply || reply;
          qs("#chat-output").textContent = reply || "--";
          replySource = event.reply_source || replySource;
          fallbackReason = event.fallback_reason || fallbackReason;
        }
      }
    }
    const source = replySource ? ` [${replySource}]` : "";
    const detail = fallbackReason ? ` | ${fallbackReason}` : "";
    appendLog("incoming", `chat${source}: ${reply || "--"}${detail}`);
    setText("#chat-stream-state", "done");
  } catch (error) {
    if (error.name !== "AbortError") {
      setBanner("#chat-banner", `文本交流失败：${error.message}`);
      appendLog("error", `chat failed: ${error.message}`);
      setText("#chat-stream-state", "error");
      showToast(`文本交流失败：${error.message}`, "error");
    }
  } finally {
    if (state.chatRequest === controller) {
      state.chatRequest = null;
    }
    chatOutput.classList.remove("is-streaming");
    setButtonLoading("#send-text-chat", false);
  }
}

async function toggleLLMVisibility() {
  if (state.llmSettings.isVisible) {
    state.llmSettings.isVisible = false;
    state.llmSettings.revealed = "";
    renderLLMSettings();
    return;
  }
  await loadLLMSettings({ reveal: true });
}

async function toggleSpeechVisibility() {
  if (state.speechSettings.isVisible) {
    state.speechSettings.isVisible = false;
    state.speechSettings.asrApiKeyRevealed = "";
    state.speechSettings.ttsTokenRevealed = "";
    renderSpeechSettings();
    return;
  }
  await loadSpeechSettings({ reveal: true });
}

async function loadStatus() {
  setBanner("#status-banner", "");
  try {
    const [health, status] = await Promise.all([
      fetchJson("/health"),
      fetchJson("/api/status"),
    ]);
    setText("#health-pill", `health: ${health.status ?? "--"}`);
    const healthPill = qs("#health-pill");
    if (healthPill) {
      const ok = (health.status ?? "") === "ok";
      healthPill.classList.toggle("is-ok", ok);
      healthPill.classList.toggle("is-warn", !ok);
      healthPill.classList.remove("is-error");
    }
    setText("#status-app-name", status.app_name ?? "--");
    setText("#status-default-user", status.default_user_id ?? "--");
    setText("#status-llm-model", status.llm_model ?? "--");
    setText("#status-tts-voice", status.tts_voice_type ?? "--");
    setText("#status-llm-model-source-overview", sourceLabel(status.llm_model_source ?? "unset"));
    setText("#status-tts-voice-source", sourceLabel(status.tts_voice_type_source ?? "unset"));
    setText("#status-memory-root", status.memory_root ?? "--");
    setText("#status-llm-base-url", status.llm_api_base_url ?? "--");
    setText("#status-asr-resource-id", status.speech_asr_resource_id ?? "--");
    setText(
      "#status-asr-resource-source",
      sourceLabel(status.speech_asr_resource_id_source ?? "unset")
    );
    state.llmSettings.configured = status.llm_api_key_configured === "true";
    state.llmSettings.model = status.llm_model ?? "";
    state.llmSettings.modelSource = status.llm_model_source ?? "unset";
    state.llmSettings.baseUrl = status.llm_api_base_url ?? "";
    state.llmSettings.baseUrlSource = status.llm_api_base_url_source ?? "unset";
    state.llmSettings.masked = status.llm_api_key_masked ?? "";
    state.llmSettings.apiKeySource = status.llm_api_key_source ?? "unset";
    if (!state.llmSettings.isVisible) {
      state.llmSettings.revealed = "";
    }
    renderLLMSettings();

    state.speechSettings.configured =
      Boolean(status.speech_asr_resource_id) || Boolean(status.speech_tts_ws_url);
    state.speechSettings.asrResourceId = status.speech_asr_resource_id ?? "";
    state.speechSettings.asrResourceIdSource = status.speech_asr_resource_id_source ?? "unset";
    state.speechSettings.ttsWsUrl = status.speech_tts_ws_url ?? state.speechSettings.ttsWsUrl;
    state.speechSettings.ttsWsUrlSource = status.speech_tts_ws_url_source ?? "unset";
    state.speechSettings.ttsVoiceType = status.tts_voice_type ?? state.speechSettings.ttsVoiceType;
    state.speechSettings.ttsVoiceTypeSource = status.tts_voice_type_source ?? "unset";
    if (!state.speechSettings.isVisible) {
      state.speechSettings.asrApiKeyRevealed = "";
      state.speechSettings.ttsTokenRevealed = "";
    }
    renderSpeechSettings();

    setText(
      "#status-note",
      status.health === "ok"
        ? "当前运行态可用，可在右侧调试台直接联调。"
        : "运行态已返回状态，但建议先检查服务健康。"
    );
  } catch (error) {
    setBanner("#status-banner", `运行态加载失败：${error.message}`);
    setText("#health-pill", "health: error");
    const healthPill = qs("#health-pill");
    if (healthPill) {
      healthPill.classList.remove("is-ok", "is-warn");
      healthPill.classList.add("is-error");
    }
    setText("#status-note", "状态接口暂不可用，可稍后手动刷新。");
    appendLog("error", `status load failed: ${error.message}`);
  }
}

async function loadDevices() {
  const pill = qs("#hardware-pill");
  if (!pill) {
    return;
  }
  try {
    const data = await fetchJson("/api/devices");
    const count = Number(data.hardware_online || 0);
    const devices = Array.isArray(data.devices) ? data.devices : [];
    const hardware = devices.filter((item) => item.kind === "hardware");
    pill.textContent = count > 0 ? `硬件: ${count} 在线` : "硬件: 离线";
    pill.classList.toggle("is-ok", count > 0);
    pill.classList.toggle("is-error", count === 0);
    pill.title = hardware.length
      ? `在线硬件设备：\n${hardware.map((item) => `· ${item.device_id}`).join("\n")}`
      : "暂无硬件设备连接";
    populateDeviceSelect(hardware);
  } catch (error) {
    pill.textContent = "硬件: 未知";
    pill.classList.remove("is-ok", "is-error");
    pill.classList.add("is-warn");
    pill.title = `设备状态获取失败：${error.message}`;
  }
}

function startDevicePolling() {
  loadDevices();
  if (state.devicesTimer) {
    clearInterval(state.devicesTimer);
  }
  state.devicesTimer = setInterval(loadDevices, 5000);
}

function populateDeviceSelect(hardware) {
  const select = qs("#conv-device-select");
  if (!select) {
    return;
  }
  const previous = select.value;
  const ids = hardware.map((item) => item.device_id);
  select.innerHTML = "";
  if (!ids.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "无在线硬件";
    select.append(option);
    updateDeviceVolumeControl();
    populateTraceDeviceSelect([]);
    populateVocabDeviceSelect([]);
    populateIdiomDeviceSelect([]);
    return;
  }
  ids.forEach((id) => {
    const option = document.createElement("option");
    option.value = id;
    option.textContent = id;
    select.append(option);
  });
  select.value = ids.includes(previous) ? previous : ids[0];
  updateDeviceVolumeControl();
  populateTraceDeviceSelect(hardware);
  populateVocabDeviceSelect(hardware);
  populateIdiomDeviceSelect(hardware);
  if (!qs("#gateway-device-id")?.value) {
    const gatewayDeviceInput = qs("#gateway-device-id");
    if (gatewayDeviceInput) {
      gatewayDeviceInput.value = select.value || "";
      updateGatewayUi();
    }
  }
}

function selectedDeviceId() {
  const select = qs("#conv-device-select");
  return (select && select.value) || "";
}

async function dispatchDeviceSay() {
  const deviceId = selectedDeviceId();
  if (!deviceId) {
    showToast("没有可下发的在线硬件设备", "error");
    return;
  }
  const input = qs("#conv-input");
  const text = (input && input.value.trim()) || "";
  if (!text) {
    showToast("请输入要下发给硬件播报的文本", "error");
    return;
  }
  setButtonLoading("#conv-device-say", true);
  try {
    await sendJson(`/api/devices/${encodeURIComponent(deviceId)}/say`, "POST", {
      text,
      user_id: convUserId(),
    });
    if (input) {
      input.value = "";
      autoResizeComposer();
    }
    appendBubble({ role: "user", kind: "text", text: `↗ 下发到 ${deviceId}：${text}` });
    appendTurnLog("system", `已下发文本到硬件 ${deviceId}`, text);
    showToast(`已下发到 ${deviceId}`, "success");
  } catch (error) {
    setBanner("#conv-banner", `下发失败：${error.message}`);
    appendTurnLog("error", `下发到 ${deviceId} 失败`, error.message);
    showToast(`下发失败：${error.message}`, "error");
  } finally {
    setButtonLoading("#conv-device-say", false);
  }
}

function updateDeviceVolumeControl() {
  const hasDevice = Boolean(selectedDeviceId());
  const toggle = qs("#conv-volume-toggle");
  const slider = qs("#conv-volume-slider");
  const apply = qs("#conv-volume-apply");
  const mute = qs("#conv-volume-mute");
  const value = qs("#conv-volume-value");
  if (toggle) {
    toggle.disabled = !hasDevice;
  }
  [slider, apply, mute].forEach((element) => {
    if (element) {
      element.disabled = !hasDevice;
    }
  });
  if (value) {
    value.textContent = String(state.deviceVolume);
  }
  updateDeviceConversationControl();
}

function updateDeviceConversationControl() {
  const button = qs("#conv-device-conversation");
  const hasDevice = Boolean(selectedDeviceId());
  if (!button) {
    return;
  }
  button.disabled = !hasDevice;
  const on = hasDevice && state.deviceConversationOn;
  button.setAttribute("aria-pressed", on ? "true" : "false");
  button.textContent = on ? "对话模式: 开" : "对话模式";
}

async function toggleDeviceConversation() {
  const deviceId = selectedDeviceId();
  if (!deviceId) {
    showToast("没有可控制的在线硬件设备", "error");
    return;
  }
  const next = !state.deviceConversationOn;
  setButtonLoading("#conv-device-conversation", true);
  try {
    await sendJson(`/api/devices/${encodeURIComponent(deviceId)}/conversation`, "POST", {
      enabled: next,
    });
    state.deviceConversationOn = next;
    updateDeviceConversationControl();
    appendTurnLog("system", `已${next ? "开启" : "关闭"}硬件 ${deviceId} 对话模式`, { enabled: next });
    showToast(`对话模式已${next ? "开启" : "关闭"}`, "success");
  } catch (error) {
    setBanner("#conv-banner", `对话模式切换失败：${error.message}`);
    appendTurnLog("error", `切换硬件 ${deviceId} 对话模式失败`, error.message);
    showToast(`对话模式切换失败：${error.message}`, "error");
  } finally {
    setButtonLoading("#conv-device-conversation", false);
  }
}

function toggleDeviceVolumePanel() {
  const panel = qs("#conv-volume-panel");
  const toggle = qs("#conv-volume-toggle");
  if (!panel || !toggle) {
    return;
  }
  if (!selectedDeviceId()) {
    showToast("没有可调节音量的在线硬件设备", "error");
    return;
  }
  panel.hidden = !panel.hidden;
  toggle.setAttribute("aria-expanded", panel.hidden ? "false" : "true");
}

async function dispatchDeviceVolume(volume) {
  const deviceId = selectedDeviceId();
  if (!deviceId) {
    showToast("没有可调节音量的在线硬件设备", "error");
    return;
  }
  const normalized = Math.max(0, Math.min(100, Number(volume) || 0));
  setButtonLoading("#conv-volume-apply", true);
  try {
    const data = await sendJson(`/api/devices/${encodeURIComponent(deviceId)}/volume`, "POST", {
      volume: normalized,
    });
    state.deviceVolume = Number(data.volume ?? normalized);
    if (state.deviceVolume > 0) {
      state.previousDeviceVolume = state.deviceVolume;
    }
    const slider = qs("#conv-volume-slider");
    if (slider) {
      slider.value = String(state.deviceVolume);
    }
    updateDeviceVolumeControl();
    appendTurnLog("system", `已设置硬件 ${deviceId} 音量`, { volume: state.deviceVolume });
    showToast(`音量已设置为 ${state.deviceVolume}`, "success");
  } catch (error) {
    setBanner("#conv-banner", `音量设置失败：${error.message}`);
    appendTurnLog("error", `设置硬件 ${deviceId} 音量失败`, error.message);
    showToast(`音量设置失败：${error.message}`, "error");
  } finally {
    setButtonLoading("#conv-volume-apply", false);
  }
}

function handleDeviceVolumeInput() {
  const slider = qs("#conv-volume-slider");
  if (!slider) {
    return;
  }
  state.deviceVolume = Number(slider.value || "0") || 0;
  if (state.deviceVolume > 0) {
    state.previousDeviceVolume = state.deviceVolume;
  }
  updateDeviceVolumeControl();
}

function toggleDeviceMute() {
  const nextVolume = state.deviceVolume > 0 ? 0 : state.previousDeviceVolume || 85;
  state.deviceVolume = nextVolume;
  const slider = qs("#conv-volume-slider");
  if (slider) {
    slider.value = String(nextVolume);
  }
  updateDeviceVolumeControl();
  dispatchDeviceVolume(nextVolume);
}

function updateMonitorState() {
  const active = Boolean(state.monitorSource);
  setText("#conv-monitor-state", active ? `监看: ${state.monitorDeviceId}` : "监看: off");
  const button = qs("#conv-device-monitor");
  if (button) {
    button.textContent = active ? "停止监看" : "监看";
    button.setAttribute("aria-pressed", String(active));
    button.classList.toggle("primary", active);
    button.classList.toggle("secondary", !active);
  }
}

function stopDeviceMonitor() {
  if (state.monitorSource) {
    state.monitorSource.close();
    state.monitorSource = null;
  }
  state.monitorDeviceId = "";
  updateMonitorState();
}

function renderMonitorEvent(event) {
  const type = event.event_type || "";
  const payload = event.payload || {};
  const tag = `监看·${event.device_id || ""}`;
  if (type === "asr_result") {
    appendTurnLog("asr", `${tag} 语音识别`, payload.text || "");
  } else if (type === "llm_prompt") {
    appendTurnLog("prompt", `${tag} 模型输入`, payload, { expanded: false });
  } else if (type === "llm_reply_end") {
    appendTurnLog("output", `${tag} 模型回复`, payload.text || "");
  } else if (type === "text_input") {
    appendTurnLog("system", `${tag} 文本输入`, payload.text || "");
  } else if (type === "tts_start") {
    appendTurnLog("tts", `${tag} 开始播报`);
  } else if (type === "tts_end") {
    appendTurnLog("tts", `${tag} 播报结束`, payload.reason || "");
  }
}

function toggleDeviceMonitor() {
  if (state.monitorSource) {
    stopDeviceMonitor();
    showToast("已停止监看", "info");
    return;
  }
  const deviceId = selectedDeviceId();
  if (!deviceId) {
    showToast("没有可监看的在线硬件设备", "error");
    return;
  }
  const source = new EventSource(
    `/api/devices/${encodeURIComponent(deviceId)}/events`
  );
  state.monitorSource = source;
  state.monitorDeviceId = deviceId;
  updateMonitorState();
  appendTurnLog("system", `开始监看硬件 ${deviceId}`);
  source.addEventListener("message", (evt) => {
    try {
      renderMonitorEvent(JSON.parse(evt.data));
    } catch (error) {
      appendTurnLog("error", `监看事件解析失败`, error.message);
    }
  });
  source.addEventListener("error", () => {
    appendTurnLog("error", `监看连接中断：${deviceId}`);
    stopDeviceMonitor();
  });
}

async function loadUsers() {
  setBanner("#memory-banner", "");
  setMemoryControlsEnabled(false);

  try {
    const data = await fetchJson("/api/memory/users");
    const users = Array.isArray(data.users) ? data.users : [];
    const select = qs("#memory-user-select");
    const previousUser = state.selectedUser;
    select.innerHTML = "";

  if (!users.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "暂无用户";
      select.append(option);
      state.selectedUser = null;
      state.selectedMemoryItemId = null;
      resetMemoryView("暂无可查看的记忆");
      return;
    }

    users.forEach((userId) => {
      const option = document.createElement("option");
      option.value = userId;
      option.textContent = userId;
      select.append(option);
    });

    const nextUser = users.includes(previousUser) ? previousUser : users[0];
    select.value = nextUser;
    state.selectedUser = nextUser;
    setMemoryControlsEnabled(true);
    await loadMemoryForUser(nextUser);
  } catch (error) {
    state.selectedUser = null;
    state.selectedMemoryItemId = null;
    resetMemoryView("记忆数据加载失败");
    setBanner("#memory-banner", `记忆列表加载失败：${error.message}`);
    appendLog("error", `memory users load failed: ${error.message}`);
  }
}

async function loadMemoryForUser(userId) {
  if (!userId) {
    state.selectedUser = null;
    qs("#memory-object-list").innerHTML = "";
    setMemoryEditorMeta("请选择一位用户");
    setMemoryEditorValue("");
    updateMemoryDeleteControl();
    return;
  }

  state.selectedUser = userId;
  state.selectedMemoryItemId = null;
  setBanner("#memory-banner", "");
  await loadMemorySection();
}

async function loadMemorySection() {
  const userId = state.selectedUser;
  const section = activeMemorySection();
  if (!userId) {
    return;
  }
  try {
    if (section === "profile") {
      let profile = null;
      try {
        profile = await fetchJson(
          `/api/memory/users/${encodeURIComponent(userId)}/profile`
        );
      } catch (error) {
        const message = String(error && error.message ? error.message : error);
        if (message.includes("profile -> 404") && message.includes("profile not found")) {
          profile = { user_id: userId };
        } else {
          throw error;
        }
      }
      state.memoryPayloads.profile = profile;
      renderMemoryList([{ id: "profile", label: "用户画像", payload: profile }]);
      selectMemoryObject("profile", profile);
      return;
    }
    if (section === "bot-profile") {
      const botProfile = await fetchJson("/api/settings/bot-profile");
      state.memoryPayloads.botProfile = botProfile;
      renderMemoryList([
        { id: "bot-profile", label: "Bot 画像与回复 Prompt", payload: botProfile },
      ]);
      selectMemoryObject("bot-profile", botProfile);
      return;
    }
    if (section === "turns") {
      const data = await fetchJson(
        `/api/memory/users/${encodeURIComponent(userId)}/turns?limit=100`
      );
      state.memoryPayloads.turns = Array.isArray(data.turns) ? data.turns : [];
      renderMemoryList(
        state.memoryPayloads.turns.map((turn) => ({
          id: turn.turn_id,
          label: `${turn.sequence}. ${turn.user_text || turn.turn_id}`,
          payload: turn,
        }))
      );
      return;
    }
    if (section === "vocabulary") {
      const [vocabData, historyData] = await Promise.all([
        fetchJson(`/api/memory/users/${encodeURIComponent(userId)}/vocabulary`),
        fetchJson(
          `/api/memory/users/${encodeURIComponent(userId)}/vocabulary/history?limit=100`
        ),
      ]);
      const entries = Array.isArray(vocabData.vocabulary)
        ? vocabData.vocabulary
        : [];
      const history = Array.isArray(historyData.history)
        ? historyData.history
        : [];
      state.memoryPayloads.vocabulary = { entries, history };
      renderVocabularyMemoryList(entries, history);
      return;
    }
    if (section === "summaries") {
      const data = await fetchJson(
        `/api/memory/users/${encodeURIComponent(userId)}/summaries`
      );
      state.memoryPayloads.summaries = Array.isArray(data.summaries)
        ? data.summaries
        : [];
      renderMemoryList(
        state.memoryPayloads.summaries.map((summary) => ({
          id: summary.summary_id,
          label: `${summary.start_sequence}-${summary.end_sequence}`,
          payload: summary,
        }))
      );
      return;
    }
    if (section === "entries") {
      const data = await fetchJson(
        `/api/memory/users/${encodeURIComponent(userId)}/index`
      );
      state.memoryPayloads.entries = Array.isArray(data.entries)
        ? data.entries
        : [];
      renderMemoryList(
        state.memoryPayloads.entries.map((entry) => ({
          id: entry.id,
          label: entry.title || entry.id,
          payload: entry,
        }))
      );
      return;
    }
    if (section === "audit") {
      const data = await fetchJson(
        `/api/memory/users/${encodeURIComponent(userId)}/audit?limit=100`
      );
      state.memoryPayloads.audit = Array.isArray(data.events) ? data.events : [];
      renderMemoryList(
        state.memoryPayloads.audit.map((event) => ({
          id: event.event_id,
          label: `${event.event_type || "event"} ${event.created_at || ""}`,
          payload: event,
        }))
      );
    }
  } catch (error) {
    qs("#memory-object-list").innerHTML = "";
    setMemoryEditorMeta("记忆数据加载失败");
    setMemoryEditorValue("");
    updateMemoryDeleteControl();
    setBanner("#memory-banner", `记忆数据加载失败：${error.message}`);
    appendLog("error", `memory section load failed: ${error.message}`);
  }
}

function renderMemoryList(items) {
  const list = qs("#memory-object-list");
  list.innerHTML = "";
  if (!items.length) {
    setMemoryEditorMeta("当前分类暂无数据");
    setMemoryEditorValue("");
    state.selectedMemoryItemId = null;
    updateMemoryDeleteControl();
    return;
  }
  items.forEach((item, index) => {
    const node = document.createElement("li");
    node.className = "list-item";
    node.textContent = item.label;
    node.addEventListener("click", () =>
      selectMemoryObject(item.id, item.payload, node)
    );
    list.append(node);
    if (index === 0) {
      selectMemoryObject(item.id, item.payload, node);
    }
  });
}

function renderVocabularyMemoryList(entries, history) {
  const items = [];
  entries.forEach((entry) => {
    items.push({
      id: `word-${entry.character}`,
      label: `${entry.character} · ${entry.ask_count || 0} 次`,
      payload: { type: "vocabulary_entry", ...entry },
    });
  });
  history.forEach((event) => {
    items.push({
      id: event.event_id,
      label: `${event.character} · ${event.question || ""}`,
      payload: { type: "vocabulary_history", ...event },
    });
  });
  renderMemoryList(items);
}

function selectMemoryObject(id, payload, itemNode = null) {
  state.selectedMemoryItemId = id;
  qs("#memory-object-list")
    .querySelectorAll(".list-item")
    .forEach((node) => {
      node.classList.toggle("active", node === itemNode);
  });
  setMemoryEditorMeta(`${activeMemorySection()} / ${id}`);
  setMemoryEditorValue(payload);
  updateMemoryDeleteControl();
}

async function saveMemoryProfile() {
  const userId = state.selectedUser;
  const section = activeMemorySection();
  const id = state.selectedMemoryItemId;
  const payload = getMemoryEditorJson();
  if (section === "bot-profile") {
    await sendJson("/api/settings/bot-profile", "PUT", {
      enabled: Boolean(payload.enabled),
      name: payload.name || "",
      persona: payload.persona || "",
      response_style: payload.response_style || "",
      custom_prompt: payload.custom_prompt || "",
    });
  } else if (!userId) {
    return;
  } else if (section === "vocabulary") {
    setBanner("#memory-banner", "生词表当前为只读。");
    return;
  } else if (section === "profile") {
    await sendJson(
      `/api/memory/users/${encodeURIComponent(userId)}/profile`,
      "PATCH",
      payload
    );
  } else if (section === "turns") {
    await sendJson(
      `/api/memory/users/${encodeURIComponent(userId)}/turns/${encodeURIComponent(
        id
      )}`,
      "PATCH",
      payload
    );
  } else if (section === "summaries") {
    await sendJson(
      `/api/memory/users/${encodeURIComponent(userId)}/summaries/${encodeURIComponent(
        id
      )}`,
      "PATCH",
      {
        content: payload.content || "",
        summary_source: payload.summary_source || "manual",
      }
    );
  } else if (section === "entries") {
    await sendJson(
      `/api/memory/users/${encodeURIComponent(userId)}/entries/${encodeURIComponent(
        id
      )}`,
      "PATCH",
      payload
    );
  } else if (section === "audit") {
    await sendJson(
      `/api/memory/users/${encodeURIComponent(userId)}/audit/${encodeURIComponent(
        id
      )}`,
      "PATCH",
      payload
    );
  }
  setBanner("#memory-banner", "记忆对象已保存。");
  showToast("记忆对象已保存", "success");
  await loadMemorySection();
}

function confirmMemoryItemDeletion(section, id) {
  const sectionLabel = {
    turns: "对话轮次",
    summaries: "长期摘要",
    entries: "结构化记忆",
    audit: "审计日志",
  }[section] || section;
  return window.confirm(
    `确认删除当前选中的单条${sectionLabel}吗？\n\nID: ${id}\n此操作只删除当前选中项，无法在页面内撤销。`
  );
}

async function deleteSelectedMemoryItem() {
  const userId = state.selectedUser;
  const section = activeMemorySection();
  const id = state.selectedMemoryItemId;
  if (!userId || !id || ["profile", "bot-profile"].includes(section)) {
    setBanner("#memory-banner", "画像不支持整体删除，请编辑字段后保存。");
    updateMemoryDeleteControl();
    return;
  }
  if (!confirmMemoryItemDeletion(section, id)) {
    return;
  }
  const base = `/api/memory/users/${encodeURIComponent(userId)}`;
  const pathBySection = {
    turns: `${base}/turns/${encodeURIComponent(id)}`,
    summaries: `${base}/summaries/${encodeURIComponent(id)}`,
    entries: `${base}/entries/${encodeURIComponent(id)}`,
    audit: `${base}/audit/${encodeURIComponent(id)}`,
  };
  const response = await fetch(pathBySection[section], { method: "DELETE" });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(
      `${pathBySection[section]} -> ${response.status}${detail ? ` ${detail}` : ""}`
    );
  }
  setBanner("#memory-banner", "记忆对象已删除。");
  showToast("记忆对象已删除", "success");
  await loadMemorySection();
}

async function deleteAllTurns() {
  const userId = state.selectedUser;
  if (!userId) {
    setBanner("#memory-banner", "请先选择用户。");
    return;
  }
  if (activeMemorySection() !== "turns") {
    return;
  }
  const turns = state.memoryPayloads.turns;
  const count = Array.isArray(turns) ? turns.length : 0;
  if (count === 0) {
    setBanner("#memory-banner", "当前用户没有可删除的聊天记录。");
    return;
  }
  const confirmed = window.confirm(
    `确认删除用户「${userId}」的全部聊天记录吗？\n\n` +
      `共 ${count} 条对话轮次将被清空，旧文件会自动备份为 .bak，但页面内无法撤销。`
  );
  if (!confirmed) {
    return;
  }
  const url = `/api/memory/users/${encodeURIComponent(userId)}/turns`;
  const response = await fetch(url, { method: "DELETE" });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(
      `${url} -> ${response.status}${detail ? ` ${detail}` : ""}`
    );
  }
  const data = await response.json().catch(() => ({}));
  const deleted =
    typeof data.deleted_count === "number" ? data.deleted_count : count;
  setBanner("#memory-banner", `已删除全部聊天记录，共 ${deleted} 条。`);
  showToast(`已删除 ${deleted} 条聊天记录`, "success");
  await loadMemorySection();
}

function websocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws`;
}

function connectWebSocket() {
  if (
    state.socket &&
    (state.socket.readyState === WebSocket.CONNECTING ||
      state.socket.readyState === WebSocket.OPEN)
  ) {
    return;
  }

  setBanner("#ws-banner", "");
  const socket = new WebSocket(websocketUrl());
  state.socket = socket;
  updateDebugState();

  socket.addEventListener("open", () => {
    appendLog("incoming", "WebSocket connected");
    setBanner("#ws-banner", "");
    updateDebugState();
  });

  socket.addEventListener("error", () => {
    setBanner("#ws-banner", "WebSocket 连接失败，请确认服务已启动并允许 /ws 访问。");
    appendLog("error", "WebSocket error");
    updateDebugState();
  });

  socket.addEventListener("close", (event) => {
    appendLog("error", `WebSocket disconnected (${event.code})`);
    if (state.socket === socket) {
      state.socket = null;
    }
    state.authenticated = false;
    state.collectingAudio = false;
    if (event.code !== 1000) {
      setBanner("#ws-banner", `WebSocket 已断开，code=${event.code}`);
    }
    updateDebugState();
  });

  socket.addEventListener("message", (event) => {
    if (typeof event.data === "string") {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (error) {
        appendLog("error", `invalid JSON message: ${event.data}`);
        setBanner("#ws-banner", "收到无法解析的服务端消息。");
        return;
      }

      if (payload.type === "auth_ok") {
        state.authenticated = true;
        appendTurnLog("system", "鉴权成功 auth_ok");
      } else if (payload.type === "heartbeat") {
        appendTurnLog("system", "心跳 heartbeat");
      } else if (payload.type === "text_input_ack") {
        appendTurnLog("system", "文本输入已接收 text_input_ack", payload.text || "");
      } else if (payload.type === "error") {
        setBanner("#ws-banner", payload.message || "服务端返回错误。");
        setBanner("#conv-banner", payload.message || "语音链路返回错误。");
        appendTurnLog("error", payload.message || "服务端返回错误");
        if (payload.message === "auth failed") {
          state.authenticated = false;
        }
        if (payload.message === "audio_start required") {
          state.collectingAudio = false;
          state.speechRtc.vadState = "idle";
        }
        if (
          typeof payload.message === "string" &&
          payload.message.startsWith("speech turn failed:")
        ) {
          state.speechRtc.playbackState = "error";
        }
      } else if (payload.type === "asr_partial") {
        // Bidirectional streaming returns the full transcript so far on each
        // update, so overwrite the live caption instead of appending.
        const next = payload.text || "";
        if (next) {
          state.speechRtc.captions = next;
        }
      } else if (payload.type === "asr_result") {
        state.speechRtc.captions = payload.text || state.speechRtc.captions;
        if (payload.text) {
          // Voice bubble: badge marks it as a voice message, the ASR text is
          // shown as a transcript sub-line beneath the bubble.
          appendBubble({
            role: "user",
            kind: "voice",
            text: "🎙 语音消息",
            transcript: payload.text,
          });
        }
        appendTurnLog("asr", "识别结果 asr_result", payload.text || "");
        setBanner("#conv-banner", "");
      } else if (payload.type === "llm_prompt") {
        // llm_prompt marks the start of a new turn on the server side
        state.conversation.turnNo += 1;
        appendTurnLog("prompt", "发送给模型的输入", payload.payload || {}, {
          expanded: false,
        });
      } else if (payload.type === "llm_reply") {
        state.lastReply = payload.text || "--";
        const currentAssistantBubble = state.conversation.currentAssistantBubble;
        attachTtsToAssistantBubble(currentAssistantBubble, {
          turnId: payload.turn_id || "",
        });
        const tag = [payload.source, payload.detail].filter(Boolean).join(" | ");
        appendTurnLog(
          "output",
          `完整回复${tag ? ` [${tag}]` : ""}`,
          payload.text || "--"
        );
        state.speechRtc.captions = payload.text || state.speechRtc.captions;
        // de-dupe: llm_reply repeats the streamed text; only fill bubble if no stream happened
        if (payload.text && payload.text !== state.conversation.streamedText) {
          const bubble = getOrCreateAssistantBubble();
          attachTtsToAssistantBubble(bubble, {
            turnId: payload.turn_id || "",
          });
          updateBubble(bubble, payload.text, { streaming: false });
          state.conversation.streamedText = payload.text;
        }
      } else if (payload.type === "llm_reply_start") {
        state.wsStreamingReply = "";
        state.conversation.currentAssistantBubble = null;
        state.conversation.streamedText = "";
        getOrCreateAssistantBubble();
        const tag = [payload.source, payload.detail].filter(Boolean).join(" | ");
        appendTurnLog("output", `开始流式输出${tag ? ` [${tag}]` : ""}`);
      } else if (payload.type === "llm_reply_delta") {
        state.wsStreamingReply += payload.text || "";
        state.lastReply = state.wsStreamingReply || "--";
        state.speechRtc.captions = state.wsStreamingReply || state.speechRtc.captions;
        const bubble = getOrCreateAssistantBubble();
        updateBubble(bubble, state.wsStreamingReply, { streaming: true });
      } else if (payload.type === "llm_reply_end") {
        state.lastReply = payload.text || state.wsStreamingReply || "--";
        state.conversation.streamedText = payload.text || state.wsStreamingReply || "";
        const bubble = getOrCreateAssistantBubble();
        updateBubble(bubble, state.conversation.streamedText, { streaming: false });
        const tag = [payload.source, payload.detail].filter(Boolean).join(" | ");
        appendTurnLog(
          "output",
          `流式输出结束${tag ? ` [${tag}]` : ""}`,
          state.lastReply
        );
        state.speechRtc.captions = state.lastReply || state.speechRtc.captions;
      } else if (payload.type === "tts_start") {
        ensurePlaybackAudioContextSync();
        setAssistantPlaying(true);
        if (payload.source === "local") {
          setBanner(
            "#conv-banner",
            "TTS 未配置火山上游，当前播放的是本地调试音调（嘟声）。请到「模型配置」页填入 TTS Access Token 后再试。"
          );
          showToast("TTS 未配置上游，仅播放调试嘟声", "info");
          appendTurnLog(
            "tts",
            "TTS 走本地兜底（local），仅生成调试音调",
            "请在「模型配置」页配置火山 TTS Access Token / App ID / Voice Type 后真实语音才会读出文字"
          );
        } else {
          appendTurnLog("tts", "开始语音合成 tts_start", { source: payload.source || "real" });
        }
      } else if (payload.type === "tts_end") {
        attachTtsToAssistantBubble(state.conversation.currentAssistantBubble, {
          turnId: payload.turn_id || "",
          ttsUrl: payload.tts_url || "",
          ttsAudioFormat: payload.tts_audio_format || "",
          ttsSampleRateHz: payload.tts_sample_rate_hz || 0,
        });
        setAssistantPlaying(false);
        state.collectingAudio = false;
        if (typeof payload.latency_ms === "number") {
          state.speechRtc.lastBargeInLatencyMs = payload.latency_ms;
        }
        if (state.speechRtc.lastBargeInAt) {
          state.speechRtc.lastBargeInLatencyMs =
            Date.now() - state.speechRtc.lastBargeInAt;
          state.speechRtc.lastBargeInAt = 0;
        }
        if (payload.reason === "barge_in") {
          setBanner("#conv-banner", "已打断当前播报，等待新一轮输入。");
          stopPlayback("interrupted");
        } else if (payload.reason === "error") {
          setBanner("#conv-banner", "语音播报失败，已停止当前播放。");
          stopPlayback("error");
        } else if (payload.reason === "natural_end") {
          setBanner("#conv-banner", "当前语音播报完成。");
        } else {
          stopPlayback("idle");
        }
        const latency =
          typeof payload.latency_ms === "number"
            ? ` | ${payload.latency_ms}ms`
            : "";
        appendTurnLog(
          "tts",
          `语音合成结束 tts_end [${payload.reason || "end"}${latency}]`
        );
      }
    } else {
      // Treat binary frames from server as PCM s16le mono and use runtime TTS sample rate.
      event.data.arrayBuffer().then((buf) => {
        schedulePcmPlayback(buf);
      });
    }
    updateDebugState();
    updateSpeechRtcState();
  });
}

function disconnectWebSocket() {
  if (state.socket) {
    state.socket.close(1000, "manual disconnect");
  }
}

function waitForSocketOpen(timeoutMs = 3000) {
  if (state.socket && state.socket.readyState === WebSocket.OPEN) {
    return Promise.resolve(true);
  }
  if (!state.socket || state.socket.readyState !== WebSocket.CONNECTING) {
    return Promise.resolve(false);
  }
  return new Promise((resolve) => {
    const socket = state.socket;
    let done = false;
    const finish = (value) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      socket.removeEventListener("open", onOpen);
      socket.removeEventListener("error", onError);
      socket.removeEventListener("close", onError);
      resolve(value);
    };
    const onOpen = () => finish(true);
    const onError = () => finish(false);
    const timer = setTimeout(() => finish(false), timeoutMs);
    socket.addEventListener("open", onOpen);
    socket.addEventListener("error", onError);
    socket.addEventListener("close", onError);
  });
}

function waitForAuthOk(timeoutMs = 3000) {
  if (state.authenticated) {
    return Promise.resolve(true);
  }
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
    return Promise.resolve(false);
  }
  return new Promise((resolve) => {
    const socket = state.socket;
    let done = false;
    const finish = (value) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      socket.removeEventListener("message", onMessage);
      socket.removeEventListener("error", onError);
      socket.removeEventListener("close", onError);
      resolve(value);
    };
    const onMessage = (event) => {
      if (typeof event.data !== "string") return;
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "auth_ok") {
          finish(true);
        }
        if (payload.type === "error" && payload.message === "auth failed") {
          finish(false);
        }
      } catch (error) {
        // ignore non-control payloads; the main message handler records them.
      }
    };
    const onError = () => finish(false);
    const timer = setTimeout(() => finish(Boolean(state.authenticated)), timeoutMs);
    socket.addEventListener("message", onMessage);
    socket.addEventListener("error", onError);
    socket.addEventListener("close", onError);
  });
}

async function ensureSpeechSocketReady() {
  connectWebSocket();
  const ready = await waitForSocketOpen();
  state.speechRtc.wsState = websocketStateLabel();
  updateSpeechRtcState();
  if (!ready) {
    return false;
  }
  if (!state.authenticated) {
    const sent = sendControl({
      type: "auth",
      device_id: qs("#device-id").value || "web-console",
      token: resolveAuthToken("#auth-token"),
    });
    if (!sent) {
      return false;
    }
    return await waitForAuthOk();
  }
  return true;
}

function sendControl(payload) {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
    setBanner("#ws-banner", "WebSocket 未连接，无法发送控制消息。");
    appendLog("error", "WebSocket not connected");
    return false;
  }
  state.socket.send(JSON.stringify(payload));
  appendLog("outgoing", payload);
  setBanner("#ws-banner", "");
  return true;
}

function sendBytes() {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
    setBanner("#ws-banner", "WebSocket 未连接，无法发送 mock bytes。");
    appendLog("error", "WebSocket not connected");
    return;
  }
  const text = qs("#mock-text").value;
  const bytes = new TextEncoder().encode(text);
  state.socket.send(bytes);
  appendLog("outgoing", `binary frame: ${bytes.byteLength} bytes`);
}

// ===========================================================================
// Device simulator: a browser-side ESP32-S3 emulator. Opens its own /ws as a
// hardware device_id, mirrors the firmware UI state machine + half-duplex
// conversation logic (conversation.c / vad.c / ui_state.c), so the full voice
// loop and remote commands (say / set_volume / set_conversation) can be tested
// before flashing. No backend change: any non-"web-console" device_id is
// treated as hardware.
// ===========================================================================
const sim = {
  socket: null,
  audioContext: null, // capture context
  mediaStream: null,
  sourceNode: null,
  processorNode: null,
  heartbeatTimer: null,
  screen: "offline",
  cloud: "disconnected",
  authed: false,
  convOn: false,
  captureEnabled: false, // half-duplex gate: false while thinking/playing
  duplexMode: "full", // "half" | "full" | "wake" — barge-in behaviour during playback
  character: "princess", // device screen character (backend setting), default princess
  bargeGuardUntil: 0, // ignore barge-in for a moment after playback starts
  volume: 0,
  subtitle: "",
  // VAD
  vadActive: false,
  vadLastVoiceAt: 0,
  vadThreshold: 0.02,
  vadSilenceMs: 700,
  preRoll: [],
  // playback: one long-lived AudioContext at the hardware default rate; each
  // TTS frame is decoded into a buffer at its true PCM rate (playbackSampleRate)
  // and WebAudio resamples on output, so the many small paced frames from the
  // backend play back-to-back without speed/pitch distortion ("电音") or
  // per-chunk clicks (see _send_tts_audio_bytes in server.py).
  playbackContext: null,
  playbackSources: [],
  playbackBaseTime: 0,   // context time of the current playback run's start
  playbackCursorFrames: 0, // integer PCM-rate samples scheduled so far (no float drift)
  playbackSampleRate: 16000, // PCM rate of the current TTS stream (from tts_start)
  trace: {
    active: false,
    completed: false,
    character: "",
    strokes: [],
    outlines: null,
    durationMs: 1800,
    strokeIndex: 0,
    pointIndex: 0,
    tickTimer: null,
    startedAt: 0,
  },
};

const TRACE_GRID_COLOR = "#ff0000";
const TRACE_BG_COLOR = "#000000";
const TRACE_GRID_SIZE = 200;
const TRACE_GRID_X = (240 - TRACE_GRID_SIZE) / 2;
const TRACE_GRID_Y = 42;

function simLog(category, detail) {
  appendTurnLog(category, `[sim] ${category}`, detail);
  const container = qs("#sim-log");
  if (container) {
    const line = document.createElement("div");
    line.className = `log-line ${category}`;
    const text = typeof detail === "string" ? detail : JSON.stringify(detail);
    line.textContent = `${new Date().toLocaleTimeString()}  ${text}`;
    container.prepend(line);
  }
}

const SIM_SCREEN_CN = {
  offline: "未连接",
  boot: "启动中",
  wifi: "连接网络",
  cloud: "连接云端",
  idle: "点我开始对话",
  recording: "我在听",
  thinking: "想一想",
  playback: "我在说",
  error: "出错了",
};

let simBlinkTimer = null;
// Deliberate blink as interaction feedback (tap / state change). Restarts the
// one-shot CSS animation by toggling the class with a reflow in between.
function simBlink() {
  const mock = qs("#sim-screen-mock");
  if (!mock || !sim.authed) {
    return;
  }
  mock.classList.remove("blinking");
  void mock.offsetWidth; // force reflow so the animation re-triggers
  mock.classList.add("blinking");
  if (simBlinkTimer) {
    clearTimeout(simBlinkTimer);
  }
  simBlinkTimer = setTimeout(() => mock.classList.remove("blinking"), 280);
}

function simSetScreen(screen) {
  const changed = sim.screen !== screen;
  sim.screen = screen;
  simRender();
  if (changed) {
    simBlink(); // blink each time the cat reacts to a new state
  }
}

function simRender() {
  setText("#sim-screen", sim.screen);
  setText("#sim-screen-title", SIM_SCREEN_CN[sim.screen] || "");
  // Drive the cat pet on the device-screen mock (CSS animations key off data-state).
  // The whole screen is the conversation toggle (tap the cat), like the real
  // touch LCD; status + subtitle render small inside the screen.
  const mock = qs("#sim-screen-mock");
  if (mock) {
    mock.setAttribute("data-state", sim.screen);
    mock.setAttribute("data-character", sim.character);
    mock.classList.toggle("is-clickable", sim.authed);
    mock.classList.toggle("is-active", sim.convOn);
  }
  setText("#sim-state-cn", SIM_SCREEN_CN[sim.screen] || "");
  setText("#sim-screen-subtitle", sim.subtitle || "");
  setText(
    "#sim-tap-hint",
    !sim.authed ? "未上电" : sim.convOn ? "点我结束对话" : "点我说话"
  );
  setText("#sim-cloud-state", sim.cloud);
  setText("#sim-conv-state", sim.convOn ? "on" : "off");
  const duplexLabel = { half: "半双工", full: "全双工", wake: "唤醒词" }[sim.duplexMode] || sim.duplexMode;
  setText("#sim-mic-state", `mic: ${sim.captureEnabled ? "capturing" : "idle"} · ${duplexLabel}`);
  const wakeBtn = qs("#sim-wake");
  if (wakeBtn) {
    // Wake button is the simulated WakeNet trigger; only meaningful in wake mode.
    wakeBtn.disabled = !(sim.authed && sim.duplexMode === "wake");
  }
  setText("#sim-volume", String(sim.volume));
  const connect = qs("#sim-connect");
  const disconnect = qs("#sim-disconnect");
  if (connect) connect.disabled = Boolean(sim.socket);
  if (disconnect) disconnect.disabled = !sim.socket;

  // Trace canvas preview can work even without a live /ws connection.
  renderTraceCanvas("#trace-canvas");
}

function simWsUrl() {
  const base = location.origin.replace(/^http/, "ws");
  return `${base}/ws`;
}

function simConnect() {
  if (sim.socket) {
    return;
  }
  const deviceId = (qs("#sim-device-id").value || "esp32-sim-001").trim();
  if (deviceId === "web-console") {
    setBanner("#sim-banner", "device_id 不能为 web-console（那是控制台身份）。");
    return;
  }
  setBanner("#sim-banner", "");
  sim.authed = false;
  sim.cloud = "connecting";
  simSetScreen("boot");

  let socket;
  try {
    socket = new WebSocket(simWsUrl());
  } catch (error) {
    setBanner("#sim-banner", `无法建立 WebSocket：${error.message}`);
    return;
  }
  socket.binaryType = "arraybuffer";
  sim.socket = socket;
  simRender();

  socket.addEventListener("open", () => {
    sim.cloud = "connected";
    simSetScreen("cloud");
    socket.send(
      JSON.stringify({
        type: "auth",
        device_id: deviceId,
        token: resolveAuthToken("#sim-token"),
        transport: "wifi_direct",
      })
    );
    simLog("outgoing", `auth device_id=${deviceId}`);
    sim.heartbeatTimer = setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "heartbeat" }));
      }
    }, 15000);
  });

  socket.addEventListener("message", (event) => {
    if (typeof event.data === "string") {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (error) {
        return;
      }
      simHandleServerMessage(payload);
    } else {
      simSchedulePcm(event.data);
    }
  });

  socket.addEventListener("close", () => {
    simLog("system", "socket closed");
    simTeardown();
  });
  socket.addEventListener("error", () => {
    setBanner("#sim-banner", "WebSocket 错误。");
    simLog("error", "websocket error");
  });
}

function simDisconnect() {
  if (sim.convOn) {
    simSetConversation(false, "manual");
  }
  if (sim.socket) {
    try {
      sim.socket.close();
    } catch (error) {
      // ignore
    }
  }
  simTeardown();
}

function simTeardown() {
  if (sim.heartbeatTimer) {
    clearInterval(sim.heartbeatTimer);
    sim.heartbeatTimer = null;
  }
  simStopCapture();
  simStopPlayback();
  if (sim.playbackContext) {
    try { sim.playbackContext.close(); } catch (e) {}
    sim.playbackContext = null;
  }
  sim.socket = null;
  sim.authed = false;
  sim.cloud = "disconnected";
  sim.convOn = false;
  simSetScreen("offline");
}

function simHandleServerMessage(payload) {
  const type = payload.type;
  if (type !== "heartbeat") {
    simLog("incoming", payload);
  }
  switch (type) {
    case "auth_ok":
      sim.authed = true;
      simSetScreen("idle");
      break;
    case "error":
      simSetScreen("error");
      setBanner("#sim-banner", `server error: ${payload.message || ""}`);
      // Server error frees a stuck turn; resume listening if still in a call.
      if (sim.convOn) {
        simEnterListening();
      }
      break;
    case "set_volume":
      sim.volume = Math.max(0, Math.min(100, Number(payload.volume) || 0));
      simRender();
      break;
    case "set_conversation":
      // Remote toggle (M6). Drive the same path as a local button press.
      simSetConversation(Boolean(payload.enabled), "remote");
      break;
    case "asr_result":
      sim.subtitle = payload.text || "";
      simSetScreen("thinking");
      break;
    case "llm_reply_start":
      sim.subtitle = "";
      simSetScreen("thinking");
      break;
    case "llm_reply_delta":
      sim.subtitle += payload.text || "";
      simSetScreen("thinking");
      break;
    case "llm_reply_end":
    case "llm_reply":
      if (payload.text) sim.subtitle = payload.text;
      simRender();
      break;
    case "tts_start":
      sim.playbackSampleRate =
        Number(payload.tts_sample_rate_hz) || sim.playbackSampleRate || 16000;
      sim.vadActive = false;
      if (sim.duplexMode === "full" && sim.convOn) {
        // Full-duplex (phase 1): keep listening during playback; the browser's
        // echoCancellation removes the speaker echo so the bot's own voice
        // doesn't self-trigger. Guard the first 250ms against AEC transients.
        sim.captureEnabled = true;
        sim.bargeGuardUntil = Date.now() + 250;
      } else {
        // Half-duplex / wake: mic off during playback. In wake mode the only
        // way to interrupt is the (simulated WakeNet) 唤醒词 button.
        sim.captureEnabled = false;
      }
      simSetScreen("playback");
      break;
    case "show_character_trace":
      if (payload.payload || payload.character) {
        simStartTrace(payload.payload || payload);
      }
      break;
    case "show_color_test":
      simLog("info", `LCD color test: ${payload.color || "bars"}`);
      break;
    case "tts_end":
      if (payload.reason === "barge_in") {
        simStopPlayback();
      }
      if (!sim.convOn) {
        simSetScreen("idle");
      } else if (!sim.vadActive) {
        // Not mid-utterance: re-arm listening for the next turn. (If a local
        // barge-in already started a new utterance, leave it streaming.)
        simEnterListening();
      }
      break;
    case "barge_in":
      simStopPlayback();
      break;
    default:
      break;
  }
  simRender();
}

function normalizeTracePayload(payload) {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const character = String(payload.character || "").trim();
  const strokes = Array.isArray(payload.strokes) ? payload.strokes : [];
  const durationMs = Number(payload.duration_ms) || Number(payload.durationMs) || 1800;
  if (!character || !strokes.length) {
    return null;
  }
  // 楷体填充轮廓（真正正楷）；同序对应 strokes（中线）。缺失时回退到中线骨架渲染。
  const outlines =
    Array.isArray(payload.outlines) && payload.outlines.length === strokes.length
      ? payload.outlines
      : null;
  return {
    character,
    strokes,
    outlines,
    durationMs: Math.max(300, Math.min(12000, durationMs)),
  };
}

function simStopTrace() {
  if (sim.trace.tickTimer) {
    clearInterval(sim.trace.tickTimer);
    sim.trace.tickTimer = null;
  }
  sim.trace.active = false;
  sim.trace.completed = false;
  sim.trace.character = "";
  sim.trace.strokes = [];
  sim.trace.outlines = null;
  sim.trace.durationMs = 1200;
  sim.trace.strokeIndex = 0;
  sim.trace.pointIndex = 0;
  sim.trace.startedAt = 0;
}

function simStartTrace(payload) {
  const normalized = normalizeTracePayload(payload);
  if (!normalized) {
    simLog("warn", "invalid show_character_trace payload");
    return;
  }
  simStopTrace();
  sim.trace.active = true;
  sim.trace.completed = false;
  sim.trace.character = normalized.character;
  sim.trace.strokes = normalized.strokes;
  sim.trace.outlines = normalized.outlines;
  sim.trace.durationMs = Math.min(normalized.durationMs, 1200);
  sim.trace.strokeIndex = 0;
  sim.trace.pointIndex = 0;
  sim.trace.startedAt = Date.now();

  const totalSegments = sim.trace.strokes.reduce((acc, stroke) => {
    const pts = Array.isArray(stroke) ? stroke.length : 0;
    return acc + Math.max(0, pts - 1);
  }, 0);
  if (totalSegments <= 0) {
    sim.trace.completed = true;
    simRender();
    return;
  }
  let tick = Math.floor(sim.trace.durationMs / totalSegments);
  tick = Math.max(20, Math.min(160, tick));
  sim.trace.tickTimer = setInterval(() => {
    if (!sim.trace.active || sim.trace.completed) {
      return;
    }
    const stroke = sim.trace.strokes[sim.trace.strokeIndex];
    const len = Array.isArray(stroke) ? stroke.length : 0;
    if (len < 2) {
      sim.trace.strokeIndex += 1;
      sim.trace.pointIndex = 0;
    } else {
      sim.trace.pointIndex += 1;
      if (sim.trace.pointIndex + 1 >= len) {
        sim.trace.strokeIndex += 1;
        sim.trace.pointIndex = 0;
      }
    }
    if (sim.trace.strokeIndex >= sim.trace.strokes.length) {
      sim.trace.completed = true;
      if (sim.trace.tickTimer) {
        clearInterval(sim.trace.tickTimer);
        sim.trace.tickTimer = null;
      }
    }
    simRender();
  }, tick);
  simRender();
}

function traceCanvasCtx(selector) {
  const canvas = qs(selector);
  if (!canvas) {
    return null;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return null;
  }
  return { canvas, ctx };
}

function traceMapPoint(point) {
  const x = Math.max(0, Math.min(1000, Number(point?.[0]) || 0));
  const y = Math.max(0, Math.min(1000, Number(point?.[1]) || 0));
  const px = TRACE_GRID_X + (x / 1000) * TRACE_GRID_SIZE;
  const py = TRACE_GRID_Y + (y / 1000) * TRACE_GRID_SIZE;
  return { x: px, y: py };
}

function traceOutlinePath(ctx, outline) {
  // Build a single path covering all contours of one stroke (even-odd handles holes).
  ctx.beginPath();
  for (const contour of outline) {
    if (!Array.isArray(contour) || contour.length < 3) {
      continue;
    }
    const p0 = traceMapPoint(contour[0]);
    ctx.moveTo(p0.x, p0.y);
    for (let k = 1; k < contour.length; k += 1) {
      const p = traceMapPoint(contour[k]);
      ctx.lineTo(p.x, p.y);
    }
    ctx.closePath();
  }
}

function fillStrokeOutline(ctx, outline, color, alpha) {
  ctx.save();
  ctx.fillStyle = color;
  ctx.globalAlpha = alpha;
  traceOutlinePath(ctx, outline);
  ctx.fill("evenodd");
  ctx.restore();
}

function revealStrokeOutline(ctx, outline, median, uptoPointIndex) {
  if (!Array.isArray(median) || median.length < 2) {
    fillStrokeOutline(ctx, outline, "#ffffff", 1.0);
    return;
  }
  ctx.save();
  // Clip to the filled 楷体 stroke, then sweep a thick line along the median:
  // the swept band, clipped to the outline, paints the written part of the
  // stroke up to the current progress — the "brush writing" reveal.
  traceOutlinePath(ctx, outline);
  ctx.clip("evenodd");
  ctx.strokeStyle = "#ffffff";
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.lineWidth = 40;
  ctx.beginPath();
  const p0 = traceMapPoint(median[0]);
  ctx.moveTo(p0.x, p0.y);
  const last = Math.min(median.length - 1, uptoPointIndex + 1);
  for (let k = 1; k <= last; k += 1) {
    const p = traceMapPoint(median[k]);
    ctx.lineTo(p.x, p.y);
  }
  ctx.stroke();
  ctx.restore();
}

function renderTraceKaiti(ctx, outlines, medians) {
  // Written strokes in solid white, by stroke order; current stroke sweeps.
  for (let si = 0; si < outlines.length; si += 1) {
    const outline = outlines[si];
    if (!Array.isArray(outline) || !outline.length) {
      continue;
    }
    if (sim.trace.completed || si < sim.trace.strokeIndex) {
      fillStrokeOutline(ctx, outline, "#ffffff", 1.0);
    } else if (si === sim.trace.strokeIndex) {
      revealStrokeOutline(ctx, outline, medians[si], sim.trace.pointIndex);
    }
  }
}

function renderTraceCanvas(selector) {
  const env = traceCanvasCtx(selector);
  if (!env) {
    return;
  }
  const { ctx, canvas } = env;
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  ctx.fillStyle = TRACE_BG_COLOR;
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  ctx.save();
  ctx.strokeStyle = TRACE_GRID_COLOR;
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.rect(TRACE_GRID_X, TRACE_GRID_Y, TRACE_GRID_SIZE, TRACE_GRID_SIZE);
  ctx.stroke();
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(TRACE_GRID_X + TRACE_GRID_SIZE / 2, TRACE_GRID_Y);
  ctx.lineTo(TRACE_GRID_X + TRACE_GRID_SIZE / 2, TRACE_GRID_Y + TRACE_GRID_SIZE);
  ctx.moveTo(TRACE_GRID_X, TRACE_GRID_Y + TRACE_GRID_SIZE / 2);
  ctx.lineTo(TRACE_GRID_X + TRACE_GRID_SIZE, TRACE_GRID_Y + TRACE_GRID_SIZE / 2);
  ctx.moveTo(TRACE_GRID_X, TRACE_GRID_Y);
  ctx.lineTo(TRACE_GRID_X + TRACE_GRID_SIZE, TRACE_GRID_Y + TRACE_GRID_SIZE);
  ctx.moveTo(TRACE_GRID_X + TRACE_GRID_SIZE, TRACE_GRID_Y);
  ctx.lineTo(TRACE_GRID_X, TRACE_GRID_Y + TRACE_GRID_SIZE);
  ctx.stroke();
  ctx.restore();

  if (!sim.trace.active || !Array.isArray(sim.trace.strokes) || !sim.trace.strokes.length) {
    return;
  }

  // 真正正楷：用楷体填充轮廓（outlines）渲染。按笔顺把已写笔画以白色实心填出；
  // 当前笔画沿中线扫描显影。
  const outlines = sim.trace.outlines;
  if (Array.isArray(outlines) && outlines.length === sim.trace.strokes.length) {
    renderTraceKaiti(ctx, outlines, sim.trace.strokes);
    return;
  }

  const strokeCount = sim.trace.strokes.length;
  for (let si = 0; si < strokeCount; si += 1) {
    const stroke = sim.trace.strokes[si];
    if (!Array.isArray(stroke) || stroke.length < 2) {
      continue;
    }
    const maxSegment =
      sim.trace.completed
        ? stroke.length - 2
        : si < sim.trace.strokeIndex
        ? stroke.length - 2
        : si === sim.trace.strokeIndex
        ? Math.max(0, sim.trace.pointIndex - 1)
        : -1;
    if (maxSegment < 0) {
      continue;
    }
    ctx.save();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 11;
    ctx.globalAlpha = 1.0;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    for (let pi = 0; pi <= maxSegment; pi += 1) {
      const p0 = traceMapPoint(stroke[pi]);
      const p1 = traceMapPoint(stroke[pi + 1]);
      ctx.beginPath();
      ctx.moveTo(p0.x, p0.y);
      ctx.lineTo(p1.x, p1.y);
      ctx.stroke();
    }
    ctx.restore();
  }
}

async function fetchTracePayload(character) {
  const ch = String(character || "").trim();
  if (!ch) {
    throw new Error("请输入单个汉字");
  }
  const response = await fetch(`/api/trace/preview?character=${encodeURIComponent(ch)}`);
  if (!response.ok) {
    let detail = "";
    try {
      const data = await response.json();
      detail = data?.detail ? String(data.detail) : "";
    } catch (e) {}
    throw new Error(detail || `请求失败：${response.status}`);
  }
  const data = await response.json();
  const payload = data?.payload || null;
  if (!payload) {
    throw new Error("该字暂无描红数据");
  }
  return payload;
}

async function tracePreviewWeb() {
  const input = qs("#trace-input");
  const ch = (input && input.value.trim()) || "";
  setButtonLoading("#trace-preview-web", true);
  try {
    const payload = await fetchTracePayload(ch);
    simStartTrace(payload);
    showToast(`Web 预览：${payload.character}`, "success");
  } catch (error) {
    showToast(`描红预览失败：${error.message}`, "error");
  } finally {
    setButtonLoading("#trace-preview-web", false);
  }
}

async function traceDispatchHardware() {
  const input = qs("#trace-input");
  const ch = (input && input.value.trim()) || "";
  const select = qs("#trace-device-select");
  const deviceId = (select && select.value) || "";
  if (!deviceId) {
    showToast("没有可下发的在线硬件设备", "error");
    return;
  }
  setButtonLoading("#trace-dispatch-hardware", true);
  try {
    await sendJson(`/api/devices/${encodeURIComponent(deviceId)}/trace`, "POST", {
      character: ch,
    });
    showToast(`已下发描红到 ${deviceId}`, "success");
  } catch (error) {
    showToast(`下发描红失败：${error.message}`, "error");
  } finally {
    setButtonLoading("#trace-dispatch-hardware", false);
  }
}

function populateTraceDeviceSelect(hardware) {
  const select = qs("#trace-device-select");
  if (!select) {
    return;
  }
  const previous = select.value;
  select.innerHTML = "";
  if (!hardware.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "无在线硬件";
    select.append(option);
    return;
  }
  hardware.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.device_id;
    option.textContent = item.device_id;
    select.append(option);
  });
  select.value = hardware.some((item) => item.device_id === previous) ? previous : hardware[0].device_id;
}

// ===== 生字管理（批量录入 + 田字格陈列 + 下发描红） =====

function uniqueCharacters(characters) {
  const seen = new Set();
  const result = [];
  for (const ch of characters || []) {
    const value = String(ch || "").trim();
    if (value && !seen.has(value)) {
      seen.add(value);
      result.push(value);
    }
  }
  return result;
}

function normalizeSections(sections, allChars) {
  if (!Array.isArray(sections) || !sections.length) return [];
  const charSet = new Set(allChars);
  return sections
    .filter((s) => s && typeof s === "object" && typeof s.title === "string")
    .map((s) => ({
      title: s.title.trim(),
      characters: uniqueCharacters(s.characters).filter((ch) => charSet.has(ch)),
    }))
    .filter((s) => s.title);
}

function normalizeVocabGroups(groups) {
  const source = groups && typeof groups === "object" ? groups : {};
  const normalized = {};
  for (const [name, characters] of Object.entries(source)) {
    const label = String(name || "").trim();
    if (!label) {
      continue;
    }
    if (Array.isArray(characters)) {
      normalized[label] = {
        characters: uniqueCharacters(characters),
        statuses: {},
        sections: [],
      };
    } else {
      const chars = uniqueCharacters(characters && characters.characters);
      normalized[label] = {
        characters: chars,
        statuses: normalizeVocabStatuses(
          characters && characters.statuses,
          chars
        ),
        sections: normalizeSections(characters && characters.sections, chars),
      };
    }
  }
  if (!normalized[VOCAB_DEFAULT_GROUP_NAME]) {
    normalized[VOCAB_DEFAULT_GROUP_NAME] = { characters: [], statuses: {}, sections: [] };
  }
  return normalized;
}

function normalizeVocabStatus(value) {
  if (value === VOCAB_STATUS_LEARNED) return VOCAB_STATUS_LEARNED;
  if (value === VOCAB_STATUS_NEW) return VOCAB_STATUS_NEW;
  return VOCAB_STATUS_UNLEARNED;
}

function normalizeVocabStatuses(statuses, characters) {
  const result = {};
  const source = statuses && typeof statuses === "object" ? statuses : {};
  for (const ch of characters || []) {
    result[ch] = normalizeVocabStatus(source[ch]);
  }
  return result;
}

function vocabGlobalStatuses() {
  const merged = {};
  for (const group of Object.values(state.vocab.groups)) {
    if (group.statuses) {
      for (const [ch, s] of Object.entries(group.statuses)) {
        const ns = normalizeVocabStatus(s);
        if (!merged[ch] || ns !== VOCAB_STATUS_UNLEARNED) merged[ch] = ns;
      }
    }
  }
  return merged;
}

function syncActiveVocabCharacters() {
  const groupName = state.vocab.activeGroup || VOCAB_DEFAULT_GROUP_NAME;
  state.vocab.groups = normalizeVocabGroups(state.vocab.groups);
  state.vocab.activeGroup = state.vocab.groups[groupName]
    ? groupName
    : VOCAB_DEFAULT_GROUP_NAME;
  const global = vocabGlobalStatuses();
  const group = state.vocab.groups[state.vocab.activeGroup];
  for (const ch of group.characters) {
    if (global[ch]) group.statuses[ch] = global[ch];
  }
  group.statuses = normalizeVocabStatuses(group.statuses, group.characters);
  state.vocab.characters = group.characters;
  state.vocab.statuses = group.statuses;
}

function currentVocabGroupsPayload() {
  return {
    active_group: state.vocab.activeGroup || VOCAB_DEFAULT_GROUP_NAME,
    active_status_filter: state.vocab.activeStatusFilter || "all",
    groups: normalizeVocabGroups(state.vocab.groups),
    prompt_template: state.vocab.promptTemplate || "",
  };
}

function isDefaultEmptyVocabPayload(payload) {
  if (!payload || typeof payload !== "object") {
    return true;
  }
  const groups = normalizeVocabGroups(payload.groups);
  const names = Object.keys(groups);
  return (
    names.length === 1 &&
    names[0] === VOCAB_DEFAULT_GROUP_NAME &&
    groups[VOCAB_DEFAULT_GROUP_NAME].characters.length === 0
  );
}

function applyVocabGroupsPayload(payload) {
  const groups = normalizeVocabGroups(payload && payload.groups);
  state.vocab.groups = groups;
  state.vocab.activeGroup =
    groups[payload && payload.active_group]
      ? payload.active_group
      : VOCAB_DEFAULT_GROUP_NAME;
  state.vocab.activeStatusFilter =
    payload && ["all", VOCAB_STATUS_LEARNED, VOCAB_STATUS_UNLEARNED, VOCAB_STATUS_NEW].includes(payload.active_status_filter)
      ? payload.active_status_filter
      : "all";
  state.vocab.promptTemplate = (payload && payload.prompt_template) || "";
  const ptEl = qs("#vocab-prompt-template");
  if (ptEl) ptEl.value = state.vocab.promptTemplate;
  syncActiveVocabCharacters();
  renderVocabGroupSelect();
  renderVocabStatusFilter();
}

function persistVocabGroupsLocally() {
  try {
    const payload = currentVocabGroupsPayload();
    localStorage.setItem(
      VOCAB_GROUPS_STORAGE_KEY,
      JSON.stringify({
        activeGroup: payload.active_group,
        activeStatusFilter: payload.active_status_filter,
        groups: payload.groups,
      })
    );
  } catch (error) {
    // localStorage may be unavailable in private mode; the current session still works.
  }
}

let vocabPersistTimer = null;

async function persistVocabGroupsToServer() {
  if (!state.vocab.serverLoaded) {
    return;
  }
  try {
    await sendJson("/api/vocab/groups", "PUT", currentVocabGroupsPayload());
  } catch (error) {
    showToast(`生字状态保存到服务端失败：${error.message}`, "error");
  }
}

function persistVocabGroups() {
  persistVocabGroupsLocally();
  if (vocabPersistTimer) {
    clearTimeout(vocabPersistTimer);
  }
  vocabPersistTimer = setTimeout(() => {
    vocabPersistTimer = null;
    persistVocabGroupsToServer();
  }, 250);
}

function readLocalVocabGroupsPayload() {
  let stored = null;
  try {
    stored = JSON.parse(localStorage.getItem(VOCAB_GROUPS_STORAGE_KEY) || "null");
  } catch (error) {
    stored = null;
  }
  return {
    active_group: stored && stored.activeGroup,
    active_status_filter: stored && stored.activeStatusFilter,
    groups: stored && stored.groups,
  };
}

function loadVocabGroups() {
  applyVocabGroupsPayload(readLocalVocabGroupsPayload());
}

async function loadVocabGroupsFromServer() {
  const localPayload = readLocalVocabGroupsPayload();
  try {
    const serverPayload = await fetchJson("/api/vocab/groups");
    const shouldMigrateLocal =
      isDefaultEmptyVocabPayload(serverPayload) && !isDefaultEmptyVocabPayload(localPayload);
    applyVocabGroupsPayload(shouldMigrateLocal ? localPayload : serverPayload);
    state.vocab.serverLoaded = true;
    persistVocabGroupsLocally();
    if (shouldMigrateLocal) {
      await persistVocabGroupsToServer();
    }
    renderVocabGrid();
    if (state.vocab.characters.length) {
      refreshVocabAvailability(state.vocab.characters);
    }
  } catch (error) {
    state.vocab.serverLoaded = false;
    showToast(`生字状态从服务端加载失败，已使用本机缓存：${error.message}`, "warn");
  }
}

function renderVocabGroupSelect() {
  const select = qs("#vocab-group-select");
  if (!select) {
    return;
  }
  const previous = state.vocab.activeGroup || VOCAB_DEFAULT_GROUP_NAME;
  select.innerHTML = "";
  Object.keys(normalizeVocabGroups(state.vocab.groups)).forEach((name) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    select.append(option);
  });
  select.value = state.vocab.groups[previous] ? previous : VOCAB_DEFAULT_GROUP_NAME;
}

function renderVocabStatusFilter() {
  const select = qs("#vocab-status-filter");
  if (!select) {
    return;
  }
  select.value = state.vocab.activeStatusFilter || "all";
}

function vocabStatusFilter() {
  return state.vocab.activeStatusFilter || "all";
}

function vocabCharacterColor(character) {
  const s = normalizeVocabStatus(state.vocab.statuses[character]);
  if (s === VOCAB_STATUS_LEARNED) return VOCAB_LEARNED_COLOR;
  if (s === VOCAB_STATUS_NEW) return VOCAB_NEW_COLOR;
  return VOCAB_UNLEARNED_COLOR;
}

function vocabSwitchGroup(groupName) {
  state.vocab.activeGroup = state.vocab.groups[groupName]
    ? groupName
    : VOCAB_DEFAULT_GROUP_NAME;
  syncActiveVocabCharacters();
  persistVocabGroups();
  renderVocabGroupSelect();
  renderVocabStatusFilter();
  renderVocabGrid();
  if (state.vocab.characters.length) {
    refreshVocabAvailability(state.vocab.characters);
  }
}

function vocabCreateGroup() {
  const input = qs("#vocab-group-name");
  const name = String((input && input.value) || "").trim();
  if (!name) {
    setVocabBanner("请输入分组名称。");
    return;
  }
  state.vocab.groups = normalizeVocabGroups(state.vocab.groups);
  if (!state.vocab.groups[name]) {
    state.vocab.groups[name] = { characters: [], statuses: {} };
  }
  if (input) {
    input.value = "";
  }
  setVocabBanner("");
  vocabSwitchGroup(name);
  showToast(`已切换到分组「${name}」`, "success");
}

function vocabDeleteGroup() {
  const name = state.vocab.activeGroup;
  if (name === VOCAB_DEFAULT_GROUP_NAME) {
    showToast("默认分组不可删除", "error");
    return;
  }
  const count = (state.vocab.characters || []).length;
  const msg = count
    ? `确定删除分组「${name}」及其中 ${count} 个生字吗？此操作不可撤销。`
    : `确定删除空分组「${name}」吗？`;
  if (!confirm(msg)) return;
  delete state.vocab.groups[name];
  vocabSwitchGroup(VOCAB_DEFAULT_GROUP_NAME);
  showToast(`已删除分组「${name}」`, "success");
}

function vocabAddCharactersToGroup(characters, groupName = state.vocab.activeGroup) {
  const targetGroup = groupName || VOCAB_DEFAULT_GROUP_NAME;
  state.vocab.groups = normalizeVocabGroups(state.vocab.groups);
  if (!state.vocab.groups[targetGroup]) {
    state.vocab.groups[targetGroup] = { characters: [], statuses: {} };
  }
  const group = state.vocab.groups[targetGroup];
  const list = group.characters;
  let added = 0;
  for (const ch of characters) {
    if (!list.includes(ch)) {
      list.push(ch);
      group.statuses[ch] = VOCAB_STATUS_UNLEARNED;
      added += 1;
    }
  }
  if (targetGroup === state.vocab.activeGroup) {
    state.vocab.characters = list;
    state.vocab.statuses = group.statuses;
  }
  persistVocabGroups();
  renderVocabGroupSelect();
  return added;
}

// 从任意文本中抽取「汉字」字符（基本汉字区 + 扩展A），按出现顺序去重。
function extractHanzi(text) {
  const matched = String(text || "").match(/[一-鿿㐀-䶿]/g) || [];
  const seen = new Set();
  const result = [];
  for (const ch of matched) {
    if (!seen.has(ch)) {
      seen.add(ch);
      result.push(ch);
    }
  }
  return result;
}

function setVocabBanner(message) {
  const banner = qs("#vocab-banner");
  if (!banner) {
    return;
  }
  if (message) {
    banner.textContent = message;
    banner.hidden = false;
  } else {
    banner.textContent = "";
    banner.hidden = true;
  }
}

// 高 DPR 屏（手机/Retina）田字格位图按 devicePixelRatio 放大，避免发虚。
// 布局仍以 CSS 像素计算：返回的 w/h 是 CSS 像素，context 已按 dpr 缩放，
// 因此所有绘制坐标（含 0..1000 字形映射）继续用 CSS 像素，无需改动映射逻辑。
function setupVocabCanvas(canvas) {
  const dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
  const cssW = Number(canvas.dataset.cssWidth) || 120;
  const cssH = Number(canvas.dataset.cssHeight) || 120;
  const bw = Math.round(cssW * dpr);
  const bh = Math.round(cssH * dpr);
  if (canvas.width !== bw) {
    canvas.width = bw;
  }
  if (canvas.height !== bh) {
    canvas.height = bh;
  }
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  return { ctx, w: cssW, h: cssH };
}

// 把田字格画到一个 canvas 上：背景 + 红色十字虚线 + 红色对角虚线 + 居中字。
// 无字形数据的字以灰色绘制，便于一眼区分。
function renderVocabCell(canvas, character, available, color = VOCAB_UNLEARNED_COLOR) {
  const { ctx, w, h } = setupVocabCanvas(canvas);
  if (!ctx) {
    return;
  }
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = TRACE_BG_COLOR;
  ctx.fillRect(0, 0, w, h);

  ctx.save();
  ctx.strokeStyle = TRACE_GRID_COLOR;
  ctx.lineWidth = 2;
  // 外框（实线）
  ctx.strokeRect(1, 1, w - 2, h - 2);
  // 十字 + 对角线（虚线）
  ctx.setLineDash([6, 5]);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(w / 2, 0);
  ctx.lineTo(w / 2, h);
  ctx.moveTo(0, h / 2);
  ctx.lineTo(w, h / 2);
  ctx.moveTo(0, 0);
  ctx.lineTo(w, h);
  ctx.moveTo(w, 0);
  ctx.lineTo(0, h);
  ctx.stroke();
  ctx.restore();

  ctx.save();
  ctx.fillStyle = available ? color : "#777777";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.font = `${Math.floor(h * 0.62)}px "Kaiti SC", "STKaiti", Kaiti, "楷体", "KaiTi", serif`;
  ctx.fillText(character, w / 2, h / 2 + 2);
  ctx.restore();
}

function renderVocabCellWithGlyph(canvas, character) {
  const color = vocabCharacterColor(character);
  const { ctx, w, h } = setupVocabCanvas(canvas);
  if (!ctx) return;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = TRACE_BG_COLOR;
  ctx.fillRect(0, 0, w, h);
  ctx.save();
  ctx.strokeStyle = TRACE_GRID_COLOR;
  ctx.lineWidth = 2;
  ctx.strokeRect(1, 1, w - 2, h - 2);
  ctx.setLineDash([6, 5]);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(w / 2, 0); ctx.lineTo(w / 2, h);
  ctx.moveTo(0, h / 2); ctx.lineTo(w, h / 2);
  ctx.moveTo(0, 0); ctx.lineTo(w, h);
  ctx.moveTo(w, 0); ctx.lineTo(0, h);
  ctx.stroke();
  ctx.restore();
  const glyph = state.vocab.glyphs[character];
  if (!glyph || !drawVocabGlyphStatic(ctx, glyph, w, h, color)) {
    ctx.save();
    ctx.fillStyle = color;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = `${Math.floor(h * 0.62)}px "Kaiti SC", "STKaiti", Kaiti, "楷体", "KaiTi", serif`;
    ctx.fillText(character, w / 2, h / 2 + 2);
    ctx.restore();
  }
}

function drawVocabGlyphStatic(ctx, payload, w, h, color = VOCAB_UNLEARNED_COLOR) {
  const normalized = normalizeTracePayload(payload);
  if (!normalized || !normalized.strokes || !normalized.strokes.length) {
    return false;
  }
  const medians = normalized.strokes;
  const outlines = normalized.outlines;
  const pad = 6;
  const map = (pt) => ({
    x: pad + (Math.max(0, Math.min(1000, Number(pt?.[0]) || 0)) / 1000) * (w - 2 * pad),
    y: pad + (Math.max(0, Math.min(1000, Number(pt?.[1]) || 0)) / 1000) * (h - 2 * pad),
  });
  ctx.save();
  ctx.fillStyle = color;
  ctx.strokeStyle = color;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  if (Array.isArray(outlines) && outlines.length === medians.length) {
    // 楷体轮廓：逐笔整笔填充（与描红完成态一致）。
    for (const outline of outlines) {
      if (!Array.isArray(outline) || !outline.length) {
        continue;
      }
      ctx.beginPath();
      for (const contour of outline) {
        if (!Array.isArray(contour) || contour.length < 3) {
          continue;
        }
        const p0 = map(contour[0]);
        ctx.moveTo(p0.x, p0.y);
        for (let k = 1; k < contour.length; k += 1) {
          const p = map(contour[k]);
          ctx.lineTo(p.x, p.y);
        }
        ctx.closePath();
      }
      ctx.fill("evenodd");
    }
  } else {
    // 无轮廓：中线骨架整字描出（与描红骨架回退一致）。
    ctx.lineWidth = (w / 240) * 14;
    for (const median of medians) {
      if (!Array.isArray(median) || median.length < 2) {
        continue;
      }
      ctx.beginPath();
      const p0 = map(median[0]);
      ctx.moveTo(p0.x, p0.y);
      for (let k = 1; k < median.length; k += 1) {
        const p = map(median[k]);
        ctx.lineTo(p.x, p.y);
      }
      ctx.stroke();
    }
  }
  ctx.restore();
  return true;
}

function buildVocabCellElement(ch) {
  const available = state.vocab.availability[ch];
  const status = normalizeVocabStatus(state.vocab.statuses[ch]);
  const cell = document.createElement("div");
  cell.className = "vocab-cell";
  cell.dataset.status = status;
  if (available === false) cell.classList.add("is-unavailable");

  const canvas = document.createElement("canvas");
  canvas.dataset.cssWidth = "120";
  canvas.dataset.cssHeight = "120";
  canvas.width = 120;
  canvas.height = 120;
  canvas.className = "vocab-cell-canvas";
  canvas.dataset.vocabChar = ch;
  canvas.dataset.vocabAvailable = available !== false ? "1" : "0";
  cell.append(canvas);
  if (available !== false) {
    canvas.classList.add("is-clickable");
    canvas.title = "点击预览描红并下发到硬件";
    canvas.addEventListener("click", () => vocabPreviewAndDispatch(ch, canvas));
  }

  const explain = state.vocab.explanations[ch];
  if (explain && explain.word) {
    const wordTag = document.createElement("div");
    wordTag.className = "vocab-cell-word";
    wordTag.textContent = explain.word;
    wordTag.style.color = vocabCharacterColor(ch);
    cell.append(wordTag);
  }

  const statusControls = document.createElement("div");
  statusControls.className = "vocab-status-control";
  statusControls.setAttribute("aria-label", `设置「${ch}」学习状态`);
  [
    { value: VOCAB_STATUS_UNLEARNED, label: "生字" },
    { value: VOCAB_STATUS_NEW, label: "不熟" },
    { value: VOCAB_STATUS_LEARNED, label: "已会" },
  ].forEach((item) => {
    const button = document.createElement("button");
    button.className = "vocab-status-button";
    button.type = "button";
    button.dataset.status = item.value;
    button.textContent = item.label;
    button.setAttribute("aria-pressed", String(status === item.value));
    button.disabled = state.vocab.dispatching;
    button.addEventListener("click", () => vocabSetCharacterStatus(ch, item.value));
    statusControls.append(button);
  });
  cell.append(statusControls);

  const explainBox = document.createElement("div");
  explainBox.className = "vocab-cell-explain";
  const textarea = document.createElement("textarea");
  textarea.className = "vocab-cell-explain-text";
  textarea.rows = 3;
  textarea.placeholder = "尚未生成解读，点「生成解读」由 AI 编写组词与拆解写法。";
  textarea.value = explain ? explain.explanation || "" : "";
  textarea.disabled = state.vocab.dispatching;
  textarea.addEventListener("input", () => {
    const current = state.vocab.explanations[ch] || {};
    state.vocab.explanations[ch] = { ...current, explanation: textarea.value };
  });
  explainBox.append(textarea);
  cell.append(explainBox);

  const actions = document.createElement("div");
  actions.className = "vocab-cell-actions";

  const genBtn = document.createElement("button");
  genBtn.className = "button secondary vocab-cell-generate";
  genBtn.type = "button";
  genBtn.textContent = state.vocab.generating[ch]
    ? "生成中…"
    : explain
      ? "重新生成"
      : "生成解读";
  genBtn.disabled =
    available === false || state.vocab.dispatching || Boolean(state.vocab.generating[ch]);
  genBtn.addEventListener("click", () => vocabGenerateOne(ch, { regenerate: Boolean(explain) }));
  actions.append(genBtn);

  if (explain && explain.hasTts) {
    const previewBtn = document.createElement("button");
    previewBtn.className = "button secondary vocab-cell-preview";
    previewBtn.type = "button";
    previewBtn.textContent = "试听";
    previewBtn.disabled = state.vocab.dispatching;
    previewBtn.addEventListener("click", () => vocabPreviewTts(ch, previewBtn));
    actions.append(previewBtn);
  }

  const dispatchBtn = document.createElement("button");
  dispatchBtn.className = "button secondary vocab-cell-dispatch";
  dispatchBtn.type = "button";
  dispatchBtn.textContent = "下发";
  dispatchBtn.disabled = available === false || state.vocab.dispatching;
  dispatchBtn.addEventListener("click", () => vocabDispatchOne(ch));
  actions.append(dispatchBtn);

  const removeBtn = document.createElement("button");
  removeBtn.className = "button secondary vocab-cell-remove";
  removeBtn.type = "button";
  removeBtn.textContent = "删除";
  removeBtn.disabled = state.vocab.dispatching;
  removeBtn.addEventListener("click", () => vocabConfirmRemove(ch));
  actions.append(removeBtn);

  cell.append(actions);
  return cell;
}

function renderVocabGrid() {
  const grid = qs("#vocab-grid");
  if (!grid) {
    return;
  }
  const chars = state.vocab.characters;
  const filter = vocabStatusFilter();
  const visibleSet = new Set(
    chars.filter(
      (ch) => filter === "all" || normalizeVocabStatus(state.vocab.statuses[ch]) === filter
    )
  );
  const visibleCount = visibleSet.size;
  const empty = qs("#vocab-empty");
  const count = qs("#vocab-count");
  if (count) {
    count.textContent = chars.length
      ? filter === "all"
        ? `（${chars.length} 字）`
        : `（${visibleCount}/${chars.length} 字）`
      : "";
  }
  if (empty) {
    empty.hidden = visibleCount > 0;
    empty.textContent = chars.length
      ? "当前状态筛选下暂无生字。"
      : "暂无生字，先在上方批量录入或导入已问过的生词。";
  }
  grid.innerHTML = "";

  const group = state.vocab.groups[state.vocab.activeGroup];
  const sections = (group && group.sections) || [];

  const appendSection = (title, sectionChars) => {
    const wrapper = document.createElement("div");
    wrapper.className = "vocab-section-block";
    if (title) {
      const header = document.createElement("div");
      header.className = "vocab-section-header";
      header.textContent = title;
      wrapper.append(header);
    }
    const cellGrid = document.createElement("div");
    cellGrid.className = "vocab-cell-grid";
    for (const ch of sectionChars) {
      cellGrid.append(buildVocabCellElement(ch));
    }
    wrapper.append(cellGrid);
    grid.append(wrapper);
  };

  if (sections.length) {
    const rendered = new Set();
    for (const section of sections) {
      const sectionChars = (section.characters || []).filter(
        (ch) => visibleSet.has(ch) && !rendered.has(ch)
      );
      if (!sectionChars.length) continue;
      sectionChars.forEach((ch) => rendered.add(ch));
      appendSection(section.title, sectionChars);
    }
    const unsectioned = chars.filter((ch) => visibleSet.has(ch) && !rendered.has(ch));
    if (unsectioned.length) {
      appendSection(rendered.size > 0 ? "其他" : "", unsectioned);
    }
  } else {
    const all = chars.filter((ch) => visibleSet.has(ch));
    if (all.length) appendSection("", all);
  }
  vocabLazyRenderCanvases();
}

let vocabCanvasObserver = null;

function vocabRenderOneCanvas(canvas) {
  const ch = canvas.dataset.vocabChar;
  if (!ch || canvas.dataset.vocabRendered) return;
  canvas.dataset.vocabRendered = "1";
  renderVocabCell(canvas, ch, canvas.dataset.vocabAvailable === "1", vocabCharacterColor(ch));
}

function vocabLazyRenderCanvases() {
  if (vocabCanvasObserver) vocabCanvasObserver.disconnect();
  const grid = qs("#vocab-grid");
  if (!grid) return;
  const canvases = grid.querySelectorAll("canvas[data-vocab-char]");
  if (!canvases.length) return;
  const vh = window.innerHeight;
  const deferred = [];
  for (const c of canvases) {
    const rect = c.getBoundingClientRect();
    if (rect.top < vh + 200 && rect.bottom > -200) {
      vocabRenderOneCanvas(c);
    } else {
      deferred.push(c);
    }
  }
  if (!deferred.length) return;
  vocabCanvasObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        vocabCanvasObserver.unobserve(entry.target);
        vocabRenderOneCanvas(entry.target);
      }
    },
    { rootMargin: "200px 0px" }
  );
  for (const c of deferred) vocabCanvasObserver.observe(c);
}

// 查询某字是否有字形数据（带缓存），更新 availability 后局部刷新。
// 同时缓存描红字形 payload（含 outlines），供静态展示按描红同一套字形渲染。
async function refreshVocabAvailability(characters) {
  const pending = characters.filter((ch) => state.vocab.availability[ch] === undefined);
  if (!pending.length) return;
  const BATCH = 20;
  for (let i = 0; i < pending.length; i += BATCH) {
    const batch = pending.slice(i, i + BATCH);
    await Promise.all(batch.map(async (ch) => {
      try {
        const response = await fetch(`/api/trace/preview?character=${encodeURIComponent(ch)}`);
        if (!response.ok) {
          state.vocab.availability[ch] = false;
          return;
        }
        const data = await response.json();
        state.vocab.availability[ch] = Boolean(data && data.available);
      } catch (e) {
        state.vocab.availability[ch] = false;
      }
    }));
    renderVocabGrid();
  }
}

// 为单个字调用后端预生成「组词 + 拆解写法解读」，存入 state 供编辑 / 下发。
async function vocabGenerateOne(character, { regenerate = false } = {}) {
  if (state.vocab.generating[character] || state.vocab.dispatching) {
    return;
  }
  state.vocab.generating[character] = true;
  renderVocabGrid();
  try {
    const body = { character, regenerate };
    if (state.vocab.promptTemplate) body.prompt_template = state.vocab.promptTemplate;
    const data = await sendJson("/api/vocab/explanation", "POST", body);
    state.vocab.explanations[character] = {
      word: (data && data.word) || "",
      explanation: (data && data.explanation) || "",
      source: (data && data.source) || "",
      hasTts: Boolean(data && data.has_tts),
      ttsUrl: (data && data.tts_url) || "",
      ttsSampleRateHz: (data && data.tts_sample_rate_hz) || 0,
    };
    if (data && data.cached) {
      // silently restored from cache
    } else if (data && data.source === "fallback") {
      showToast(`「${character}」用了离线模板，请检查模型配置后重试`, "warn");
    } else if (data && data.tts_detail) {
      showToast(`「${character}」解读已生成，但语音合成失败（检查 TTS 配置）`, "warn");
    } else {
      showToast(`已生成「${character}」的解读与语音`, "success");
    }
  } catch (error) {
    showToast(`生成「${character}」解读失败：${error.message}`, "error");
  } finally {
    state.vocab.generating[character] = false;
    renderVocabGrid();
  }
}

// 试听：拉取该字缓存的 TTS 音频（audio/L16 裸 PCM），用 WebAudio 播放。
async function vocabPreviewTts(character, button) {
  const explain = state.vocab.explanations[character];
  const ttsUrl = (explain && explain.ttsUrl) || `/api/vocab/${encodeURIComponent(character)}/tts`;
  if (button) {
    button.disabled = true;
    button.classList.add("is-loading");
  }
  try {
    const response = await fetch(ttsUrl);
    if (!response.ok) {
      throw new Error(`${response.status}`);
    }
    const sampleRate =
      Number(
        (explain && explain.ttsSampleRateHz) ||
          response.headers.get("X-TTS-Sample-Rate-Hz") ||
          state.speechSettings.ttsSampleRateHz ||
          16000
      ) || 16000;
    await ensurePlaybackAudioContext();
    stopPlayback("idle");
    schedulePcmPlayback(await response.arrayBuffer(), { sampleRateHz: sampleRate });
  } catch (error) {
    const msg = /404/.test(String(error.message)) ? "暂无缓存语音，请先生成解读" : error.message;
    showToast(`试听「${character}」失败：${msg}`, "error");
  } finally {
    if (button) {
      button.disabled = state.vocab.dispatching;
      button.classList.remove("is-loading");
    }
  }
}

// 点击田字格：在本格 canvas 上播放楷体逐笔描红动画（纯 web，不依赖真机）。
async function vocabPreviewTrace(character, canvas) {
  if (state.vocab.tracing && state.vocab.tracing[character]) {
    return;
  }
  state.vocab.tracing = state.vocab.tracing || {};
  state.vocab.tracing[character] = true;
  try {
    const payload = await fetchTracePayload(character);
    state.vocab.glyphs[character] = payload;
    runCellTraceAnimation(canvas, payload, () => {
      state.vocab.tracing[character] = false;
      renderVocabCellWithGlyph(canvas, character);
    });
  } catch (error) {
    state.vocab.tracing[character] = false;
    showToast(`描红预览失败：${error.message}`, "error");
  }
}

async function vocabPreviewAndDispatch(character, canvas) {
  vocabPreviewTrace(character, canvas);
  await vocabDispatchOne(character, { silentNoDevice: true });
}

// 自包含的单格描红动画引擎：在给定 canvas（120×120）上按笔顺逐笔揭示楷体字形。
// 与全局 #trace-canvas 的 simStartTrace 互不干扰（后者绑定固定 canvas / sim 单例）。
function runCellTraceAnimation(canvas, payload, onDone) {
  const normalized = normalizeTracePayload(payload);
  if (!normalized || !normalized.strokes || !normalized.strokes.length) {
    if (onDone) onDone();
    return;
  }
  const { ctx, w, h } = setupVocabCanvas(canvas);
  if (!ctx) {
    if (onDone) onDone();
    return;
  }
  const medians = normalized.strokes || [];
  // 有楷体填充轮廓时按轮廓揭示；否则回退到中线骨架描线。
  const outlines = normalized.outlines || medians.map(() => null);
  const total = outlines.length;
  // 坐标系：字形点位为 0..1000，映射到整格 canvas（留 6px 边距）。
  const pad = 6;
  const map = (pt) => ({
    x: pad + (Math.max(0, Math.min(1000, Number(pt?.[0]) || 0)) / 1000) * (w - 2 * pad),
    y: pad + (Math.max(0, Math.min(1000, Number(pt?.[1]) || 0)) / 1000) * (h - 2 * pad),
  });
  const drawGrid = () => {
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = TRACE_BG_COLOR;
    ctx.fillRect(0, 0, w, h);
    ctx.save();
    ctx.strokeStyle = TRACE_GRID_COLOR;
    ctx.lineWidth = 2;
    ctx.strokeRect(1, 1, w - 2, h - 2);
    ctx.setLineDash([6, 5]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(w / 2, 0);
    ctx.lineTo(w / 2, h);
    ctx.moveTo(0, h / 2);
    ctx.lineTo(w, h / 2);
    ctx.stroke();
    ctx.restore();
  };
  const outlinePath = (outline) => {
    ctx.beginPath();
    for (const contour of outline) {
      if (!Array.isArray(contour) || contour.length < 3) {
        continue;
      }
      const p0 = map(contour[0]);
      ctx.moveTo(p0.x, p0.y);
      for (let k = 1; k < contour.length; k += 1) {
        const p = map(contour[k]);
        ctx.lineTo(p.x, p.y);
      }
      ctx.closePath();
    }
  };
  const fillStroke = (outline) => {
    ctx.save();
    ctx.fillStyle = "#ffffff";
    outlinePath(outline);
    ctx.fill("evenodd");
    ctx.restore();
  };
  const revealStroke = (outline, median, upto) => {
    if (!Array.isArray(median) || median.length < 2) {
      fillStroke(outline);
      return;
    }
    ctx.save();
    outlinePath(outline);
    ctx.clip("evenodd");
    ctx.strokeStyle = "#ffffff";
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.lineWidth = (w / 240) * 40;
    ctx.beginPath();
    const p0 = map(median[0]);
    ctx.moveTo(p0.x, p0.y);
    const last = Math.min(median.length - 1, upto + 1);
    for (let k = 1; k <= last; k += 1) {
      const p = map(median[k]);
      ctx.lineTo(p.x, p.y);
    }
    ctx.stroke();
    ctx.restore();
  };
  const drawSkeletonStroke = (median, upto) => {
    ctx.save();
    ctx.strokeStyle = "#ffffff";
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.lineWidth = (w / 240) * 14;
    ctx.beginPath();
    const p0 = map(median[0]);
    ctx.moveTo(p0.x, p0.y);
    const last = Math.min(median.length - 1, upto + 1);
    for (let k = 1; k <= last; k += 1) {
      const p = map(median[k]);
      ctx.lineTo(p.x, p.y);
    }
    ctx.stroke();
    ctx.restore();
  };
  let strokeIndex = 0;
  let pointIndex = 0;
  const totalSegments = medians.reduce(
    (acc, s) => acc + Math.max(0, (Array.isArray(s) ? s.length : 0) - 1),
    0
  );
  let tick = Math.floor(Math.min(normalized.durationMs || 2400, 1200) / Math.max(1, totalSegments));
  tick = Math.max(16, Math.min(120, tick));
  const render = () => {
    drawGrid();
    for (let si = 0; si < total; si += 1) {
      const outline = outlines[si];
      const median = medians[si];
      if (Array.isArray(outline) && outline.length) {
        // 楷体轮廓路径
        if (si < strokeIndex) {
          fillStroke(outline);
        } else if (si === strokeIndex) {
          revealStroke(outline, median, pointIndex);
        }
      } else if (Array.isArray(median) && median.length >= 2) {
        // 无轮廓：用中线骨架描线（已完成的整笔 + 当前笔进度）。
        const upto = si < strokeIndex ? median.length - 1 : si === strokeIndex ? pointIndex : -1;
        if (upto >= 0) {
          drawSkeletonStroke(median, upto);
        }
      }
    }
  };
  render();
  const timer = setInterval(() => {
    const median = medians[strokeIndex];
    const len = Array.isArray(median) ? median.length : 0;
    if (len < 2) {
      strokeIndex += 1;
      pointIndex = 0;
    } else {
      pointIndex += 1;
      if (pointIndex + 1 >= len) {
        strokeIndex += 1;
        pointIndex = 0;
      }
    }
    if (strokeIndex >= total) {
      clearInterval(timer);
      render();
      if (onDone) onDone();
      return;
    }
    render();
  }, tick);
}

// 打开页面时从后端加载已生成的解读记录（含 TTS 可用性），恢复网格。
async function vocabLoadExplanations() {
  try {
    const data = await fetchJson("/api/vocab/explanations");
    const items = Array.isArray(data.explanations) ? data.explanations : [];
    const restored = [];
    for (const item of items) {
      const ch = String(item && item.character ? item.character : "").trim();
      if (!ch) {
        continue;
      }
      state.vocab.explanations[ch] = {
        word: item.word || "",
        explanation: item.explanation || "",
        source: item.source || "",
        hasTts: Boolean(item.has_tts),
        ttsUrl: item.tts_url || "",
        ttsSampleRateHz: item.tts_sample_rate_hz || 0,
      };
      restored.push(ch);
    }
    if (restored.length) {
      vocabAddCharactersToGroup(restored, VOCAB_DEFAULT_GROUP_NAME);
      syncActiveVocabCharacters();
      renderVocabGroupSelect();
      renderVocabGrid();
      if (state.vocab.characters.length) {
        refreshVocabAvailability(state.vocab.characters);
      }
    }
  } catch (error) {
    // 加载历史记录失败不阻塞页面；用户仍可新建。
  }
}

// 批量生成：对所有有字形、尚未生成解读的字依次预生成（节流），用于一键准备。
async function vocabGenerateAll() {
  if (state.vocab.dispatching) {
    return;
  }
  const targets = state.vocab.characters.filter(
    (ch) => state.vocab.availability[ch] !== false && !state.vocab.explanations[ch]
  );
  if (!targets.length) {
    showToast("没有需要生成解读的字（均已生成或无字形）", "info");
    return;
  }
  setButtonLoading("#vocab-generate-all", true);
  const status = qs("#vocab-dispatch-status");
  let ok = 0;
  let fail = 0;
  for (let i = 0; i < targets.length; i += 1) {
    const ch = targets[i];
    if (status) {
      status.textContent = `生成解读 ${i + 1}/${targets.length}：${ch}`;
    }
    await vocabGenerateOne(ch);
    if (state.vocab.explanations[ch] && state.vocab.explanations[ch].explanation) {
      ok += 1;
    } else {
      fail += 1;
    }
  }
  setButtonLoading("#vocab-generate-all", false);
  if (status) {
    status.textContent = `解读生成完成：成功 ${ok}，失败 ${fail}`;
  }
  showToast(`批量生成完成：成功 ${ok}，失败 ${fail}`, fail ? "warn" : "success");
}

function vocabAddCharacters(characters) {
  syncActiveVocabCharacters();
  const added = vocabAddCharactersToGroup(characters, state.vocab.activeGroup);
  syncActiveVocabCharacters();
  renderVocabGrid();
  if (characters.length) {
    refreshVocabAvailability(state.vocab.characters);
  }
  return added;
}

function parseVocabInputSections(text) {
  const lines = String(text || "").split(/\r?\n/);
  const sections = [];
  let currentTitle = "";
  let currentChars = [];
  for (const line of lines) {
    const headingMatch = line.match(/^#{1,6}\s+(.+)/);
    if (headingMatch) {
      if (currentChars.length) {
        sections.push({ title: currentTitle, characters: currentChars });
      }
      currentTitle = headingMatch[1].trim();
      currentChars = [];
    } else {
      const chars = extractHanzi(line);
      currentChars.push(...chars);
    }
  }
  if (currentChars.length) {
    sections.push({ title: currentTitle, characters: currentChars });
  }
  return sections;
}

function vocabAddFromInput() {
  const input = qs("#vocab-input");
  const text = (input && input.value) || "";
  const parsed = parseVocabInputSections(text);
  const allChars = parsed.flatMap((s) => s.characters);
  if (!allChars.length) {
    setVocabBanner("没有识别到汉字，请粘贴或输入包含汉字的文本。");
    return;
  }
  setVocabBanner("");

  const hasSections = parsed.some((s) => s.title);

  syncActiveVocabCharacters();
  const group = state.vocab.groups[state.vocab.activeGroup];

  if (hasSections) {
    const globalSeen = new Set();
    const newSections = [];
    for (const s of parsed) {
      const deduped = [];
      for (const ch of s.characters) {
        if (!globalSeen.has(ch)) {
          globalSeen.add(ch);
          deduped.push(ch);
        }
      }
      if (deduped.length) {
        newSections.push({ title: s.title, characters: deduped });
      }
    }
    const newCharOrder = newSections.flatMap((s) => s.characters);
    const oldOnly = (group.characters || []).filter((ch) => !globalSeen.has(ch));
    group.characters = [...newCharOrder, ...oldOnly];
    for (const ch of newCharOrder) {
      if (!(ch in group.statuses)) group.statuses[ch] = VOCAB_STATUS_UNLEARNED;
    }
    const oldSections = (group.sections || []).filter(
      (s) => !newSections.some((ns) => ns.title === s.title)
    );
    const remainOldSections = oldSections.map((s) => ({
      title: s.title,
      characters: s.characters.filter((ch) => !globalSeen.has(ch)),
    })).filter((s) => s.characters.length);
    group.sections = [...newSections, ...remainOldSections];
    state.vocab.characters = group.characters;
    state.vocab.statuses = group.statuses;
    persistVocabGroups();
    renderVocabGrid();
    refreshVocabAvailability(state.vocab.characters);
    const added = newCharOrder.length;
    showToast(`已按小标题录入 ${added} 个生字`, "success");
  } else {
    const added = vocabAddCharacters(allChars);
    showToast(added > 0 ? `已添加 ${added} 个生字` : "这些字都已在列表中", added > 0 ? "success" : "info");
  }
  if (input) input.value = "";
}

function vocabSetCharacterStatus(character, status) {
  syncActiveVocabCharacters();
  const normalized = normalizeVocabStatus(status);
  state.vocab.statuses[character] = normalized;
  for (const group of Object.values(state.vocab.groups)) {
    if (group.characters && group.characters.includes(character)) {
      group.statuses[character] = normalized;
    }
  }
  persistVocabGroups();
  renderVocabGrid();
}

function vocabRemove(character) {
  syncActiveVocabCharacters();
  state.vocab.characters = state.vocab.characters.filter((ch) => ch !== character);
  delete state.vocab.statuses[character];
  const group = state.vocab.groups[state.vocab.activeGroup];
  group.characters = state.vocab.characters;
  group.statuses = state.vocab.statuses;
  if (group.sections) {
    group.sections = group.sections.map((s) => ({
      ...s,
      characters: s.characters.filter((ch) => ch !== character),
    })).filter((s) => s.characters.length);
  }
  persistVocabGroups();
  renderVocabGrid();
}

function vocabConfirmRemove(character) {
  if (!window.confirm(`确认删除生字「${character}」吗？`)) {
    return;
  }
  vocabRemove(character);
}

function vocabSearch(query) {
  const resultEl = qs("#vocab-search-result");
  if (!resultEl) return;
  document.querySelectorAll(".vocab-cell.is-search-hit").forEach((el) =>
    el.classList.remove("is-search-hit")
  );
  const chars = extractHanzi(query);
  if (!chars.length) {
    resultEl.innerHTML = "";
    return;
  }
  const ch = chars[0];
  const inList = state.vocab.characters.includes(ch);
  const hasExplain = Boolean(state.vocab.explanations[ch]);
  if (inList) {
    const cell = qs(`#vocab-grid .vocab-cell canvas[data-vocab-char="${ch}"]`);
    if (cell) {
      const cellEl = cell.closest(".vocab-cell");
      if (cellEl) {
        cellEl.classList.add("is-search-hit");
        cellEl.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }
    resultEl.textContent = hasExplain
      ? `「${ch}」已在列表中，已有解读`
      : `「${ch}」已在列表中，尚未生成解读`;
  } else {
    resultEl.innerHTML = "";
    resultEl.append(document.createTextNode(`「${ch}」不在当前分组 `));
    const addLink = document.createElement("button");
    addLink.type = "button";
    addLink.className = "vocab-search-add";
    addLink.textContent = "添加该字";
    addLink.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const added = vocabAddCharacters([ch]);
      const searchInput = qs("#vocab-search");
      if (searchInput) searchInput.value = "";
      resultEl.innerHTML = "";
      showToast(added ? `已添加「${ch}」` : `「${ch}」已存在`, added ? "success" : "info");
    });
    resultEl.append(addLink);
  }
}

function vocabClear() {
  if (!confirmVocabClear()) {
    return;
  }
  state.vocab.characters = [];
  state.vocab.statuses = {};
  state.vocab.groups[state.vocab.activeGroup] = {
    characters: state.vocab.characters,
    statuses: state.vocab.statuses,
  };
  persistVocabGroups();
  renderVocabGrid();
  setVocabBanner("");
}

function confirmVocabClear() {
  syncActiveVocabCharacters();
  if (!state.vocab.characters.length) {
    return false;
  }
  const firstConfirmed = window.confirm(
    `确认清空分组「${state.vocab.activeGroup}」下的全部生字吗？`
  );
  if (!firstConfirmed) {
    return false;
  }
  return window.confirm(
    "请再次确认：清空后该分组的生字列表会被删除，页面内无法撤销。"
  );
}

function vocabSelectedDeviceId() {
  const select = qs("#vocab-device-select");
  return (select && select.value) || "";
}

async function vocabDispatchOne(character, options = {}) {
  const deviceId = vocabSelectedDeviceId();
  if (!deviceId) {
    if (!options.silentNoDevice) {
      showToast("没有可下发的在线硬件设备", "error");
    }
    return;
  }
  const explain = state.vocab.explanations[character];
  const text = explain && explain.explanation ? explain.explanation.trim() : "";
  try {
    if (text) {
      // 有预生成解读：下发「描红 + 文字 + TTS 朗读」（设备逐字播解读、不过 LLM）。
      await sendJson(`/api/devices/${encodeURIComponent(deviceId)}/trace-say`, "POST", {
        character,
        text,
      });
    } else {
      // 无解读：退回仅描红展示。
      await sendJson(`/api/devices/${encodeURIComponent(deviceId)}/trace`, "POST", {
        character,
      });
    }
    showToast(`已下发「${character}」到 ${deviceId}`, "success");
  } catch (error) {
    const msg = /409/.test(error.message) ? "设备未连接或忙" : error.message;
    showToast(`下发「${character}」失败：${msg}`, "error");
  }
}

// 全部下发：仅对有字形的字依次下发，节流避免刷爆设备；按钮禁用并显示进度。
async function vocabDispatchAll() {
  if (state.vocab.dispatching) {
    return;
  }
  const deviceId = vocabSelectedDeviceId();
  if (!deviceId) {
    showToast("没有可下发的在线硬件设备", "error");
    return;
  }
  const targets = state.vocab.characters.filter((ch) => state.vocab.availability[ch] !== false);
  if (!targets.length) {
    showToast("没有可下发的字（均无字形数据）", "error");
    return;
  }
  state.vocab.dispatching = true;
  setButtonLoading("#vocab-dispatch-all", true);
  renderVocabGrid();
  const status = qs("#vocab-dispatch-status");
  let ok = 0;
  let fail = 0;
  for (let i = 0; i < targets.length; i += 1) {
    const ch = targets[i];
    if (status) {
      status.textContent = `下发中 ${i + 1}/${targets.length}：${ch}`;
    }
    try {
      const explain = state.vocab.explanations[ch];
      const text = explain && explain.explanation ? explain.explanation.trim() : "";
      if (text) {
        await sendJson(`/api/devices/${encodeURIComponent(deviceId)}/trace-say`, "POST", {
          character: ch,
          text,
        });
      } else {
        await sendJson(`/api/devices/${encodeURIComponent(deviceId)}/trace`, "POST", {
          character: ch,
        });
      }
      ok += 1;
    } catch (error) {
      fail += 1;
    }
    // 节流：每次之间留出展示间隔，避免刷爆设备
    if (i < targets.length - 1) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  }
  state.vocab.dispatching = false;
  setButtonLoading("#vocab-dispatch-all", false);
  if (status) {
    status.textContent = `完成：成功 ${ok}，失败 ${fail}`;
  }
  renderVocabGrid();
  showToast(`批量下发完成：成功 ${ok}，失败 ${fail}`, fail ? "warn" : "success");
}

// 从「记忆管理」生词表导入已问过的字作为初始列表（复用现有 vocabulary 接口）。
async function vocabImportFromMemory() {
  setButtonLoading("#vocab-import", true);
  setVocabBanner("");
  try {
    const usersData = await fetchJson("/api/memory/users");
    const users = Array.isArray(usersData.users) ? usersData.users : [];
    if (!users.length) {
      setVocabBanner("暂无用户记忆，无法导入生词。");
      return;
    }
    const collected = [];
    for (const userId of users) {
      try {
        const data = await fetchJson(
          `/api/memory/users/${encodeURIComponent(userId)}/vocabulary`
        );
        const entries = Array.isArray(data.vocabulary) ? data.vocabulary : [];
        for (const entry of entries) {
          const ch = String(entry && entry.character ? entry.character : "").trim();
          if (ch) {
            collected.push(ch);
          }
        }
      } catch (e) {
        // 单个用户读取失败不阻塞整体导入
      }
    }
    const chars = extractHanzi(collected.join(""));
    if (!chars.length) {
      setVocabBanner("生词表为空，没有可导入的汉字。");
      return;
    }
    const added = vocabAddCharacters(chars);
    showToast(`已从生词表导入 ${added} 个新字`, added > 0 ? "success" : "info");
  } catch (error) {
    setVocabBanner(`导入生词失败：${error.message}`);
  } finally {
    setButtonLoading("#vocab-import", false);
  }
}

// ===== 打印功能：生成田字格大字排版 PDF（浏览器打印） =====

async function vocabPrint() {
  const chars = state.vocab.characters;
  const filter = vocabStatusFilter();
  const visible = chars.filter(
    (ch) => filter === "all" || normalizeVocabStatus(state.vocab.statuses[ch]) === filter
  );
  if (!visible.length) {
    showToast("当前没有可打印的生字", "warn");
    return;
  }

  setButtonLoading("#vocab-print", true);
  showToast("正在生成打印图片…", "info");

  // 组词/解读：若 state 里缺，重新拉一次缓存（纯读，不调模型）后合并，
  // 只更新 explanations，不触碰分组与网格渲染。
  const missingWord = visible.some((ch) => {
    const e = state.vocab.explanations[ch];
    return !e || !e.word;
  });
  if (missingWord) {
    try {
      const data = await fetchJson("/api/vocab/explanations");
      const items = Array.isArray(data.explanations) ? data.explanations : [];
      for (const item of items) {
        const ch = String(item && item.character ? item.character : "").trim();
        if (!ch) continue;
        state.vocab.explanations[ch] = {
          word: item.word || "",
          explanation: item.explanation || "",
          source: item.source || "",
          hasTts: Boolean(item.has_tts),
          ttsUrl: item.tts_url || "",
          ttsSampleRateHz: item.tts_sample_rate_hz || 0,
        };
      }
    } catch (_) {}
  }

  // 批量拉取字形轮廓（正楷描红）
  const needGlyph = visible.filter(
    (ch) => !state.vocab.glyphs[ch] && state.vocab.availability[ch] !== false
  );
  const BATCH = 20;
  for (let i = 0; i < needGlyph.length; i += BATCH) {
    const batch = needGlyph.slice(i, i + BATCH);
    await Promise.all(batch.map(async (ch) => {
      try {
        const payload = await fetchTracePayload(ch);
        state.vocab.glyphs[ch] = payload;
      } catch (_) {}
    }));
  }

  const groupName = state.vocab.activeGroup || VOCAB_DEFAULT_GROUP_NAME;

  // 渲染成 A4 纸大小的图片（不直接调打印机，移动端长按保存自己打印）
  const pageImages = renderVocabA4Pages(visible, groupName);
  setButtonLoading("#vocab-print", false);
  showVocabPrintModal(pageImages, groupName);
}

// 把生字按 5 列田字格排版渲染到一张或多张 A4（竖版，约 150 DPI）画布，返回 PNG dataURL 列表。
function renderVocabA4Pages(visible, groupName) {
  const PAGE_W = 1240; // A4 竖版 @ ~150 DPI
  const PAGE_H = 1754;
  const MARGIN = 40;
  const COLS = 5;
  const TITLE_H = 64;
  const cellW = Math.floor((PAGE_W - 2 * MARGIN) / COLS);
  const gridSize = cellW - 8;
  const stripH = Math.round(gridSize * 0.2);
  const cellH = gridSize + stripH + 8;
  const rowsPerPage = Math.max(1, Math.floor((PAGE_H - MARGIN - TITLE_H - MARGIN) / cellH));
  const perPage = COLS * rowsPerPage;

  const pages = [];
  for (let i = 0; i < visible.length; i += perPage) {
    pages.push(visible.slice(i, i + perPage));
  }

  const images = [];
  pages.forEach((pageChars, pageIdx) => {
    const page = document.createElement("canvas");
    page.width = PAGE_W;
    page.height = PAGE_H;
    const pctx = page.getContext("2d");
    pctx.fillStyle = "#ffffff";
    pctx.fillRect(0, 0, PAGE_W, PAGE_H);

    pctx.fillStyle = "#000000";
    pctx.textAlign = "center";
    pctx.textBaseline = "middle";
    pctx.font = 'bold 34px "PingFang SC", "Microsoft YaHei", sans-serif';
    const titleText = pages.length > 1
      ? `${groupName}（${pageIdx + 1}/${pages.length}）`
      : groupName;
    pctx.fillText(titleText, PAGE_W / 2, MARGIN + TITLE_H / 2);

    pageChars.forEach((ch, idx) => {
      const col = idx % COLS;
      const row = Math.floor(idx / COLS);
      const x = MARGIN + col * cellW + Math.floor((cellW - gridSize) / 2);
      const y = MARGIN + TITLE_H + row * cellH;
      const explain = state.vocab.explanations[ch];
      const wordText = (explain && explain.word) || "";
      const tmp = document.createElement("canvas");
      renderPrintCell(tmp, ch, gridSize, wordText);
      pctx.drawImage(tmp, x, y);
    });

    images.push(page.toDataURL("image/png"));
  });
  return images;
}

// 弹窗展示生成的 A4 图片：移动端长按保存到相册，桌面端可点下载或直接打印。
function showVocabPrintModal(pageImages, groupName) {
  const overlay = qs("#print-overlay");
  if (!overlay) return;
  overlay.innerHTML = "";
  overlay.classList.add("print-modal");

  const head = document.createElement("div");
  head.className = "print-modal-head";
  const hint = document.createElement("span");
  hint.className = "print-modal-hint";
  hint.textContent = pageImages.length > 1
    ? `共 ${pageImages.length} 页 · 手机长按图片保存到相册，再自行打印`
    : "手机长按图片保存到相册，再自行打印";
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "button secondary";
  closeBtn.textContent = "关闭";
  closeBtn.addEventListener("click", () => {
    overlay.hidden = true;
    overlay.innerHTML = "";
  });
  head.append(hint, closeBtn);
  overlay.append(head);

  const pagesWrap = document.createElement("div");
  pagesWrap.className = "print-pages";
  pageImages.forEach((src, idx) => {
    const item = document.createElement("div");
    item.className = "print-page-item";
    const img = document.createElement("img");
    img.src = src;
    img.alt = `${groupName} 第${idx + 1}页`;
    item.append(img);

    const dl = document.createElement("a");
    dl.className = "button secondary print-download";
    dl.href = src;
    dl.download = pageImages.length > 1
      ? `${groupName}-${idx + 1}.png`
      : `${groupName}.png`;
    dl.textContent = pageImages.length > 1 ? `下载第 ${idx + 1} 页` : "下载图片";
    item.append(dl);

    pagesWrap.append(item);
  });
  overlay.append(pagesWrap);

  overlay.hidden = false;
}

function renderPrintCell(canvas, character, gridSize, word) {
  const g = gridSize;
  const stripH = Math.round(g * 0.2); // 底部组词文字带
  const W = g;
  const H = g + stripH;
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, W, H);

  // 田字格（占顶部 g×g）：实线外框 + 虚线十字
  ctx.save();
  ctx.strokeStyle = "#cc0000";
  ctx.lineWidth = 2;
  ctx.strokeRect(1, 1, g - 2, g - 2);
  ctx.setLineDash([6, 5]);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(g / 2, 0); ctx.lineTo(g / 2, g);
  ctx.moveTo(0, g / 2); ctx.lineTo(g, g / 2);
  ctx.stroke();
  ctx.restore();

  // 顶部 g×g 内绘正楷字形轮廓
  let drewGlyph = false;
  const glyph = state.vocab.glyphs[character];
  if (glyph) {
    const normalized = normalizeTracePayload(glyph);
    if (normalized && normalized.strokes && normalized.strokes.length) {
      const pad = 6;
      const map = (pt) => ({
        x: pad + (Math.max(0, Math.min(1000, Number(pt?.[0]) || 0)) / 1000) * (g - 2 * pad),
        y: pad + (Math.max(0, Math.min(1000, Number(pt?.[1]) || 0)) / 1000) * (g - 2 * pad),
      });
      ctx.save();
      ctx.fillStyle = "#222222";
      ctx.strokeStyle = "#222222";
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      const outlines = normalized.outlines;
      if (Array.isArray(outlines) && outlines.length === normalized.strokes.length) {
        for (const outline of outlines) {
          if (!Array.isArray(outline) || !outline.length) continue;
          ctx.beginPath();
          for (const contour of outline) {
            if (!Array.isArray(contour) || contour.length < 3) continue;
            const p0 = map(contour[0]);
            ctx.moveTo(p0.x, p0.y);
            for (let k = 1; k < contour.length; k++) {
              const p = map(contour[k]);
              ctx.lineTo(p.x, p.y);
            }
            ctx.closePath();
          }
          ctx.fill("evenodd");
        }
      } else {
        ctx.lineWidth = (g / 240) * 14;
        for (const median of normalized.strokes) {
          if (!Array.isArray(median) || median.length < 2) continue;
          ctx.beginPath();
          const p0 = map(median[0]);
          ctx.moveTo(p0.x, p0.y);
          for (let k = 1; k < median.length; k++) {
            const p = map(median[k]);
            ctx.lineTo(p.x, p.y);
          }
          ctx.stroke();
        }
      }
      ctx.restore();
      drewGlyph = true;
    }
  }

  // 无字形数据：回退楷体文字
  if (!drewGlyph) {
    ctx.save();
    ctx.fillStyle = "#222222";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = `${Math.floor(g * 0.7)}px "Kaiti SC", "STKaiti", Kaiti, "楷体", "KaiTi", serif`;
    ctx.fillText(character, g / 2, g / 2 + 2);
    ctx.restore();
  }

  // 底部文字带：组词（缺组词时退回单字），按宽度自动缩放字号防溢出
  const label = String(word || character);
  ctx.save();
  ctx.fillStyle = "#000000";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  let fontPx = Math.round(stripH * 0.62);
  ctx.font = `${fontPx}px "PingFang SC", "Microsoft YaHei", "Heiti SC", sans-serif`;
  const maxW = W - 8;
  while (fontPx > 8 && ctx.measureText(label).width > maxW) {
    fontPx -= 1;
    ctx.font = `${fontPx}px "PingFang SC", "Microsoft YaHei", "Heiti SC", sans-serif`;
  }
  ctx.fillText(label, W / 2, g + stripH / 2 + 1);
  ctx.restore();
}

function populateVocabDeviceSelect(hardware) {
  const select = qs("#vocab-device-select");
  if (!select) {
    return;
  }
  const previous = select.value;
  select.innerHTML = "";
  if (!hardware.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "无在线硬件";
    select.append(option);
    return;
  }
  hardware.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.device_id;
    option.textContent = item.device_id;
    select.append(option);
  });
  select.value = hardware.some((item) => item.device_id === previous)
    ? previous
    : hardware[0].device_id;
}

// ===== 成语学习（批量录入 + 大字陈列 + 解读/故事生成 + 下发大字朗读 + 打印） =====

function uniqueIdioms(idioms) {
  const seen = new Set();
  const result = [];
  for (const w of idioms || []) {
    const value = String(w || "").trim();
    if (value && !seen.has(value)) {
      seen.add(value);
      result.push(value);
    }
  }
  return result;
}

// 从文本抽取成语：按空白/标点/换行切分，保留「2–8 个连续汉字」的词条。
function extractIdioms(text) {
  const matches = String(text || "").match(/[一-鿿]{2,8}/g) || [];
  return uniqueIdioms(matches);
}

function normalizeIdiomGroups(groups) {
  const source = groups && typeof groups === "object" ? groups : {};
  const normalized = {};
  for (const [name, group] of Object.entries(source)) {
    const label = String(name || "").trim();
    if (!label) continue;
    if (Array.isArray(group)) {
      normalized[label] = { idioms: uniqueIdioms(group), statuses: {}, sections: [] };
    } else {
      const idioms = uniqueIdioms(group && group.idioms);
      normalized[label] = {
        idioms,
        statuses: normalizeIdiomStatuses(group && group.statuses, idioms),
        sections: [],
      };
    }
  }
  if (!normalized[IDIOM_DEFAULT_GROUP_NAME]) {
    normalized[IDIOM_DEFAULT_GROUP_NAME] = { idioms: [], statuses: {}, sections: [] };
  }
  return normalized;
}

function normalizeIdiomStatuses(statuses, idioms) {
  const result = {};
  const source = statuses && typeof statuses === "object" ? statuses : {};
  for (const w of idioms || []) {
    result[w] = normalizeVocabStatus(source[w]);
  }
  return result;
}

function idiomGlobalStatuses() {
  const merged = {};
  for (const group of Object.values(state.idiom.groups)) {
    if (group.statuses) {
      for (const [w, s] of Object.entries(group.statuses)) {
        const ns = normalizeVocabStatus(s);
        if (!merged[w] || ns !== VOCAB_STATUS_UNLEARNED) merged[w] = ns;
      }
    }
  }
  return merged;
}

function syncActiveIdiomList() {
  const groupName = state.idiom.activeGroup || IDIOM_DEFAULT_GROUP_NAME;
  state.idiom.groups = normalizeIdiomGroups(state.idiom.groups);
  state.idiom.activeGroup = state.idiom.groups[groupName] ? groupName : IDIOM_DEFAULT_GROUP_NAME;
  const global = idiomGlobalStatuses();
  const group = state.idiom.groups[state.idiom.activeGroup];
  for (const w of group.idioms) {
    if (global[w]) group.statuses[w] = global[w];
  }
  group.statuses = normalizeIdiomStatuses(group.statuses, group.idioms);
  state.idiom.idioms = group.idioms;
  state.idiom.statuses = group.statuses;
}

function currentIdiomGroupsPayload() {
  return {
    active_group: state.idiom.activeGroup || IDIOM_DEFAULT_GROUP_NAME,
    active_status_filter: state.idiom.activeStatusFilter || "all",
    groups: normalizeIdiomGroups(state.idiom.groups),
    explanation_prompt_template: state.idiom.explanationTemplate || "",
    story_prompt_template: state.idiom.storyTemplate || "",
  };
}

function applyIdiomGroupsPayload(payload) {
  const groups = normalizeIdiomGroups(payload && payload.groups);
  state.idiom.groups = groups;
  state.idiom.activeGroup =
    payload && groups[payload.active_group] ? payload.active_group : IDIOM_DEFAULT_GROUP_NAME;
  state.idiom.activeStatusFilter =
    (payload && payload.active_status_filter) || "all";
  state.idiom.explanationTemplate = (payload && payload.explanation_prompt_template) || "";
  state.idiom.storyTemplate = (payload && payload.story_prompt_template) || "";
  const eEl = qs("#idiom-explanation-template");
  if (eEl) eEl.value = state.idiom.explanationTemplate;
  const sEl = qs("#idiom-story-template");
  if (sEl) sEl.value = state.idiom.storyTemplate;
  const filterEl = qs("#idiom-status-filter");
  if (filterEl) filterEl.value = state.idiom.activeStatusFilter;
  syncActiveIdiomList();
  renderIdiomGroupSelect();
  renderIdiomGrid();
}

function persistIdiomGroupsLocally() {
  try {
    localStorage.setItem(IDIOM_GROUPS_STORAGE_KEY, JSON.stringify(currentIdiomGroupsPayload()));
  } catch (_) {}
}

let idiomPersistTimer = null;
async function saveIdiomGroupsToServer() {
  if (!state.idiom.serverLoaded) return;
  try {
    await sendJson("/api/idiom/groups", "PUT", currentIdiomGroupsPayload());
  } catch (error) {
    showToast(`成语分组保存到服务端失败：${error.message}`, "error");
  }
}

function persistIdiomGroups() {
  persistIdiomGroupsLocally();
  if (idiomPersistTimer) clearTimeout(idiomPersistTimer);
  idiomPersistTimer = setTimeout(() => {
    idiomPersistTimer = null;
    saveIdiomGroupsToServer();
  }, 1000);
}

function readLocalIdiomGroupsPayload() {
  try {
    const raw = localStorage.getItem(IDIOM_GROUPS_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (_) {
    return null;
  }
}

function loadIdiomGroups() {
  const local = readLocalIdiomGroupsPayload();
  if (local) {
    applyIdiomGroupsPayload(local);
  } else {
    state.idiom.groups = normalizeIdiomGroups({});
    state.idiom.activeGroup = IDIOM_DEFAULT_GROUP_NAME;
    syncActiveIdiomList();
  }
}

async function loadIdiomGroupsFromServer() {
  try {
    const serverPayload = await fetchJson("/api/idiom/groups");
    applyIdiomGroupsPayload(serverPayload);
    state.idiom.serverLoaded = true;
    persistIdiomGroupsLocally();
  } catch (error) {
    state.idiom.serverLoaded = false;
    showToast(`成语分组从服务端加载失败，已使用本机缓存：${error.message}`, "warn");
  }
}

function renderIdiomGroupSelect() {
  const select = qs("#idiom-group-select");
  if (!select) return;
  const names = Object.keys(state.idiom.groups);
  select.innerHTML = "";
  for (const name of names) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    select.append(option);
  }
  select.value = state.idiom.activeGroup || IDIOM_DEFAULT_GROUP_NAME;
}

function idiomStatusFilter() {
  return state.idiom.activeStatusFilter || "all";
}

function idiomColor(idiom) {
  const status = normalizeVocabStatus(state.idiom.statuses[idiom]);
  if (status === VOCAB_STATUS_LEARNED) return VOCAB_LEARNED_COLOR;
  if (status === VOCAB_STATUS_NEW) return VOCAB_NEW_COLOR;
  return VOCAB_UNLEARNED_COLOR;
}

function idiomSwitchGroup(groupName) {
  if (!state.idiom.groups[groupName]) return;
  state.idiom.activeGroup = groupName;
  syncActiveIdiomList();
  persistIdiomGroups();
  renderIdiomGrid();
  if (state.idiom.idioms.length) idiomEnsureAllGlyphs(state.idiom.idioms);
}

function idiomCreateGroup() {
  const input = qs("#idiom-group-name");
  const name = (input && input.value || "").trim();
  if (!name) {
    setIdiomBanner("请输入新分组名称。");
    return;
  }
  if (state.idiom.groups[name]) {
    setIdiomBanner("该分组已存在。");
    return;
  }
  state.idiom.groups[name] = { idioms: [], statuses: {}, sections: [] };
  state.idiom.activeGroup = name;
  if (input) input.value = "";
  setIdiomBanner("");
  syncActiveIdiomList();
  renderIdiomGroupSelect();
  persistIdiomGroups();
  renderIdiomGrid();
}

function idiomDeleteGroup() {
  const name = state.idiom.activeGroup;
  if (name === IDIOM_DEFAULT_GROUP_NAME) {
    showToast("默认分组不可删除", "warn");
    return;
  }
  if (!window.confirm(`确认删除分组「${name}」吗？该分组内的成语列表会被移除。`)) return;
  delete state.idiom.groups[name];
  state.idiom.activeGroup = IDIOM_DEFAULT_GROUP_NAME;
  syncActiveIdiomList();
  renderIdiomGroupSelect();
  persistIdiomGroups();
  renderIdiomGrid();
}

function idiomAddToGroup(idioms, groupName = state.idiom.activeGroup) {
  const group = state.idiom.groups[groupName];
  if (!group) return 0;
  let added = 0;
  for (const w of uniqueIdioms(idioms)) {
    if (!group.idioms.includes(w)) {
      group.idioms.push(w);
      group.statuses[w] = VOCAB_STATUS_UNLEARNED;
      added += 1;
    }
  }
  return added;
}

function idiomAddFromInput() {
  const input = qs("#idiom-input");
  const text = (input && input.value) || "";
  const idioms = extractIdioms(text);
  if (!idioms.length) {
    setIdiomBanner("没有识别到成语（需 2–8 个连续汉字），请检查输入。");
    return;
  }
  setIdiomBanner("");
  syncActiveIdiomList();
  const added = idiomAddToGroup(idioms, state.idiom.activeGroup);
  syncActiveIdiomList();
  persistIdiomGroups();
  renderIdiomGrid();
  idiomEnsureAllGlyphs(state.idiom.idioms);
  showToast(added > 0 ? `已添加 ${added} 个成语` : "这些成语都已在列表中", added > 0 ? "success" : "info");
  if (input) input.value = "";
}

function setIdiomBanner(message) {
  const banner = qs("#idiom-banner");
  if (!banner) return;
  if (message) {
    banner.textContent = message;
    banner.hidden = false;
  } else {
    banner.textContent = "";
    banner.hidden = true;
  }
}

// 拉取一个成语里每个单字的楷体字形（共享缓存，键为单字），用于大字展示与打印。
async function idiomEnsureGlyphs(idiom) {
  const chars = Array.from(idiom);
  await Promise.all(
    chars.map(async (ch) => {
      if (state.idiom.glyphs[ch] !== undefined) return;
      try {
        state.idiom.glyphs[ch] = await fetchTracePayload(ch);
      } catch (_) {
        state.idiom.glyphs[ch] = null;
      }
    })
  );
}

async function idiomEnsureAllGlyphs(idioms) {
  const chars = new Set();
  for (const w of idioms || []) for (const ch of w) chars.add(ch);
  const need = [...chars].filter((ch) => state.idiom.glyphs[ch] === undefined);
  const BATCH = 20;
  for (let i = 0; i < need.length; i += BATCH) {
    const batch = need.slice(i, i + BATCH);
    await Promise.all(
      batch.map(async (ch) => {
        try {
          state.idiom.glyphs[ch] = await fetchTracePayload(ch);
        } catch (_) {
          state.idiom.glyphs[ch] = null;
        }
      })
    );
  }
  renderIdiomGrid();
}

// 在一个 canvas 上画一个成语单字的大字（楷体轮廓静态填充，无田字格、无描红）。
function renderIdiomBigChar(canvas, ch, color) {
  const { ctx, w, h } = setupVocabCanvas(canvas);
  if (!ctx) return;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = TRACE_BG_COLOR;
  ctx.fillRect(0, 0, w, h);
  const glyph = state.idiom.glyphs[ch];
  if (!glyph || !drawVocabGlyphStatic(ctx, glyph, w, h, color)) {
    ctx.save();
    ctx.fillStyle = color;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = `${Math.floor(h * 0.7)}px "Kaiti SC", "STKaiti", Kaiti, "楷体", "KaiTi", serif`;
    ctx.fillText(ch, w / 2, h / 2 + 2);
    ctx.restore();
  }
}

function buildIdiomCard(idiom) {
  const status = normalizeVocabStatus(state.idiom.statuses[idiom]);
  const content = state.idiom.contents[idiom];
  const color = idiomColor(idiom);
  const card = document.createElement("div");
  card.className = "idiom-card";
  card.dataset.status = status;

  // 大字区：单行横排（硬件端才是 2×2）。
  const chars = Array.from(idiom);
  const charsWrap = document.createElement("div");
  charsWrap.className = "idiom-card-chars";
  for (const ch of chars) {
    const canvas = document.createElement("canvas");
    canvas.dataset.cssWidth = "76";
    canvas.dataset.cssHeight = "76";
    canvas.width = 76;
    canvas.height = 76;
    canvas.className = "idiom-char-canvas";
    charsWrap.append(canvas);
    renderIdiomBigChar(canvas, ch, color);
  }
  card.append(charsWrap);

  if (content && content.pinyin) {
    const pinyin = document.createElement("div");
    pinyin.className = "idiom-card-pinyin";
    pinyin.textContent = content.pinyin;
    card.append(pinyin);
  }

  // 状态切换
  const statusControls = document.createElement("div");
  statusControls.className = "vocab-status-control";
  statusControls.setAttribute("aria-label", `设置「${idiom}」学习状态`);
  [
    { value: VOCAB_STATUS_UNLEARNED, label: "未学" },
    { value: VOCAB_STATUS_NEW, label: "不熟" },
    { value: VOCAB_STATUS_LEARNED, label: "已会" },
  ].forEach((item) => {
    const button = document.createElement("button");
    button.className = "vocab-status-button";
    button.type = "button";
    button.dataset.status = item.value;
    button.textContent = item.label;
    button.setAttribute("aria-pressed", String(status === item.value));
    button.disabled = state.idiom.dispatching;
    button.addEventListener("click", () => idiomSetStatus(idiom, item.value));
    statusControls.append(button);
  });
  card.append(statusControls);

  // 解读 + 故事 文本框
  const makeField = (labelText, key, placeholder) => {
    const box = document.createElement("div");
    box.className = "idiom-card-field";
    const label = document.createElement("div");
    label.className = "idiom-card-field-label";
    label.textContent = labelText;
    box.append(label);
    const textarea = document.createElement("textarea");
    textarea.className = "vocab-cell-explain-text";
    textarea.rows = 3;
    textarea.placeholder = placeholder;
    textarea.value = content ? content[key] || "" : "";
    textarea.disabled = state.idiom.dispatching;
    textarea.addEventListener("input", () => {
      const current = state.idiom.contents[idiom] || {};
      state.idiom.contents[idiom] = { ...current, [key]: textarea.value };
    });
    box.append(textarea);
    return box;
  };
  card.append(makeField("解读", "explanation", "尚未生成，点「生成」由 AI 编写成语解读。"));
  card.append(makeField("小故事", "story", "尚未生成，点「生成」由 AI 编写成语小故事。"));

  // 操作行
  const actions = document.createElement("div");
  actions.className = "vocab-cell-actions";

  const genBtn = document.createElement("button");
  genBtn.className = "button secondary";
  genBtn.type = "button";
  genBtn.textContent = state.idiom.generating[idiom] ? "生成中…" : content ? "重新生成" : "生成";
  genBtn.disabled = state.idiom.dispatching || Boolean(state.idiom.generating[idiom]);
  genBtn.addEventListener("click", () => idiomGenerateOne(idiom, { regenerate: Boolean(content) }));
  actions.append(genBtn);

  if (content && content.hasExplanationTts) {
    const b = document.createElement("button");
    b.className = "button secondary";
    b.type = "button";
    b.textContent = "试听解读";
    b.disabled = state.idiom.dispatching;
    b.addEventListener("click", () => idiomPreviewTts(idiom, "explanation", b));
    actions.append(b);
  }
  if (content && content.hasStoryTts) {
    const b = document.createElement("button");
    b.className = "button secondary";
    b.type = "button";
    b.textContent = "试听故事";
    b.disabled = state.idiom.dispatching;
    b.addEventListener("click", () => idiomPreviewTts(idiom, "story", b));
    actions.append(b);
  }

  const dispatchBtn = document.createElement("button");
  dispatchBtn.className = "button secondary";
  dispatchBtn.type = "button";
  dispatchBtn.textContent = "下发";
  dispatchBtn.disabled = state.idiom.dispatching;
  dispatchBtn.addEventListener("click", () => idiomDispatchOne(idiom));
  actions.append(dispatchBtn);

  const removeBtn = document.createElement("button");
  removeBtn.className = "button secondary";
  removeBtn.type = "button";
  removeBtn.textContent = "删除";
  removeBtn.disabled = state.idiom.dispatching;
  removeBtn.addEventListener("click", () => idiomConfirmRemove(idiom));
  actions.append(removeBtn);

  card.append(actions);
  return card;
}

function renderIdiomGrid() {
  const grid = qs("#idiom-grid");
  if (!grid) return;
  const idioms = state.idiom.idioms;
  const filter = idiomStatusFilter();
  const visible = idioms.filter(
    (w) => filter === "all" || normalizeVocabStatus(state.idiom.statuses[w]) === filter
  );
  const empty = qs("#idiom-empty");
  const count = qs("#idiom-count");
  if (count) {
    count.textContent = idioms.length
      ? filter === "all"
        ? `（${idioms.length} 条）`
        : `（${visible.length}/${idioms.length} 条）`
      : "";
  }
  if (empty) {
    empty.hidden = visible.length > 0;
    empty.textContent = idioms.length ? "当前状态筛选下暂无成语。" : "暂无成语，先在上方批量录入。";
  }
  grid.innerHTML = "";
  for (const w of visible) {
    grid.append(buildIdiomCard(w));
  }
}

function idiomSetStatus(idiom, status) {
  syncActiveIdiomList();
  const normalized = normalizeVocabStatus(status);
  state.idiom.statuses[idiom] = normalized;
  for (const group of Object.values(state.idiom.groups)) {
    if (group.idioms && group.idioms.includes(idiom)) {
      group.statuses[idiom] = normalized;
    }
  }
  persistIdiomGroups();
  renderIdiomGrid();
}

function idiomConfirmRemove(idiom) {
  if (!window.confirm(`确认删除成语「${idiom}」吗？`)) return;
  idiomRemove(idiom);
}

function idiomRemove(idiom) {
  for (const group of Object.values(state.idiom.groups)) {
    if (group.idioms) {
      group.idioms = group.idioms.filter((w) => w !== idiom);
      if (group.statuses) delete group.statuses[idiom];
    }
  }
  syncActiveIdiomList();
  persistIdiomGroups();
  renderIdiomGrid();
}

function idiomClear() {
  if (!state.idiom.idioms.length) {
    showToast("当前分组没有成语", "info");
    return;
  }
  if (!window.confirm("请再次确认：清空后该分组的成语列表会被删除（解读/故事缓存仍保留在服务端）。")) {
    return;
  }
  confirmIdiomClear();
}

function confirmIdiomClear() {
  const group = state.idiom.groups[state.idiom.activeGroup];
  if (group) {
    group.idioms = [];
    group.statuses = {};
    group.sections = [];
  }
  syncActiveIdiomList();
  persistIdiomGroups();
  renderIdiomGrid();
}

async function idiomGenerateOne(idiom, { regenerate = false } = {}) {
  if (state.idiom.generating[idiom] || state.idiom.dispatching) return;
  state.idiom.generating[idiom] = true;
  renderIdiomGrid();
  try {
    const body = { idiom, regenerate };
    if (state.idiom.explanationTemplate) body.explanation_prompt = state.idiom.explanationTemplate;
    if (state.idiom.storyTemplate) body.story_prompt = state.idiom.storyTemplate;
    const data = await sendJson("/api/idiom/content", "POST", body);
    state.idiom.contents[idiom] = {
      pinyin: (data && data.pinyin) || "",
      explanation: (data && data.explanation) || "",
      story: (data && data.story) || "",
      source: (data && data.source) || "",
      hasExplanationTts: Boolean(data && data.has_explanation_tts),
      hasStoryTts: Boolean(data && data.has_story_tts),
    };
    if (data && data.cached) {
      // silently restored from cache
    } else if (data && data.source === "fallback") {
      showToast(`「${idiom}」用了离线模板，请检查模型配置后重试`, "warn");
    } else if (data && data.tts_detail) {
      showToast(`「${idiom}」已生成，但语音合成失败（检查 TTS 配置）`, "warn");
    } else {
      showToast(`已生成「${idiom}」的解读、小故事与语音`, "success");
    }
  } catch (error) {
    showToast(`生成「${idiom}」失败：${error.message}`, "error");
  } finally {
    state.idiom.generating[idiom] = false;
    renderIdiomGrid();
  }
}

async function idiomGenerateAll() {
  if (state.idiom.dispatching) return;
  const targets = state.idiom.idioms.filter((w) => !state.idiom.contents[w]);
  if (!targets.length) {
    showToast("没有需要生成的成语（均已生成）", "info");
    return;
  }
  setButtonLoading("#idiom-generate-all", true);
  const status = qs("#idiom-dispatch-status");
  let ok = 0;
  let fail = 0;
  for (let i = 0; i < targets.length; i += 1) {
    const w = targets[i];
    if (status) status.textContent = `生成 ${i + 1}/${targets.length}：${w}`;
    await idiomGenerateOne(w);
    if (state.idiom.contents[w] && state.idiom.contents[w].explanation) ok += 1;
    else fail += 1;
  }
  setButtonLoading("#idiom-generate-all", false);
  if (status) status.textContent = `生成完成：成功 ${ok}，失败 ${fail}`;
  showToast(`批量生成完成：成功 ${ok}，失败 ${fail}`, fail ? "warn" : "success");
}

// 试听：拉取该成语某段（解读/故事）缓存的 TTS 音频（audio/L16 裸 PCM），WebAudio 播放。
async function idiomPreviewTts(idiom, kind, button) {
  const url = `/api/idiom/${encodeURIComponent(idiom)}/tts?kind=${encodeURIComponent(kind)}`;
  if (button) {
    button.disabled = true;
    button.classList.add("is-loading");
  }
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${response.status}`);
    const sampleRate =
      Number(
        response.headers.get("X-TTS-Sample-Rate-Hz") ||
          state.speechSettings.ttsSampleRateHz ||
          16000
      ) || 16000;
    await ensurePlaybackAudioContext();
    stopPlayback("idle");
    schedulePcmPlayback(await response.arrayBuffer(), { sampleRateHz: sampleRate });
  } catch (error) {
    const msg = /404/.test(String(error.message)) ? "暂无缓存语音，请先生成" : error.message;
    showToast(`试听「${idiom}」失败：${msg}`, "error");
  } finally {
    if (button) {
      button.disabled = state.idiom.dispatching;
      button.classList.remove("is-loading");
    }
  }
}

function idiomSelectedDeviceId() {
  const select = qs("#idiom-device-select");
  return (select && select.value) || "";
}

function idiomReadKind() {
  const select = qs("#idiom-read-kind");
  return (select && select.value) === "story" ? "story" : "explanation";
}

async function idiomDispatchOne(idiom, options = {}) {
  const deviceId = idiomSelectedDeviceId();
  if (!deviceId) {
    if (!options.silentNoDevice) showToast("没有可下发的在线硬件设备", "error");
    return false;
  }
  const content = state.idiom.contents[idiom];
  const kind = idiomReadKind();
  const text = content && content[kind] ? String(content[kind]).trim() : "";
  if (!text) {
    showToast(`「${idiom}」尚无${kind === "story" ? "小故事" : "解读"}，请先生成`, "warn");
    return false;
  }
  try {
    await sendJson(`/api/devices/${encodeURIComponent(deviceId)}/idiom-say`, "POST", {
      idiom,
      pinyin: (content && content.pinyin) || "",
      text,
    });
    if (!options.silent) showToast(`已下发「${idiom}」到 ${deviceId}`, "success");
    return true;
  } catch (error) {
    const msg = /409/.test(error.message) ? "设备未连接或忙" : error.message;
    showToast(`下发「${idiom}」失败：${msg}`, "error");
    return false;
  }
}

async function idiomDispatchAll() {
  if (state.idiom.dispatching) return;
  const deviceId = idiomSelectedDeviceId();
  if (!deviceId) {
    showToast("没有可下发的在线硬件设备", "error");
    return;
  }
  const kind = idiomReadKind();
  const targets = state.idiom.idioms.filter((w) => {
    const c = state.idiom.contents[w];
    return c && c[kind] && String(c[kind]).trim();
  });
  if (!targets.length) {
    showToast(`没有可下发的成语（均无${kind === "story" ? "小故事" : "解读"}，请先生成）`, "error");
    return;
  }
  state.idiom.dispatching = true;
  setButtonLoading("#idiom-dispatch-all", true);
  renderIdiomGrid();
  const status = qs("#idiom-dispatch-status");
  let ok = 0;
  let fail = 0;
  for (let i = 0; i < targets.length; i += 1) {
    const w = targets[i];
    if (status) status.textContent = `下发中 ${i + 1}/${targets.length}：${w}`;
    const done = await idiomDispatchOne(w, { silent: true });
    if (done) ok += 1;
    else fail += 1;
    if (i < targets.length - 1) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  }
  state.idiom.dispatching = false;
  setButtonLoading("#idiom-dispatch-all", false);
  if (status) status.textContent = `完成：成功 ${ok}，失败 ${fail}`;
  renderIdiomGrid();
  showToast(`批量下发完成：成功 ${ok}，失败 ${fail}`, fail ? "warn" : "success");
}

// 打开控制台时恢复已生成的成语解读/故事记录（含可试听标记）。
async function idiomLoadContents() {
  try {
    const data = await fetchJson("/api/idiom/contents");
    const items = Array.isArray(data.idioms) ? data.idioms : [];
    let restored = 0;
    for (const item of items) {
      const w = String(item && item.idiom ? item.idiom : "").trim();
      if (!w) continue;
      // 解读/故事/语音是「按成语字面」全局缓存（console-idiom 命名空间），
      // 与分组无关。这里只填充内容缓存，绝不把成语塞进任何分组——
      // 否则所有分组生成过的成语都会被累加进默认分组，造成「跨组串数据」。
      state.idiom.contents[w] = {
        pinyin: item.pinyin || "",
        explanation: item.explanation || "",
        story: item.story || "",
        source: item.source || "",
        hasExplanationTts: Boolean(item.has_explanation_tts),
        hasStoryTts: Boolean(item.has_story_tts),
      };
      restored += 1;
    }
    if (restored) {
      // 仅刷新当前分组卡片，让已在分组内的成语显示出缓存内容。
      renderIdiomGrid();
      idiomEnsureAllGlyphs(state.idiom.idioms);
    }
  } catch (_) {
    // 加载历史记录失败不阻塞页面。
  }
}

// 打印：每条成语一格（4 字 2×2 大字），仅文字、不打印解读/故事/拼音。
async function idiomPrint() {
  const filter = idiomStatusFilter();
  const visible = state.idiom.idioms.filter(
    (w) => filter === "all" || normalizeVocabStatus(state.idiom.statuses[w]) === filter
  );
  if (!visible.length) {
    showToast("当前没有可打印的成语", "warn");
    return;
  }
  setButtonLoading("#idiom-print", true);
  showToast("正在生成打印图片…", "info");
  await idiomEnsureAllGlyphs(visible);
  const groupName = state.idiom.activeGroup || IDIOM_DEFAULT_GROUP_NAME;
  const pageImages = renderIdiomA4Pages(visible, groupName);
  setButtonLoading("#idiom-print", false);
  showVocabPrintModal(pageImages, groupName);
}

// 把成语按 2 列排版渲染到一张或多张 A4（竖版，~150 DPI），返回 PNG dataURL 列表。
function renderIdiomA4Pages(visible, groupName) {
  const PAGE_W = 1240;
  const PAGE_H = 1754;
  const MARGIN = 40;
  const COLS = 2; // 一行放两个成语
  const TITLE_H = 64;
  const cellW = Math.floor((PAGE_W - 2 * MARGIN) / COLS);
  // 每个成语的字横排成一行 → 格高约为一个字高（按 4 字估算字宽），不再是 2×2 方块。
  const cellH = Math.floor(cellW * 0.3);
  const rowsPerPage = Math.max(1, Math.floor((PAGE_H - MARGIN - TITLE_H - MARGIN) / cellH));
  const perPage = COLS * rowsPerPage;

  const pages = [];
  for (let i = 0; i < visible.length; i += perPage) {
    pages.push(visible.slice(i, i + perPage));
  }

  const images = [];
  pages.forEach((pageIdioms, pageIdx) => {
    const page = document.createElement("canvas");
    page.width = PAGE_W;
    page.height = PAGE_H;
    const pctx = page.getContext("2d");
    pctx.fillStyle = "#ffffff";
    pctx.fillRect(0, 0, PAGE_W, PAGE_H);
    pctx.fillStyle = "#000000";
    pctx.textAlign = "center";
    pctx.textBaseline = "middle";
    pctx.font = 'bold 34px "PingFang SC", "Microsoft YaHei", sans-serif';
    const titleText = pages.length > 1 ? `${groupName}（${pageIdx + 1}/${pages.length}）` : groupName;
    pctx.fillText(titleText, PAGE_W / 2, MARGIN + TITLE_H / 2);

    pageIdioms.forEach((idiom, idx) => {
      const col = idx % COLS;
      const row = Math.floor(idx / COLS);
      const x = MARGIN + col * cellW;
      const y = MARGIN + TITLE_H + row * cellH;
      const tmp = document.createElement("canvas");
      renderIdiomPrintCard(tmp, idiom, cellW - 16, cellH - 16);
      pctx.drawImage(tmp, x + 8, y + 8);
    });
    images.push(page.toDataURL("image/png"));
  });
  return images;
}

// 单条成语打印格：方框 + 4 字 2×2 大字（楷体轮廓，黑色），不含解读文字。
function renderIdiomPrintCard(canvas, idiom, W, H) {
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, W, H);
  ctx.save();
  ctx.strokeStyle = "#cc0000";
  ctx.lineWidth = 2;
  ctx.strokeRect(1, 1, W - 2, H - 2);
  ctx.restore();

  // 打印：一个成语的字横排成一行（1×N），不是 2×2。
  const chars = Array.from(idiom);
  const cols = Math.max(1, chars.length);
  const cw = W / cols;
  const cellSize = Math.min(cw, H);
  chars.forEach((char, idx) => {
    const cx = idx * cw + (cw - cellSize) / 2;
    const cy = (H - cellSize) / 2;
    const pad = 8;
    const glyph = state.idiom.glyphs[char];
    let drew = false;
    const normalized = glyph ? normalizeTracePayload(glyph) : null;
    if (normalized && normalized.strokes && normalized.strokes.length) {
      const map = (pt) => ({
        x: cx + pad + (Math.max(0, Math.min(1000, Number(pt?.[0]) || 0)) / 1000) * (cellSize - 2 * pad),
        y: cy + pad + (Math.max(0, Math.min(1000, Number(pt?.[1]) || 0)) / 1000) * (cellSize - 2 * pad),
      });
      ctx.save();
      ctx.fillStyle = "#222222";
      const outlines = normalized.outlines;
      if (Array.isArray(outlines) && outlines.length === normalized.strokes.length) {
        for (const outline of outlines) {
          if (!Array.isArray(outline) || !outline.length) continue;
          ctx.beginPath();
          for (const contour of outline) {
            if (!Array.isArray(contour) || contour.length < 3) continue;
            const p0 = map(contour[0]);
            ctx.moveTo(p0.x, p0.y);
            for (let k = 1; k < contour.length; k++) {
              const p = map(contour[k]);
              ctx.lineTo(p.x, p.y);
            }
            ctx.closePath();
          }
          ctx.fill("evenodd");
        }
        drew = true;
      }
      ctx.restore();
    }
    if (!drew) {
      ctx.save();
      ctx.fillStyle = "#222222";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.font = `${Math.floor(cellSize * 0.7)}px "Kaiti SC", "STKaiti", Kaiti, "楷体", "KaiTi", serif`;
      ctx.fillText(char, cx + cellSize / 2, cy + cellSize / 2);
      ctx.restore();
    }
  });
}

function populateIdiomDeviceSelect(hardware) {
  const select = qs("#idiom-device-select");
  if (!select) return;
  const previous = select.value;
  select.innerHTML = "";
  if (!hardware.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "无在线硬件";
    select.append(option);
    return;
  }
  hardware.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.device_id;
    option.textContent = item.device_id;
    select.append(option);
  });
  select.value = hardware.some((item) => item.device_id === previous)
    ? previous
    : hardware[0].device_id;
}

// --- conversation half-duplex state machine (mirrors conversation.c) ---
function simSetConversation(enabled, reason) {
  if (enabled === sim.convOn) {
    return;
  }
  if (enabled) {
    if (!sim.authed) {
      setBanner("#sim-banner", "未鉴权，无法进入对话模式。");
      return;
    }
    sim.convOn = true;
    simLog("system", `conversation ON (${reason})`);
    simEnterListening();
  } else {
    const wasBusy = sim.screen === "thinking" || sim.screen === "playback";
    sim.convOn = false;
    simLog("system", `conversation OFF (${reason})`);
    if (wasBusy) {
      simSend({ type: "barge_in", reason: "conversation_off" });
    }
    simCloseUtterance();
    simStopCapture();
    simStopPlayback();
    simSetScreen("idle");
  }
  simRender();
}

async function simEnterListening() {
  sim.vadActive = false;
  sim.preRoll = [];
  simSetScreen("recording");
  await simStartCapture();
  sim.captureEnabled = true;
  simRender();
}

function simSend(payload) {
  if (sim.socket && sim.socket.readyState === WebSocket.OPEN) {
    sim.socket.send(JSON.stringify(payload));
    if (payload.type !== "heartbeat") {
      simLog("outgoing", payload);
    }
  }
}

function simCloseUtterance() {
  if (sim.vadActive) {
    sim.vadActive = false;
    simSend({ type: "audio_end" });
  }
}

async function simStartCapture() {
  if (sim.processorNode) {
    return; // already capturing
  }
  const Ctor = window.AudioContext || window.webkitAudioContext;
  if (!Ctor) {
    setBanner("#sim-banner", "浏览器不支持 AudioContext。");
    return;
  }
  if (!sim.audioContext) {
    sim.audioContext = new Ctor();
  }
  await sim.audioContext.resume();
  sim.vadThreshold = Number(qs("#sim-vad-threshold").value || "0.02");
  sim.vadSilenceMs = Number(qs("#sim-vad-silence-ms").value || "700");
  try {
    sim.mediaStream = await getMicStream({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (error) {
    setBanner("#sim-banner", `麦克风获取失败：${error.message}`);
    return;
  }
  sim.sourceNode = sim.audioContext.createMediaStreamSource(sim.mediaStream);
  sim.processorNode = sim.audioContext.createScriptProcessor(4096, 1, 1);
  sim.sourceNode.connect(sim.processorNode);
  sim.processorNode.connect(sim.audioContext.destination);
  sim.processorNode.onaudioprocess = simOnAudioProcess;
}

function simStopCapture() {
  sim.captureEnabled = false;
  if (sim.processorNode) {
    sim.processorNode.onaudioprocess = null;
    try { sim.processorNode.disconnect(); } catch (e) {}
    sim.processorNode = null;
  }
  if (sim.sourceNode) {
    try { sim.sourceNode.disconnect(); } catch (e) {}
    sim.sourceNode = null;
  }
  if (sim.mediaStream) {
    sim.mediaStream.getTracks().forEach((t) => t.stop());
    sim.mediaStream = null;
  }
}

function simOnAudioProcess(event) {
  if (!sim.captureEnabled || !sim.socket || sim.socket.readyState !== WebSocket.OPEN) {
    return;
  }
  const input = event.inputBuffer.getChannelData(0);
  const down = downsampleTo16k(input, sim.audioContext.sampleRate);
  const pcm16 = floatToPcm16(down);
  const rms = rmsOfPcm16(pcm16);
  const nowMs = Date.now();

  if (!sim.vadActive) {
    // Keep a short rolling pre-roll so the speech onset is not clipped.
    sim.preRoll.push(int16ToBytes(pcm16).slice());
    if (sim.preRoll.length > 8) sim.preRoll.shift();
    if (rms >= sim.vadThreshold) {
      const playing = sim.screen === "playback" || sim.playbackSources.length > 0;
      if (playing && nowMs < sim.bargeGuardUntil) {
        return; // ignore AEC convergence transients right after playback starts
      }
      sim.vadActive = true;
      sim.vadLastVoiceAt = nowMs;
      if (playing) {
        // Full-duplex barge-in: stop local playback now and tell the server to
        // cancel the current turn before the new uplink (server also treats
        // audio_start as a barge-in, but this is immediate + explicit).
        simStopPlayback();
        simSend({ type: "barge_in", reason: "vad_start" });
        simSetScreen("recording");
      }
      simSend({ type: "audio_start", user_id: qs("#sim-user-id").value || "default-user", codec: "pcm_s16le", sample_rate_hz: 16000 });
      for (const chunk of sim.preRoll.splice(0, sim.preRoll.length)) {
        sim.socket.send(chunk);
      }
    }
    return;
  }

  // In speech: stream every frame; end on trailing silence.
  sim.socket.send(int16ToBytes(pcm16).slice());
  if (rms >= sim.vadThreshold) {
    sim.vadLastVoiceAt = nowMs;
  } else if (nowMs - sim.vadLastVoiceAt >= sim.vadSilenceMs) {
    sim.vadActive = false;
    simSend({ type: "audio_end" });
    sim.captureEnabled = false; // half-duplex: wait for the reply
    simSetScreen("thinking");
  }
}

// A leftover odd byte can occur if a paced frame ever splits a 16-bit sample;
// carry it to the next frame so samples stay aligned (defensive — backend
// frames are 1280B/even today).
let simPcmRemainder = new Uint8Array(0);

function simEnsurePlaybackContext() {
  if (sim.playbackContext) {
    return sim.playbackContext;
  }
  const Ctor = window.AudioContext || window.webkitAudioContext;
  if (!Ctor) return null;
  // One long-lived context at the hardware default rate. Each buffer carries
  // its own PCM sample rate (see simSchedulePcm) and WebAudio resamples it on
  // output. Forcing the context to the PCM rate is fragile: some browsers
  // ignore the request (or the fallback path hands back a default-rate
  // context), and then PCM-rate samples placed in a differently-rated buffer
  // play at the wrong speed — the buzzing "电音". Creating the buffer at the
  // real PCM rate (like schedulePcmPlayback does on the conversation page)
  // avoids that entirely.
  sim.playbackContext = new Ctor();
  sim.playbackBaseTime = 0;
  sim.playbackCursorFrames = 0;
  return sim.playbackContext;
}

function simSchedulePcm(arrayBuffer) {
  const ctx = simEnsurePlaybackContext();
  if (!ctx) return;
  ctx.resume();

  // Concatenate any carried remainder, then keep samples 2-byte aligned.
  const incoming = new Uint8Array(arrayBuffer);
  let bytes = incoming;
  if (simPcmRemainder.length) {
    bytes = new Uint8Array(simPcmRemainder.length + incoming.length);
    bytes.set(simPcmRemainder, 0);
    bytes.set(incoming, simPcmRemainder.length);
  }
  const usableLen = bytes.length - (bytes.length % 2);
  simPcmRemainder = bytes.slice(usableLen);
  if (usableLen === 0) return;

  const pcmRate = sim.playbackSampleRate || 16000;
  const samples = usableLen / 2;
  // Buffer at the true PCM rate; WebAudio resamples to the context's output
  // rate. (Do NOT use ctx.sampleRate here — see simEnsurePlaybackContext.)
  const buffer = ctx.createBuffer(1, samples, pcmRate);
  const channel = buffer.getChannelData(0);
  const view = new DataView(bytes.buffer, bytes.byteOffset, usableLen);
  for (let i = 0; i < samples; i++) {
    channel[i] = view.getInt16(i * 2, true) / 32768;
  }

  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(ctx.destination);

  // Integer-sample cursor (at the PCM rate) avoids float drift across hundreds
  // of frames. pcmRate is constant within one playback run (set at tts_start).
  const now = ctx.currentTime;
  if (sim.playbackBaseTime < now) {
    // First frame of a run, or we fell behind (underrun): re-anchor + small
    // lead so we never schedule in the past (which itself causes glitches).
    sim.playbackBaseTime = now + 0.03;
    sim.playbackCursorFrames = 0;
  }
  const startTime = sim.playbackBaseTime + sim.playbackCursorFrames / pcmRate;
  source.start(startTime);
  sim.playbackCursorFrames += samples;
  sim.playbackSources.push(source);
  source.addEventListener("ended", () => {
    sim.playbackSources = sim.playbackSources.filter((n) => n !== source);
  });
}

function simStopPlayback() {
  sim.playbackSources.forEach((source) => {
    try { source.stop(0); } catch (e) {}
  });
  sim.playbackSources = [];
  sim.playbackBaseTime = 0;
  sim.playbackCursorFrames = 0;
  simPcmRemainder = new Uint8Array(0);
}

// Simulated WakeNet hit (phase 2): wake word during playback -> barge-in. Maps
// to the firmware VOICE_CONV_EVENT_BARGE_DETECTED logic input.
function simWakeBarge() {
  const playing = sim.screen === "playback" || sim.playbackSources.length > 0;
  if (!sim.convOn || !playing) {
    simLog("system", "wake ignored (not playing)");
    return;
  }
  simStopPlayback();
  simSend({ type: "barge_in", reason: "wake_word" });
  simEnterListening();
}

// ========================= 场景（意图场景 + 默认人设）=========================

async function loadScenesTab() {
  setBanner("#scene-default-banner", "");
  await loadDefaultSceneCard();
  await loadIntentScenes();
}

async function loadDefaultSceneCard() {
  // 基础 system prompt 复用 /api/settings/llm（运行态键不迁移，仅展示位置迁来）。
  const llm = await fetchJson("/api/settings/llm");
  state.llmSettings.systemPrompt = llm.system_prompt || "";
  state.llmSettings.systemPromptSource = llm.system_prompt_source || "unset";
  const promptInput = qs("#scene-base-system-prompt");
  if (promptInput) promptInput.value = state.llmSettings.systemPrompt;
  setText("#scene-base-prompt-source", `来源：${sourceLabel(state.llmSettings.systemPromptSource)}`);

  const profile = await fetchJson("/api/settings/bot-profile");
  state.memoryPayloads.botProfile = profile;
  const enabled = qs("#scene-botprofile-enabled");
  if (enabled) enabled.checked = Boolean(profile.enabled);
  const setVal = (sel, value) => {
    const node = qs(sel);
    if (node) node.value = value || "";
  };
  setVal("#scene-botprofile-name", profile.name);
  setVal("#scene-botprofile-persona", profile.persona);
  setVal("#scene-botprofile-style", profile.response_style);
  setVal("#scene-botprofile-custom", profile.custom_prompt);
}

async function saveSceneBasePrompt() {
  setBanner("#scene-default-banner", "");
  try {
    // /api/settings/llm 是整体覆盖：先取当前全部字段（含 api_key），只改 system_prompt
    // 回写，避免清空 model / base_url / api_key（all-or-nothing PUT 护栏）。
    const current = await fetchJson("/api/settings/llm?reveal=true");
    const data = await sendJson("/api/settings/llm", "PUT", {
      model: current.model || "",
      base_url: current.base_url || "",
      user_prompt_template: current.user_prompt_template || "",
      system_prompt: qs("#scene-base-system-prompt").value,
      api_key: current.api_key || "",
    });
    state.llmSettings.systemPrompt = data.system_prompt || "";
    state.llmSettings.systemPromptSource = data.system_prompt_source || "unset";
    setText("#scene-base-prompt-source", `来源：${sourceLabel(state.llmSettings.systemPromptSource)}`);
    setBanner("#scene-default-banner", "基础 system prompt 已保存，下一轮输入自动生效。");
    showToast("基础 system prompt 已保存", "success");
  } catch (error) {
    setBanner("#scene-default-banner", `基础 system prompt 保存失败：${error.message}`);
    showToast(`保存失败：${error.message}`, "error");
  }
}

async function saveSceneBotProfile() {
  setBanner("#scene-default-banner", "");
  try {
    const profile = await sendJson("/api/settings/bot-profile", "PUT", {
      enabled: qs("#scene-botprofile-enabled").checked,
      name: qs("#scene-botprofile-name").value,
      persona: qs("#scene-botprofile-persona").value,
      response_style: qs("#scene-botprofile-style").value,
      custom_prompt: qs("#scene-botprofile-custom").value,
    });
    state.memoryPayloads.botProfile = profile;
    setBanner("#scene-default-banner", "Bot 画像已保存。");
    showToast("Bot 画像已保存", "success");
  } catch (error) {
    setBanner("#scene-default-banner", `Bot 画像保存失败：${error.message}`);
    showToast(`保存失败：${error.message}`, "error");
  }
}

async function loadIntentScenes() {
  const data = await fetchJson("/api/scenes");
  state.scenes = Array.isArray(data.scenes) ? data.scenes : [];
  renderSceneCards();
}

function renderSceneCards() {
  const container = qs("#scene-list");
  if (!container) return;
  container.innerHTML = "";
  if (!state.scenes.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "暂无意图场景。";
    container.appendChild(empty);
    return;
  }
  state.scenes.forEach((scene) => container.appendChild(buildSceneCard(scene)));
}

function buildSceneCard(scene) {
  const card = document.createElement("div");
  card.className = "panel scene-card";
  card.dataset.sceneId = scene.scene_id;
  card.dataset.sceneKind = scene.kind || "persona";
  const isAction = (scene.kind || "persona") === "action";
  // 动作型场景（汉字/成语）：只配「启用 + 进入触发词」，退出由屏幕双击关闭 overlay。
  // 人设型场景（英语）：完整配置（人设 / ASR 热词 / 进退提示音 / 退出触发词）。
  const personaBody = `
    <label class="label">退出触发词（每行一个）</label>
    <textarea class="secret-editor" data-scene-field="exit_keywords"></textarea>
    <label class="label">场景人设（persona prompt，叠加在默认人设之上）</label>
    <textarea class="secret-editor" data-scene-field="persona_prompt"></textarea>
    <label class="label">ASR 热词（每行一个，进入该场景后注入语音识别软性偏置；如英语场景填常见英文词，约 100 词以内；留空=按默认中英混合识别）</label>
    <textarea class="secret-editor" data-scene-field="asr_hotwords"></textarea>
    <label class="label">进入语音提示</label>
    <div class="scene-prompt-row">
      <input class="config-input" data-scene-field="enter_prompt_text" />
      <button class="button secondary" data-scene-action="preview-enter">试听</button>
    </div>
    <label class="label">退出语音提示</label>
    <div class="scene-prompt-row">
      <input class="config-input" data-scene-field="exit_prompt_text" />
      <button class="button secondary" data-scene-action="preview-exit">试听</button>
    </div>
    <div class="button-row compact">
      <button class="button secondary" data-scene-action="rebuild">保存并重建提示音缓存</button>
    </div>`;
  const actionBody = `
    <p class="small-meta">动作型场景：命中触发词即触发一次性动作，退出请在设备屏幕上双击关闭。</p>`;
  card.innerHTML = `
    <div class="panel-header compact-header">
      <div>
        <h3 class="scene-card-title"></h3>
        <p class="small-meta">id: <span class="scene-card-id"></span> · 类型：<span class="scene-card-kind"></span> · 来源：<span class="scene-card-source"></span></p>
      </div>
      <div class="button-row compact">
        <button class="button primary" data-scene-action="save">保存</button>
        <button class="button secondary" data-scene-action="reset">恢复默认</button>
      </div>
    </div>
    <div class="banner" data-scene-banner hidden></div>
    <label class="scene-checkbox">
      <input type="checkbox" data-scene-field="enabled" />
      <span>启用该场景（关闭后不再识别进入）</span>
    </label>
    <label class="label">进入触发词（每行一个，子串匹配，不区分大小写）</label>
    <textarea class="secret-editor" data-scene-field="enter_keywords"></textarea>
    ${isAction ? actionBody : personaBody}
  `;
  card.querySelector(".scene-card-title").textContent = scene.name || scene.scene_id;
  card.querySelector(".scene-card-id").textContent = scene.scene_id;
  card.querySelector(".scene-card-kind").textContent = isAction ? "动作" : "人设";
  card.querySelector(".scene-card-source").textContent =
    scene.source === "runtime_override" ? "运行态覆盖" : "默认";
  card.querySelector('[data-scene-field="enabled"]').checked = Boolean(scene.enabled);
  card.querySelector('[data-scene-field="enter_keywords"]').value = (scene.enter_keywords || []).join("\n");
  const setField = (name, value) => {
    const el = card.querySelector(`[data-scene-field="${name}"]`);
    if (el) el.value = value;
  };
  setField("exit_keywords", (scene.exit_keywords || []).join("\n"));
  setField("persona_prompt", scene.persona_prompt || "");
  setField("asr_hotwords", (scene.asr_hotwords || []).join("\n"));
  setField("enter_prompt_text", scene.enter_prompt_text || "");
  setField("exit_prompt_text", scene.exit_prompt_text || "");
  return card;
}

function gatherSceneCardInput(card) {
  const lines = (sel) => {
    const el = card.querySelector(sel);
    if (!el) return [];
    return el.value
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
  };
  const value = (sel) => {
    const el = card.querySelector(sel);
    return el ? el.value : "";
  };
  return {
    enabled: card.querySelector('[data-scene-field="enabled"]').checked,
    enter_keywords: lines('[data-scene-field="enter_keywords"]'),
    exit_keywords: lines('[data-scene-field="exit_keywords"]'),
    persona_prompt: value('[data-scene-field="persona_prompt"]'),
    asr_hotwords: lines('[data-scene-field="asr_hotwords"]'),
    enter_prompt_text: value('[data-scene-field="enter_prompt_text"]'),
    exit_prompt_text: value('[data-scene-field="exit_prompt_text"]'),
  };
}

function setSceneCardBanner(card, message, isError = false) {
  const banner = card.querySelector("[data-scene-banner]");
  if (!banner) return;
  banner.textContent = message || "";
  banner.hidden = !message;
  banner.classList.toggle("error", Boolean(isError));
}

async function saveScene(card, { rebuild = false } = {}) {
  const sceneId = card.dataset.sceneId;
  setSceneCardBanner(card, "");
  try {
    await sendJson(`/api/scenes/${encodeURIComponent(sceneId)}`, "PUT", gatherSceneCardInput(card));
    if (rebuild) {
      const result = await sendJson(
        `/api/scenes/${encodeURIComponent(sceneId)}/prompt-tts/rebuild`,
        "POST",
        {}
      );
      const failed = ["enter", "exit"].filter(
        (k) => result[k] && result[k].status === "error"
      );
      if (failed.length) {
        setSceneCardBanner(card, `已保存，但提示音重建失败：${failed.join("/")}`, true);
      } else {
        setSceneCardBanner(card, "已保存并重建提示音缓存。");
      }
    } else {
      setSceneCardBanner(card, "已保存，下一轮输入自动生效。");
    }
    showToast("场景配置已保存", "success");
    await loadIntentScenes();
  } catch (error) {
    setSceneCardBanner(card, `保存失败：${error.message}`, true);
    showToast(`场景保存失败：${error.message}`, "error");
  }
}

async function resetScene(card) {
  const sceneId = card.dataset.sceneId;
  setSceneCardBanner(card, "");
  try {
    await sendJson(`/api/scenes/${encodeURIComponent(sceneId)}/reset`, "POST", {});
    showToast("已恢复默认", "success");
    await loadIntentScenes();
  } catch (error) {
    setSceneCardBanner(card, `恢复默认失败：${error.message}`, true);
    showToast(`恢复默认失败：${error.message}`, "error");
  }
}

async function previewScenePrompt(card, kind, button) {
  const sceneId = card.dataset.sceneId;
  if (button) {
    button.disabled = true;
    button.classList.add("is-loading");
  }
  try {
    const response = await fetch(
      `/api/scenes/${encodeURIComponent(sceneId)}/prompt-tts?kind=${encodeURIComponent(kind)}`
    );
    if (!response.ok) {
      throw new Error(`${response.status}`);
    }
    const sampleRate =
      Number(
        response.headers.get("X-TTS-Sample-Rate-Hz") ||
          state.speechSettings.ttsSampleRateHz ||
          16000
      ) || 16000;
    await ensurePlaybackAudioContext();
    stopPlayback("idle");
    schedulePcmPlayback(await response.arrayBuffer(), { sampleRateHz: sampleRate });
  } catch (error) {
    const msg = /404/.test(String(error.message)) ? "提示音文案为空" : error.message;
    showToast(`试听失败：${msg}`, "error");
  } finally {
    if (button) {
      button.disabled = false;
      button.classList.remove("is-loading");
    }
  }
}

function bindScenesEvents() {
  const basePromptSave = qs("#scene-base-prompt-save");
  if (basePromptSave) {
    basePromptSave.addEventListener("click", () => saveSceneBasePrompt());
  }
  const botProfileSave = qs("#scene-botprofile-save");
  if (botProfileSave) {
    botProfileSave.addEventListener("click", () => saveSceneBotProfile());
  }
  const list = qs("#scene-list");
  if (list) {
    list.addEventListener("click", (event) => {
      const button = event.target.closest("[data-scene-action]");
      if (!button) return;
      const card = button.closest(".scene-card");
      if (!card) return;
      const action = button.dataset.sceneAction;
      if (action === "save") saveScene(card);
      else if (action === "reset") resetScene(card);
      else if (action === "rebuild") saveScene(card, { rebuild: true });
      else if (action === "preview-enter") previewScenePrompt(card, "enter", button);
      else if (action === "preview-exit") previewScenePrompt(card, "exit", button);
    });
  }
}

function bindEvents() {
  const themeToggle = qs("#theme-toggle");
  if (themeToggle) {
    themeToggle.addEventListener("click", toggleTheme);
  }

  document.querySelectorAll("[data-tab-target]").forEach((button) => {
    button.addEventListener("click", () => {
      switchTab(button.dataset.tabTarget);
    });
  });

  bindScenesEvents();

  qs("#refresh-status").addEventListener("click", () => {
    loadStatus();
  });

  const refreshMetrics = qs("#refresh-metrics");
  if (refreshMetrics) {
    refreshMetrics.addEventListener("click", () => {
      loadTokenMetrics().catch((error) => {
        setBanner("#metrics-banner", `Token 统计加载失败：${error.message}`);
        appendLog("error", `metrics load failed: ${error.message}`);
      });
    });
  }

  const gatewayConnectBle = qs("#gateway-connect-ble");
  if (gatewayConnectBle) {
    gatewayConnectBle.addEventListener("click", () => {
      connectGatewayBle();
    });
  }
  const gatewayConnectCloud = qs("#gateway-connect-cloud");
  if (gatewayConnectCloud) {
    gatewayConnectCloud.addEventListener("click", () => {
      connectGatewayCloud();
    });
  }
  const gatewayVolumeSlider = qs("#gateway-volume-slider");
  if (gatewayVolumeSlider) {
    gatewayVolumeSlider.addEventListener("input", (event) => {
      state.gateway.volume = Math.max(
        0,
        Math.min(100, Number(event.target.value || "0") || 0)
      );
      updateGatewayUi();
    });
  }
  const gatewaySendVolume = qs("#gateway-send-volume");
  if (gatewaySendVolume) {
    gatewaySendVolume.addEventListener("click", () => {
      sendGatewayVolume();
    });
  }
  qsa(".gateway-color-test").forEach((button) => {
    button.addEventListener("click", () => {
      dispatchGatewayColorTest(button.dataset.colorTest || "bars");
    });
  });
  const gatewayClearLog = qs("#gateway-clear-log");
  if (gatewayClearLog) {
    gatewayClearLog.addEventListener("click", () => {
      const container = qs("#gateway-log");
      if (container) {
        container.innerHTML = "";
      }
    });
  }

  const simConnectBtn = qs("#sim-connect");
  if (simConnectBtn) {
    simConnectBtn.addEventListener("click", simConnect);
  }
  const simDisconnectBtn = qs("#sim-disconnect");
  if (simDisconnectBtn) {
    simDisconnectBtn.addEventListener("click", simDisconnect);
  }
  const simScreenMock = qs("#sim-screen-mock");
  if (simScreenMock) {
    const tapCat = () => {
      if (!sim.authed) {
        showToast("请先点「上电（连接）」", "error");
        return;
      }
      simBlink(); // immediate eye-blink feedback on tap
      simSetConversation(!sim.convOn, "tap");
    };
    simScreenMock.addEventListener("click", tapCat);
    simScreenMock.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        tapCat();
      }
    });
  }
  const simCharacter = qs("#sim-character");
  if (simCharacter) {
    // Load the backend-configured character (default princess), then persist
    // changes back so it's a real "后台" setting (survives reload / drives device).
    fetchJson("/api/settings/ui")
      .then((data) => {
        if (data && data.character) {
          sim.character = data.character;
          simCharacter.value = data.character;
          simRender();
        }
      })
      .catch(() => {});
    simCharacter.addEventListener("change", async () => {
      const character = simCharacter.value;
      sim.character = character;
      simRender();
      try {
        await sendJson("/api/settings/ui", "PUT", { character });
        simLog("system", `character -> ${character}`);
      } catch (error) {
        setBanner("#sim-banner", `形象保存失败：${error.message}`);
      }
    });
  }

  const simDuplexMode = qs("#sim-duplex-mode");
  if (simDuplexMode) {
    sim.duplexMode = simDuplexMode.value || "full";
    simDuplexMode.addEventListener("change", () => {
      sim.duplexMode = simDuplexMode.value;
      simLog("system", `duplex mode: ${sim.duplexMode}`);
      simRender();
    });
  }
  const simWake = qs("#sim-wake");
  if (simWake) {
    simWake.addEventListener("click", simWakeBarge);
  }
  const simClearLog = qs("#sim-clear-log");
  if (simClearLog) {
    simClearLog.addEventListener("click", () => {
      const container = qs("#sim-log");
      if (container) {
        container.innerHTML = "";
      }
    });
  }
  simRender();

  qs("#refresh-memory").addEventListener("click", () => {
    loadUsers();
  });

  qs("#memory-user-select").addEventListener("change", (event) => {
    loadMemoryForUser(event.target.value);
  });

  document.querySelectorAll("[data-memory-section]").forEach((button) => {
    button.addEventListener("click", () => {
      state.memorySection = button.dataset.memorySection;
      state.selectedMemoryItemId = null;
      document.querySelectorAll("[data-memory-section]").forEach((node) => {
        node.classList.toggle("active", node === button);
      });
      loadMemorySection();
    });
  });

  qs("#save-memory-item").addEventListener("click", () => {
    saveMemoryProfile().catch((error) => {
      setBanner("#memory-banner", `保存失败：${error.message}`);
      appendLog("error", `memory save failed: ${error.message}`);
    });
  });

  qs("#delete-memory-item").addEventListener("click", () => {
    deleteSelectedMemoryItem().catch((error) => {
      setBanner("#memory-banner", `删除失败：${error.message}`);
      appendLog("error", `memory delete failed: ${error.message}`);
    });
  });

  qs("#delete-all-turns").addEventListener("click", () => {
    deleteAllTurns().catch((error) => {
      setBanner("#memory-banner", `删除失败：${error.message}`);
      appendLog("error", `delete all turns failed: ${error.message}`);
    });
  });

  qs("#llm-edit-toggle").addEventListener("click", enterLLMEditMode);
  qs("#llm-visibility-toggle").addEventListener("click", () => {
    toggleLLMVisibility();
  });
  qs("#llm-save").addEventListener("click", () => {
    saveLLMSettings();
  });
  qs("#llm-cancel").addEventListener("click", cancelLLMEditMode);
  qs("#speech-edit-toggle").addEventListener("click", enterSpeechEditMode);
  qs("#speech-visibility-toggle").addEventListener("click", () => {
    toggleSpeechVisibility();
  });
  qs("#speech-save").addEventListener("click", () => {
    saveSpeechSettings();
  });
  qs("#speech-cancel").addEventListener("click", cancelSpeechEditMode);

  // ===== Conversation composer =====
  const convSend = qs("#conv-send");
  if (convSend) {
    convSend.addEventListener("click", () => {
      sendTextInput();
    });
  }
  const convInput = qs("#conv-input");
  if (convInput) {
    convInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendTextInput();
      }
    });
    convInput.addEventListener("input", autoResizeComposer);
  }
  const convMic = qs("#conv-mic");
  if (convMic) {
    convMic.addEventListener("click", toggleMic);
  }
  const convLogToggle = qs("#conv-log-toggle");
  if (convLogToggle) {
    convLogToggle.addEventListener("click", toggleLogPane);
  }
  const convLogCollapse = qs("#conv-log-collapse");
  if (convLogCollapse) {
    convLogCollapse.addEventListener("click", toggleLogPane);
  }
  const convLoadHistory = qs("#conv-load-history");
  if (convLoadHistory) {
    convLoadHistory.addEventListener("click", loadConversationHistory);
  }
  const convNewSession = qs("#conv-new-session");
  if (convNewSession) {
    convNewSession.addEventListener("click", startNewSession);
  }
  const convDeviceSay = qs("#conv-device-say");
  if (convDeviceSay) {
    convDeviceSay.addEventListener("click", dispatchDeviceSay);
  }
  const convDeviceConversation = qs("#conv-device-conversation");
  if (convDeviceConversation) {
    convDeviceConversation.addEventListener("click", toggleDeviceConversation);
  }
  const convDeviceMonitor = qs("#conv-device-monitor");
  if (convDeviceMonitor) {
    convDeviceMonitor.addEventListener("click", toggleDeviceMonitor);
  }
  const convDeviceSelect = qs("#conv-device-select");
  if (convDeviceSelect) {
    convDeviceSelect.addEventListener("change", () => {
      // Per-device conversation state is unknown after a switch; reset the
      // local toggle so the button doesn't imply the new device is engaged.
      state.deviceConversationOn = false;
      updateDeviceVolumeControl();
    });
  }
  const convVolumeToggle = qs("#conv-volume-toggle");
  if (convVolumeToggle) {
    convVolumeToggle.addEventListener("click", toggleDeviceVolumePanel);
  }
  const convVolumeSlider = qs("#conv-volume-slider");
  if (convVolumeSlider) {
    convVolumeSlider.addEventListener("input", handleDeviceVolumeInput);
  }
  const convVolumeApply = qs("#conv-volume-apply");
  if (convVolumeApply) {
    convVolumeApply.addEventListener("click", () => dispatchDeviceVolume(state.deviceVolume));
  }
  const convVolumeMute = qs("#conv-volume-mute");
  if (convVolumeMute) {
    convVolumeMute.addEventListener("click", toggleDeviceMute);
  }

  const tracePreviewBtn = qs("#trace-preview-web");
  if (tracePreviewBtn) {
    tracePreviewBtn.addEventListener("click", tracePreviewWeb);
  }
  const traceDispatchBtn = qs("#trace-dispatch-hardware");
  if (traceDispatchBtn) {
    traceDispatchBtn.addEventListener("click", traceDispatchHardware);
  }

  // 生字管理事件绑定
  const vocabPrintBtn = qs("#vocab-print");
  if (vocabPrintBtn) {
    vocabPrintBtn.addEventListener("click", vocabPrint);
  }
  const vocabAddBtn = qs("#vocab-add");
  if (vocabAddBtn) {
    vocabAddBtn.addEventListener("click", vocabAddFromInput);
  }
  const vocabClearBtn = qs("#vocab-clear");
  if (vocabClearBtn) {
    vocabClearBtn.addEventListener("click", vocabClear);
  }
  const vocabImportBtn = qs("#vocab-import");
  if (vocabImportBtn) {
    vocabImportBtn.addEventListener("click", vocabImportFromMemory);
  }
  const vocabGroupSelect = qs("#vocab-group-select");
  if (vocabGroupSelect) {
    vocabGroupSelect.addEventListener("change", (event) => {
      vocabSwitchGroup(event.target.value);
    });
  }
  const vocabStatusSelect = qs("#vocab-status-filter");
  if (vocabStatusSelect) {
    vocabStatusSelect.addEventListener("change", (event) => {
      state.vocab.activeStatusFilter = event.target.value || "all";
      persistVocabGroups();
      renderVocabGrid();
    });
  }
  const vocabGroupCreateBtn = qs("#vocab-group-create");
  if (vocabGroupCreateBtn) {
    vocabGroupCreateBtn.addEventListener("click", vocabCreateGroup);
  }
  const vocabGroupDeleteBtn = qs("#vocab-group-delete");
  if (vocabGroupDeleteBtn) {
    vocabGroupDeleteBtn.addEventListener("click", vocabDeleteGroup);
  }
  const vocabSearchInput = qs("#vocab-search");
  if (vocabSearchInput) {
    vocabSearchInput.addEventListener("input", () => vocabSearch(vocabSearchInput.value));
  }
  const vocabGroupNameInput = qs("#vocab-group-name");
  if (vocabGroupNameInput) {
    vocabGroupNameInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        vocabCreateGroup();
      }
    });
  }
  const vocabDispatchAllBtn = qs("#vocab-dispatch-all");
  if (vocabDispatchAllBtn) {
    vocabDispatchAllBtn.addEventListener("click", vocabDispatchAll);
  }
  const vocabGenerateAllBtn = qs("#vocab-generate-all");
  if (vocabGenerateAllBtn) {
    vocabGenerateAllBtn.addEventListener("click", vocabGenerateAll);
  }
  const promptTemplateEl = qs("#vocab-prompt-template");
  if (promptTemplateEl) {
    promptTemplateEl.value = state.vocab.promptTemplate || "";
    let promptSaveTimer = null;
    promptTemplateEl.addEventListener("input", () => {
      state.vocab.promptTemplate = promptTemplateEl.value;
      clearTimeout(promptSaveTimer);
      promptSaveTimer = setTimeout(() => saveVocabGroupsToServer(), 1000);
    });
  }
  // 生字管理：可折叠分区（移动端默认收起）
  document.querySelectorAll(".vocab-section-toggle").forEach((header) => {
    header.addEventListener("click", () => {
      header.closest(".vocab-section").classList.toggle("collapsed");
    });
  });
  // 解读生成模版默认收起
  const templateSection = promptTemplateEl && promptTemplateEl.closest(".vocab-section");
  if (templateSection) templateSection.classList.add("collapsed");
  if (window.matchMedia("(max-width: 640px)").matches) {
    document.querySelectorAll(".vocab-section").forEach((s) => s.classList.add("collapsed"));
  }

  // 成语学习事件绑定
  const idiomAddBtn = qs("#idiom-add");
  if (idiomAddBtn) idiomAddBtn.addEventListener("click", idiomAddFromInput);
  const idiomClearBtn = qs("#idiom-clear");
  if (idiomClearBtn) idiomClearBtn.addEventListener("click", idiomClear);
  const idiomPrintBtn = qs("#idiom-print");
  if (idiomPrintBtn) idiomPrintBtn.addEventListener("click", idiomPrint);
  const idiomGroupSelect = qs("#idiom-group-select");
  if (idiomGroupSelect) {
    idiomGroupSelect.addEventListener("change", (event) => idiomSwitchGroup(event.target.value));
  }
  const idiomStatusSelect = qs("#idiom-status-filter");
  if (idiomStatusSelect) {
    idiomStatusSelect.addEventListener("change", (event) => {
      state.idiom.activeStatusFilter = event.target.value || "all";
      persistIdiomGroups();
      renderIdiomGrid();
    });
  }
  const idiomGroupCreateBtn = qs("#idiom-group-create");
  if (idiomGroupCreateBtn) idiomGroupCreateBtn.addEventListener("click", idiomCreateGroup);
  const idiomGroupDeleteBtn = qs("#idiom-group-delete");
  if (idiomGroupDeleteBtn) idiomGroupDeleteBtn.addEventListener("click", idiomDeleteGroup);
  const idiomGroupNameInput = qs("#idiom-group-name");
  if (idiomGroupNameInput) {
    idiomGroupNameInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        idiomCreateGroup();
      }
    });
  }
  const idiomDispatchAllBtn = qs("#idiom-dispatch-all");
  if (idiomDispatchAllBtn) idiomDispatchAllBtn.addEventListener("click", idiomDispatchAll);
  const idiomGenerateAllBtn = qs("#idiom-generate-all");
  if (idiomGenerateAllBtn) idiomGenerateAllBtn.addEventListener("click", idiomGenerateAll);
  const idiomExplTemplateEl = qs("#idiom-explanation-template");
  if (idiomExplTemplateEl) {
    idiomExplTemplateEl.value = state.idiom.explanationTemplate || "";
    let t = null;
    idiomExplTemplateEl.addEventListener("input", () => {
      state.idiom.explanationTemplate = idiomExplTemplateEl.value;
      clearTimeout(t);
      t = setTimeout(() => saveIdiomGroupsToServer(), 1000);
    });
  }
  const idiomStoryTemplateEl = qs("#idiom-story-template");
  if (idiomStoryTemplateEl) {
    idiomStoryTemplateEl.value = state.idiom.storyTemplate || "";
    let t = null;
    idiomStoryTemplateEl.addEventListener("input", () => {
      state.idiom.storyTemplate = idiomStoryTemplateEl.value;
      clearTimeout(t);
      t = setTimeout(() => saveIdiomGroupsToServer(), 1000);
    });
  }

  // 打开控制台时恢复已生成的生字解读记录（含可试听标记）。
  if (qs("#vocab-grid")) {
    vocabLoadExplanations();
  }
  // 打开控制台时恢复已生成的成语解读/故事记录（含可试听标记）。
  if (qs("#idiom-grid")) {
    idiomLoadContents();
  }

  const bargeInButton = qs("#send-barge-in");
  if (bargeInButton) {
    bargeInButton.addEventListener("click", () => {
      state.speechRtc.lastBargeInAt = Date.now();
      state.speechRtc.lastBargeInLatencyMs = null;
      sendControl({ type: "barge_in", reason: "manual" });
    });
  }
  const textInputButton = qs("#send-text-input");
  if (textInputButton) {
    textInputButton.addEventListener("click", async () => {
      const wsText = qs("#ws-text-input");
      const text = (wsText && wsText.value.trim()) || "";
      if (!text) {
        return;
      }
      if (!(await ensureSpeechSocketReady())) {
        setBanner("#conv-banner", "WebSocket 未连接或鉴权失败，无法发送。");
        return;
      }
      appendUserBubble(text, "text");
      state.conversation.currentAssistantBubble = null;
      state.conversation.streamedText = "";
      state.speechRtc.lastBargeInAt = Date.now();
      state.speechRtc.lastBargeInLatencyMs = null;
      sendControl({
        type: "text_input",
        user_id: convUserId(),
        text,
      });
    });
  }
  const clearLog = qs("#clear-log");
  if (clearLog) {
    clearLog.addEventListener("click", () => {
      qs("#message-log").innerHTML = "";
    });
  }

  // hidden placeholder buttons keep the speech toggle logic available
  const speechStart = qs("#speech-start");
  if (speechStart) {
    speechStart.addEventListener("click", () => {
      startSpeechRtc();
    });
  }
  const speechStop = qs("#speech-stop");
  if (speechStop) {
    speechStop.addEventListener("click", () => {
      stopSpeechRtc();
    });
  }
}

// Resolve the auth token locally. The server never returns this secret to the
// browser; users enter it once and the console keeps it in localStorage.
function resolveAuthToken(selector) {
  const el = qs(selector);
  const typed = el && el.value ? el.value.trim() : "";
  if (typed) {
    localStorage.setItem("vr_auth_token", typed);
    return typed;
  }
  return localStorage.getItem("vr_auth_token") || "";
}

// ===== 登录 / 登出 =====
const LOGIN_KEY = "vr_logged_in";

function isLoggedIn() {
  return localStorage.getItem(LOGIN_KEY) === "1";
}

function showLogin() {
  const overlay = qs("#login-overlay");
  if (overlay) overlay.classList.remove("hidden");
  const loading = qs("#login-loading");
  if (loading) loading.hidden = true;
  const form = qs("#login-form");
  if (form) form.hidden = false;
}

function showLoginLoading() {
  const overlay = qs("#login-overlay");
  if (overlay) overlay.classList.remove("hidden");
  const loading = qs("#login-loading");
  if (loading) loading.hidden = false;
  const form = qs("#login-form");
  if (form) form.hidden = true;
}

function hideLogin() {
  const overlay = qs("#login-overlay");
  if (overlay) overlay.classList.add("hidden");
}

function setupLogin() {
  const form = qs("#login-form");
  if (form) {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const user = qs("#login-user").value.trim();
      const pass = qs("#login-pass").value;
      if (user === "13701834516" && pass === "13701834516") {
        localStorage.setItem(LOGIN_KEY, "1");
        hideLogin();
        qs("#login-error").hidden = true;
        initApp();
      } else {
        qs("#login-error").hidden = false;
      }
    });
  }
  const logoutBtn = qs("#logout-btn");
  if (logoutBtn) {
    logoutBtn.addEventListener("click", () => {
      localStorage.removeItem(LOGIN_KEY);
      showLogin();
      qs("#login-user").value = "";
      qs("#login-pass").value = "";
      qs("#login-error").hidden = true;
    });
  }
}

let appInitialized = false;
async function initApp() {
  if (appInitialized) return;
  appInitialized = true;

  const cachedToken = localStorage.getItem("vr_auth_token");
  if (cachedToken) {
    for (const id of ["#sim-token", "#auth-token", "#gateway-token"]) {
      const el = qs(id);
      if (el && !el.value) el.value = cachedToken;
    }
  }
  loadVocabGroups();
  loadIdiomGroups();
  bindEvents();
  renderVocabGrid();
  renderIdiomGrid();
  initLogPane();
  updateDebugState();
  renderLLMSettings();
  renderSpeechSettings();
  updateSpeechRtcState();
  updateGatewayUi();
  await Promise.allSettled([
    loadStatus(),
    loadUsers(),
    loadConversationUsers(),
    loadLLMSettings(),
    loadSpeechSettings(),
    loadVocabGroupsFromServer(),
    loadIdiomGroupsFromServer(),
  ]);
  await loadConversationHistory({ silent: true });
  startDevicePolling();
}

async function init() {
  initTheme();
  setupLogin();
  if (isLoggedIn()) {
    showLoginLoading();
    try {
      await initApp();
      hideLogin();
    } catch (error) {
      showLogin();
      throw error;
    }
  } else {
    showLogin();
  }
}

init().catch((error) => {
  appendLog("error", `init failed: ${error.message}`);
  setBanner("#status-banner", `页面初始化失败：${error.message}`);
});
