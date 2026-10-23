from fastapi.testclient import TestClient

from voice_robot.server import app


def test_console_page_is_served() -> None:
    client = TestClient(app)

    response = client.get("/console")

    assert response.status_code == 200
    assert "Voice Robot Console" in response.text
    assert 'data-tab-target="overview"' in response.text
    assert 'data-tab-target="conversation"' in response.text
    assert 'id="tab-conversation"' in response.text
    assert 'id="conversation-thread"' in response.text
    assert 'id="conv-input"' in response.text
    assert 'id="conv-history-user"' in response.text
    assert 'id="conv-load-history"' in response.text
    assert 'id="conv-device-select"' in response.text
    assert 'id="conv-device-say"' in response.text
    assert 'id="conv-device-conversation"' in response.text
    assert 'id="conv-device-monitor"' in response.text
    assert 'id="conv-volume-toggle"' in response.text
    assert 'id="conv-volume-panel"' in response.text
    assert 'id="conv-volume-slider"' in response.text
    assert 'id="conv-volume-slider" type="range" min="0" max="100" step="1" value="0"' in response.text
    assert 'id="conv-volume-value">0</strong>' in response.text
    assert 'id="conv-volume-mute"' in response.text
    assert 'data-tab-target="simulator"' in response.text
    assert 'id="tab-simulator"' in response.text
    assert 'id="sim-device-id"' in response.text
    assert 'id="sim-connect"' in response.text
    assert 'id="sim-tap-hint"' in response.text
    assert 'id="sim-duplex-mode"' in response.text
    assert 'id="sim-wake"' in response.text
    assert 'id="sim-screen"' in response.text
    assert 'id="sim-screen-mock"' in response.text
    assert 'class="device-screen"' in response.text
    assert 'class="cat"' in response.text
    assert 'class="princess"' in response.text
    assert 'id="sim-character"' in response.text
    assert 'id="sim-state-cn"' in response.text
    assert 'id="trace-input"' in response.text
    assert 'id="trace-device-select"' in response.text
    assert 'id="trace-preview-web"' in response.text
    assert 'id="trace-dispatch-hardware"' in response.text
    assert 'id="trace-canvas"' in response.text
    assert 'id="speech-barge-in-latency"' in response.text
    assert 'data-tab-target="metrics"' in response.text
    assert 'data-tab-target="gateway"' in response.text
    assert 'id="tab-gateway"' in response.text
    assert 'id="gateway-device-id"' in response.text
    assert 'id="gateway-id"' in response.text
    assert 'id="gateway-connect-ble"' in response.text
    assert 'id="gateway-connect-cloud"' in response.text
    assert 'data-color-test="bars"' in response.text
    assert 'data-color-test="red"' in response.text
    assert 'data-color-test="green"' in response.text
    assert 'data-color-test="blue"' in response.text
    assert 'data-color-test="black"' in response.text
    assert 'data-color-test="white"' in response.text
    assert 'id="gateway-volume-slider"' in response.text
    assert 'id="gateway-send-volume"' in response.text
    assert 'id="gateway-log"' in response.text
    assert 'data-tab-target="provisioning"' in response.text
    assert 'id="tab-provisioning"' in response.text
    assert 'id="prov-form"' in response.text
    assert 'id="prov-ssid"' in response.text
    assert 'id="prov-pop"' in response.text
    assert 'id="prov-start"' in response.text
    assert 'id="prov-log"' in response.text
    assert "provisioning.js" in response.text
    assert 'id="metrics-total-cost"' in response.text
    assert 'id="metrics-remaining-budget"' in response.text
    assert 'id="metrics-success-rate"' in response.text
    assert 'id="metrics-avg-latency"' in response.text
    assert 'id="metrics-p95-latency"' in response.text
    assert 'id="metrics-avg-prompt-tokens"' in response.text
    assert 'id="metrics-asr-hour-price"' in response.text
    assert 'id="metrics-tts-10k-price"' in response.text
    assert 'id="metrics-tts-cache-rate"' in response.text
    assert 'id="metrics-tts-cache-saving"' in response.text
    assert 'id="metrics-models-note"' in response.text
    assert 'id="metrics-voice-coverage"' in response.text
    assert "Doubao-Seed-2.0-mini 刊例价示例" in response.text
    assert "不代表供应商账单" in response.text
    assert "语音统计从本版本新增" in response.text


def test_console_assets_are_served() -> None:
    client = TestClient(app)

    script = client.get("/console/assets/app.js")
    stylesheet = client.get("/console/assets/styles.css")

    assert script.status_code == 200
    assert "new WebSocket" in script.text
    assert "/api/chat/stream" in script.text
    assert "llm_reply_delta" in script.text
    assert "barge_in" in script.text
    assert "text_input" in script.text
    assert "stopPlayback" in script.text
    assert "/api/metrics/token-usage" in script.text
    assert "/api/metrics/interactions" in script.text
    assert "total.successful_calls" in script.text
    assert "total.failed_calls" in script.text
    assert "total.provider_calls" in script.text
    assert "total.provider_success_rate" in script.text
    assert "total.avg_latency_ms" in script.text
    assert "total.p95_latency_ms" in script.text
    assert "total.provider_avg_latency_ms" in script.text
    assert "total.provider_p95_latency_ms" in script.text
    assert "total.avg_prompt_tokens" in script.text
    assert "data.models" in script.text
    assert "interactionMetricsAvailable" in script.text
    assert "llmCostEstimable" in script.text
    assert "多模型不能套用单一价格" in script.text
    assert "未按 0 计算" in script.text
    # mobile card-ification: metrics breakdown cells carry a data-label for narrow screens
    assert "cell.dataset.label = col" in script.text
    assert "/api/settings/speech" in script.text
    assert "/api/devices/" in script.text
    assert "/api/trace/preview" in script.text
    assert "/trace" in script.text
    assert "navigator.bluetooth.requestDevice" in script.text
    assert "BLE_GATEWAY_SERVICE_UUID" in script.text
    assert "6f4d5a9e-7d76-6f69-6365-626f74000002" in script.text
    assert 'transport: "ble_mobile_gateway"' in script.text
    assert "gateway_id" in script.text
    assert "connectGatewayBle" in script.text
    assert "connectGatewayCloud" in script.text
    assert "sendGatewayVolume" in script.text
    assert "dispatchGatewayColorTest" in script.text
    assert "/color-test" in script.text
    assert "dispatchDeviceSay" in script.text
    assert "dispatchDeviceVolume" in script.text
    assert "deviceVolume: 0" in script.text
    assert "/volume" in script.text
    assert "toggleDeviceMonitor" in script.text
    assert "EventSource" in script.text
    assert stylesheet.status_code == 200
    assert ".sidebar" in stylesheet.text
    assert ".workspace" in stylesheet.text


def test_provisioning_asset_is_served() -> None:
    client = TestClient(app)

    script = client.get("/console/assets/provisioning.js")

    assert script.status_code == 200
    assert "021a9004-0382-4aea-bff4-6b3f1c5adfb4" in script.text  # pinned service UUID
    assert "prov-session" in script.text
    assert "prov-config" in script.text


def test_console_memory_management_assets_are_present() -> None:
    client = TestClient(app)

    page = client.get("/console")
    script = client.get("/console/assets/app.js")

    assert page.status_code == 200
    assert 'data-memory-section="profile"' in page.text
    assert 'data-memory-section="turns"' in page.text
    assert 'data-memory-section="summaries"' in page.text
    assert 'data-memory-section="entries"' in page.text
    assert 'data-memory-section="audit"' in page.text
    # Bot 画像 migrated to the 「场景」 tab (default-scene card); no longer a memory section.
    assert 'data-memory-section="bot-profile"' not in page.text
    assert 'data-memory-section="vocabulary"' in page.text
    assert "生词表" in page.text
    assert 'id="delete-memory-item"' in page.text
    assert page.text.index('id="memory-json-editor"') < page.text.index('id="delete-memory-item"')
    assert "saveMemoryProfile" in script.text
    assert "deleteSelectedMemoryItem" in script.text
    assert "confirmMemoryItemDeletion" in script.text
    assert "删除当前选中项" in page.text
    assert "/api/settings/bot-profile" in script.text
    assert "/api/memory/users/" in script.text
    assert "/vocabulary" in script.text
    assert "/vocabulary/history" in script.text
    assert "renderVocabularyMemoryList" in script.text


def test_console_vocab_management_assets_are_present() -> None:
    client = TestClient(app)

    page = client.get("/console")
    script = client.get("/console/assets/app.js")
    stylesheet = client.get("/console/assets/styles.css")

    assert page.status_code == 200
    assert 'data-tab-target="vocab"' in page.text
    assert 'id="tab-vocab"' in page.text
    assert page.text.index('data-tab-target="vocab"') < page.text.index(
        'data-tab-target="overview"'
    )
    assert 'id="vocab-input"' in page.text
    assert 'id="vocab-add"' in page.text
    assert 'id="vocab-clear"' in page.text
    assert 'id="vocab-import"' in page.text
    assert 'id="vocab-group-select"' in page.text
    assert 'id="vocab-group-create"' in page.text
    assert 'id="vocab-status-filter"' in page.text
    assert "一年级（下）生字" in page.text
    assert "已会" in page.text
    assert "不熟" in page.text
    assert 'id="vocab-device-select"' in page.text
    assert 'id="vocab-dispatch-all"' in page.text
    assert 'id="vocab-generate-all"' in page.text
    assert 'id="vocab-grid"' in page.text
    assert "生字管理" in page.text
    assert "无字形" in page.text

    assert script.status_code == 200
    assert "extractHanzi" in script.text
    assert "renderVocabGrid" in script.text
    assert "renderVocabCell" in script.text
    assert "vocabDispatchAll" in script.text
    assert "vocabGenerateOne" in script.text
    assert "vocabGenerateAll" in script.text
    assert "vocabPreviewTts" in script.text
    assert "vocabPreviewTrace" in script.text
    assert "runCellTraceAnimation" in script.text
    assert "VOCAB_DEFAULT_GROUP_NAME" in script.text
    assert "VOCAB_GROUPS_STORAGE_KEY" in script.text
    assert "VOCAB_STATUS_LEARNED" in script.text
    assert "VOCAB_STATUS_UNLEARNED" in script.text
    assert "VOCAB_LEARNED_COLOR" in script.text
    assert 'VOCAB_LEARNED_COLOR = "#f2c94c"' in script.text
    assert "vocabCharacterColor" in script.text
    assert "state.vocab.groups" in script.text
    assert "state.vocab.statuses" in script.text
    assert "/api/vocab/groups" in script.text
    assert "loadVocabGroupsFromServer" in script.text
    assert "persistVocabGroupsToServer" in script.text
    assert "activeStatusFilter" in script.text
    assert "vocabSetCharacterStatus" in script.text
    assert "vocabStatusFilter" in script.text
    assert "vocabConfirmRemove" in script.text
    assert "confirmVocabClear" in script.text
    assert "vocabPreviewAndDispatch" in script.text
    assert "window.confirm(`确认删除生字「${character}」吗？`)" in script.text
    assert "请再次确认：清空后该分组的生字列表会被删除" in script.text
    # high-DPR mobile rendering: vocab cells scale the bitmap by devicePixelRatio
    assert "setupVocabCanvas" in script.text
    assert "devicePixelRatio" in script.text
    assert "drawVocabGlyphStatic(ctx, glyph, w, h, color)" in script.text
    assert "vocabLoadExplanations" in script.text
    assert "/api/vocab/explanation" in script.text
    assert "/api/vocab/explanations" in script.text
    assert "/trace-say" in script.text
    assert "vocabImportFromMemory" in script.text
    assert "populateVocabDeviceSelect" in script.text
    assert "/api/memory/users/" in script.text
    assert "vocabPrint" in script.text
    assert "renderPrintCell" in script.text
    assert "renderVocabA4Pages" in script.text
    assert "showVocabPrintModal" in script.text

    assert 'id="vocab-print"' in page.text
    assert 'id="print-overlay"' in page.text

    assert stylesheet.status_code == 200
    assert ".print-overlay" in stylesheet.text
    assert ".print-pages" in stylesheet.text
    assert ".tab-panel > .panel" in stylesheet.text
    assert ".tab-panel > .gateway-layout" in stylesheet.text
    assert "#tab-conversation > .conversation-layout" in stylesheet.text
    assert "#tab-gateway .gateway-layout" in stylesheet.text
    assert "#tab-vocab > .panel" in stylesheet.text
    assert "#tab-vocab .panel-header" in stylesheet.text
    # Panels fill the screen via container-relative width (100%), deliberately not
    # 100vw/100dvw — viewport units caused iOS overflow chains (see styles.css note).
    assert "而非 100vw/100dvw" in stylesheet.text
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in stylesheet.text
    assert ".vocab-group-bar" in stylesheet.text


def test_console_idiom_management_assets_are_present() -> None:
    client = TestClient(app)

    page = client.get("/console")
    script = client.get("/console/assets/app.js")
    stylesheet = client.get("/console/assets/styles.css")

    assert page.status_code == 200
    assert 'data-tab-target="idiom"' in page.text
    assert 'id="tab-idiom"' in page.text
    assert "成语学习" in page.text
    # Core controls
    assert 'id="idiom-input"' in page.text
    assert 'id="idiom-add"' in page.text
    assert 'id="idiom-clear"' in page.text
    assert 'id="idiom-print"' in page.text
    assert 'id="idiom-group-select"' in page.text
    assert 'id="idiom-group-create"' in page.text
    assert 'id="idiom-status-filter"' in page.text
    assert 'id="idiom-device-select"' in page.text
    assert 'id="idiom-read-kind"' in page.text
    assert 'id="idiom-dispatch-all"' in page.text
    assert 'id="idiom-generate-all"' in page.text
    assert 'id="idiom-explanation-template"' in page.text
    assert 'id="idiom-story-template"' in page.text
    assert 'id="idiom-grid"' in page.text
    assert "成语 · 第一批" in page.text

    assert script.status_code == 200
    assert "extractIdioms" in script.text
    assert "renderIdiomGrid" in script.text
    assert "buildIdiomCard" in script.text
    assert "idiomGenerateOne" in script.text
    assert "idiomGenerateAll" in script.text
    assert "idiomPreviewTts" in script.text
    assert "idiomDispatchOne" in script.text
    assert "idiomDispatchAll" in script.text
    assert "renderIdiomBigChar" in script.text
    assert "idiomEnsureAllGlyphs" in script.text
    assert "idiomLoadContents" in script.text
    assert "idiomPrint" in script.text
    assert "renderIdiomA4Pages" in script.text
    assert "renderIdiomPrintCard" in script.text
    assert "populateIdiomDeviceSelect" in script.text
    assert "/api/idiom/content" in script.text
    assert "/api/idiom/contents" in script.text
    assert "/api/idiom/groups" in script.text
    assert "/idiom-say" in script.text
    assert "IDIOM_DEFAULT_GROUP_NAME" in script.text

    assert stylesheet.status_code == 200
    # One page row = 2 idioms; each card lays its chars in a single horizontal
    # row on the web (only the hardware screen uses a 2×2 layout).
    assert ".idiom-grid" in stylesheet.text
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in stylesheet.text
    assert ".idiom-card-chars" in stylesheet.text
    assert "flex-wrap: nowrap" in stylesheet.text


def test_console_speech_settings_controls_are_present() -> None:
    client = TestClient(app)

    page = client.get("/console")
    script = client.get("/console/assets/app.js")

    assert page.status_code == 200
    assert 'id="speech-edit-toggle"' in page.text
    assert 'id="speech-visibility-toggle"' in page.text
    assert 'id="speech-save"' in page.text
    assert 'id="speech-cancel"' in page.text
    assert 'id="speech-asr-ws-url-input"' in page.text
    assert 'id="speech-asr-resource-id-input"' in page.text
    assert 'id="speech-asr-token-input"' in page.text
    assert "WebSocket 双向流式实时识别并增量上屏" in page.text
    assert "配置保存后下一轮输入自动生效，无需重连。" in page.text
    assert 'id="speech-tts-ws-url-input"' in page.text
    assert 'id="speech-tts-resource-id-input"' in page.text
    assert 'id="speech-tts-token-input"' in page.text
    assert 'id="speech-tts-voice-input"' in page.text


def test_console_aurora_visual_system_and_accessible_navigation_are_present() -> None:
    client = TestClient(app)

    page = client.get("/console")
    script = client.get("/console/assets/app.js")
    stylesheet = client.get("/console/assets/styles.css")

    assert "20260712-aurora" in page.text
    assert 'class="brand-mark"' in page.text
    assert 'class="brand-lockup"' in page.text
    assert 'data-icon="◉" data-tab-target="conversation"' in page.text
    assert 'button.setAttribute("aria-selected", String(active))' in script.text
    for token in ["--accent-2", "--surface-glass", "--shadow-glow"]:
        assert token in stylesheet.text
    for selector in [
        ".brand-mark",
        ".tab-button::after",
        ".dot-pill::before",
        "button:focus-visible",
        "@media (prefers-reduced-motion: reduce)",
    ]:
        assert selector in stylesheet.text
    assert 'id="speech-tts-encoding-input"' in page.text
    assert 'id="speech-tts-sample-rate"' in page.text
    assert 'id="speech-tts-sample-rate-input"' in page.text
    assert script.status_code == 200
    assert "renderSpeechSettings" in script.text
    assert "ensureSpeechSocketReady" in script.text
    assert "waitForSocketOpen" in script.text
    assert "waitForAuthOk" in script.text
    assert "Use PCM + client VAD for web debugging" in script.text
    assert "appendBubble" in script.text
    assert "replayAssistantTts" in script.text
    assert "createTtsReplayButton" in script.text
    assert "data-tts-replay-button" in script.text
    assert "bubble-actions" in script.text
    assert 'if (ttsUrl) {' in script.text
    assert 'setBubbleReplayState(bubble, "ready")' in script.text
    assert 'bubble.addEventListener("click"' not in script.text
    assert "data-tts-url" in script.text
    assert "Bot 回复下方出现重听按钮时可重放语音" in page.text
    assert "loadConversationHistory({ silent: true })" in script.text
    assert 'showToast(`重听语音失败：${error.message}`, "error")' in script.text
    assert "turn_id" in script.text
    assert "toggleMic" in script.text
    assert "loadConversationHistory" in script.text
    assert "asr_api_key" in script.text
    assert "asr_resource_id" in script.text
    assert "asr_ws_url" in script.text
    assert "tts_api_key" in script.text
    assert "ttsSampleRateHz" in script.text
    assert 'placeholder="16000"' in page.text
    assert 'Number(qs("#speech-tts-sample-rate-input").value || "16000") || 16000' in script.text
    assert "asrSampleRateHz" in script.text
    assert 'id="speech-asr-ws-url-input"' in page.text
    assert "下一轮文本或语音输入会自动使用最新配置，无需重连。" in script.text
    assert "流式语音识别 2.0" in page.text
    assert "语音合成 1.0" in page.text
    assert "startButton.disabled = rtc.running" in script.text
    assert "stopButton.disabled = !rtc.running" in script.text
    assert "/api/settings/speech" in script.text


def test_console_barge_in_control_is_present() -> None:
    client = TestClient(app)

    page = client.get("/console")

    assert page.status_code == 200
    assert 'id="send-barge-in"' in page.text


def test_console_text_input_control_is_present() -> None:
    client = TestClient(app)

    page = client.get("/console")

    assert page.status_code == 200
    assert 'id="send-text-input"' in page.text
    assert 'id="ws-text-input"' in page.text


def test_console_scenes_tab_assets_are_present() -> None:
    client = TestClient(app)

    page = client.get("/console")
    script = client.get("/console/assets/app.js")
    stylesheet = client.get("/console/assets/styles.css")

    assert page.status_code == 200
    assert 'data-tab-target="scenes"' in page.text
    assert 'id="tab-scenes"' in page.text
    # Default-scene card consolidates the base system prompt + Bot 画像 (migrated).
    assert 'id="scene-base-system-prompt"' in page.text
    assert 'id="scene-base-prompt-save"' in page.text
    assert 'id="scene-botprofile-enabled"' in page.text
    assert 'id="scene-botprofile-name"' in page.text
    assert 'id="scene-botprofile-persona"' in page.text
    assert 'id="scene-botprofile-save"' in page.text
    # Intent-scene cards are rendered into this container.
    assert 'id="scene-list"' in page.text
    # The base system prompt was moved out of the 模型配置 tab.
    assert 'id="llm-system-prompt-input"' not in page.text

    assert script.status_code == 200
    assert "loadScenesTab" in script.text
    assert "saveSceneBasePrompt" in script.text
    assert "saveSceneBotProfile" in script.text
    assert "buildSceneCard" in script.text
    assert "previewScenePrompt" in script.text
    assert "/api/scenes" in script.text
    assert "/prompt-tts/rebuild" in script.text
    # base system prompt save must preserve other LLM fields (all-or-nothing PUT).
    assert "/api/settings/llm?reveal=true" in script.text

    assert stylesheet.status_code == 200
    assert ".scene-card" in stylesheet.text
    assert ".scene-list" in stylesheet.text


def test_console_mobile_layout_fills_width_with_container_relative_units() -> None:
    client = TestClient(app)

    page = client.get("/console")
    stylesheet = client.get("/console/assets/styles.css")

    assert page.status_code == 200
    assert "styles.css?v=" in page.text
    assert stylesheet.status_code == 200
    assert "@media (max-width: 1100px)" in stylesheet.text
    # Below 1100px the shell stacks and fills the screen via container-relative
    # width (100%), deliberately not 100vw/100dvw (those caused iOS overflow chains).
    assert ".app-shell {\n    display: block;\n    width: 100%;\n    max-width: 100%;" in stylesheet.text
    assert "overflow-x: clip" in stylesheet.text
    assert "而非 100vw/100dvw" in stylesheet.text
