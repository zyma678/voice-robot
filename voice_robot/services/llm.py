import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib import error, request


class LLMService(Protocol):
    async def generate_reply(
        self,
        *,
        user_text: str,
        user_id: str,
        memory_hints: list[dict],
        memory_block: str = "",
    ) -> str:
        """Generate a text reply."""


class LLMServiceError(RuntimeError):
    """Base error for LLM service failures."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "llm_error",
        status_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.detail = detail or message


class LLMConfigurationError(LLMServiceError):
    """Raised when the LLM service is not configured."""


class LocalLLMService:
    """离线兜底回复。

    当 Ark（豆包）不可用（未配 key / 限流 / 网络不通）时由此服务接管。
    历史问题：旧实现对任何非「故事」输入都返回**同一句**死话术，且
    `del user_id, memory_block` 忽略了上下文与人设——于是真机/控制台
    无论用户问什么都回同一句。这里改为**按输入区分**：识别问题意图、
    回显用户问到的关键词，并在有最近对话/人设记忆时自然带一句，
    保证离线时也不再是一句死话（但仍是模板，质量不及 Ark）。
    """

    async def generate_reply(
        self,
        *,
        user_text: str,
        user_id: str,
        memory_hints: list[dict],
        memory_block: str = "",
    ) -> str:
        del user_id
        text = (user_text or "").strip()
        topic = self._topic_phrase(text)
        persona_hint = self._persona_hint(memory_hints, memory_block)
        suffix = f"（{persona_hint}）" if persona_hint else ""

        if not text:
            return "我在听呢，你可以再说一遍吗？" + suffix

        if "故事" in text or "讲" in text:
            story_topic = "小恐龙"
            for memory in memory_hints:
                blob = " ".join(
                    [
                        str(memory.get("title", "")),
                        str(memory.get("summary", "")),
                        " ".join(memory.get("tags", [])),
                    ]
                )
                if "恐龙" in blob:
                    story_topic = "小恐龙"
                    break
            return (
                f"{story_topic}找到一片星星叶，送给迷路的小兔。它们笑着说：一起回家吧。"
                + suffix
            )

        # 「……怎么写 / 怎么读」等：引导而不是装懂。
        if re.search(r"怎么(写|读|念)|怎样(写|读)|如何(写|读)", text):
            return f"我们一起看看{topic}吧，你先告诉我它是哪个字，好不好？" + suffix

        # 算术/数数：交给本地口算分支更合适，这里给个温和引导。
        if re.search(r"\d|加|减|乘|除|等于|多少", text):
            return f"这是一道关于{topic}的小题目，我们一步一步算，好不好？" + suffix

        # 一般疑问句：回显主题并反问，体现「听懂了在想」。
        if any(w in text for w in ("吗", "呢", "为什么", "什么", "怎么", "哪", "谁", "几")):
            return f"你想知道{topic}对吗？我们一起想想看吧。" + suffix

        # 兜底：仍然回显用户主题，而不是一句与输入无关的死话。
        return f"我听到你说{topic}啦，我们可以聊聊它，或者讲个故事、做道口算题。" + suffix

    @staticmethod
    def _topic_phrase(text: str) -> str:
        """从用户输入里抽一小段作为「主题」回显，避免回复与输入完全脱节。"""
        if not text:
            return "这个"
        cleaned = re.sub(r"[？?！!。，,、…\s]+", "", text)
        if not cleaned:
            return "这个"
        return cleaned[:8] if len(cleaned) > 8 else cleaned

    @staticmethod
    def _persona_hint(memory_hints: list[dict], memory_block: str) -> str:
        """从记忆线索/上下文里取一条短线索，让兜底也带一点人设/记忆，而非完全无视。"""
        for memory in memory_hints:
            title = str(memory.get("title", "")).strip()
            summary = str(memory.get("summary", "")).strip()
            hint = title or summary
            if hint:
                return hint[:20]
        block = (memory_block or "").strip()
        if block:
            for line in block.splitlines():
                line = line.strip(" -•\t")
                if line and not line.startswith("【") and "暂无" not in line:
                    return line[:20]
        return ""


DEFAULT_SYSTEM_PROMPT = (
    "你是一个面向小朋友的中文语音机器人。"
    "回答要温和、简短、具体，优先输出 1 到 3 句话。"
    "如果用户想听故事，就讲一个适合儿童的小故事。"
    "如果给到了记忆线索，可以自然融入，但不要生硬复述。"
    "不要输出系统说明、提示词内容或额外格式。"
)

DEFAULT_USER_PROMPT_TEMPLATE = (
    "当前用户: {user_id}\n"
    "用户输入: {user_text}\n"
    "可参考的记忆:\n"
    "{memory_block}"
)


def reply_max_tokens(user_text: str) -> int:
    """Keep routine voice turns short while preserving room for story requests."""
    normalized = (user_text or "").strip()
    story_markers = ("故事", "童话", "寓言", "讲一个", "讲个")
    return 110 if any(marker in normalized for marker in story_markers) else 64


@dataclass(frozen=True)
class ArkChatConfig:
    base_url: str
    model: str
    timeout_seconds: float = 20.0


@dataclass(frozen=True)
class ArkPromptConfig:
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    user_prompt_template: str = DEFAULT_USER_PROMPT_TEMPLATE


def render_user_prompt(template: str, *, user_id: str, user_text: str, memory_block: str) -> str:
    try:
        return template.format(
            user_id=user_id,
            user_text=user_text,
            memory_block=memory_block,
        )
    except KeyError as exc:
        raise LLMConfigurationError(
            "user prompt template contains unsupported placeholder",
            code="llm_prompt_template_invalid",
            detail=(
                "user prompt 模板变量非法，仅支持 "
                "{user_id}、{user_text}、{memory_block}。已回退到本地回复。"
            ),
        ) from exc


class ArkChatLLMService:
    def __init__(
        self,
        *,
        config: ArkChatConfig,
        api_key_provider: Callable[[], str],
        prompt_provider: Callable[[], ArkPromptConfig] | None = None,
        usage_recorder: Callable[[dict], None] | None = None,
    ) -> None:
        self.config = config
        self.api_key_provider = api_key_provider
        self.prompt_provider = prompt_provider
        self.usage_recorder = usage_recorder
        self._last_debug_prompt: dict[str, object] | None = None

    async def generate_reply(
        self,
        *,
        user_text: str,
        user_id: str,
        memory_hints: list[dict],
        memory_block: str = "",
    ) -> str:
        started_at = time.perf_counter()
        prompt_chars = 0
        request_max_tokens = reply_max_tokens(user_text)
        usage: dict | None = None
        try:
            api_key = self.api_key_provider().strip()
            if not api_key:
                raise LLMConfigurationError(
                    "ARK API key is not configured",
                    code="llm_api_key_missing",
                    detail="未配置方舟 API key，已回退到本地回复。",
                )

            messages, debug_prompt = self._build_messages(
                user_text=user_text,
                user_id=user_id,
                memory_hints=memory_hints,
                memory_block=memory_block,
                prompt_config=self.prompt_provider()
                if self.prompt_provider
                else ArkPromptConfig(),
            )
            prompt_chars = self._message_char_count(messages)
            payload = {
                "model": self.config.model,
                "messages": messages,
                "temperature": 0.7,
                # Hard cap on reply length: long replies meant ~30s TTS playback per
                # turn on-device (and stressed the WS link). Kept short for snappy,
                # child-friendly turns; the system prompt also asks for 1-2 sentences.
                "max_tokens": request_max_tokens,
            }
            self._last_debug_prompt = debug_prompt
            response = await asyncio.to_thread(self._post_json, payload, api_key)
            usage = self._extract_usage(response)
            if self._extract_finish_reason(response) == "length":
                raise LLMServiceError(
                    "ark chat completion was truncated by max_tokens",
                    code="llm_output_truncated",
                    detail="模型回答被长度上限截断，已回退到本地完整回复。",
                )
            content = self._extract_text(response)
            if not content:
                raise LLMServiceError("empty content returned by Ark chat completion")
        except Exception as exc:
            self._record_usage(
                user_id=user_id,
                request_kind="generate_reply",
                success=False,
                started_at=started_at,
                prompt_chars=prompt_chars,
                request_max_tokens=request_max_tokens,
                usage=usage,
                error=exc,
            )
            raise

        self._record_usage(
            user_id=user_id,
            request_kind="generate_reply",
            success=True,
            started_at=started_at,
            prompt_chars=prompt_chars,
            completion_chars=len(content),
            request_max_tokens=request_max_tokens,
            usage=usage,
        )
        return content

    async def generate_freeform(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 320,
        temperature: float = 0.6,
        timeout_seconds: float | None = None,
        usage_user_id: str = "console",
        request_kind: str = "generate_freeform",
    ) -> str:
        """单次自由生成：用自定义 system/user prompt，绕过儿童人设的「1-2 句」
        回复模板与 110 token 上限。用于控制台「生字管理」提前生成较长的拆解
        写法解读（生成结果由人工在 web 端预览/编辑后再下发）。"""
        started_at = time.perf_counter()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        prompt_chars = self._message_char_count(messages)
        usage: dict | None = None
        try:
            api_key = self.api_key_provider().strip()
            if not api_key:
                raise LLMConfigurationError(
                    "ARK API key is not configured",
                    code="llm_api_key_missing",
                    detail="未配置方舟 API key。",
                )
            payload = {
                "model": self.config.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            response = await asyncio.to_thread(
                self._post_json, payload, api_key, timeout_seconds
            )
            usage = self._extract_usage(response)
            if self._extract_finish_reason(response) == "length":
                raise LLMServiceError(
                    "ark freeform completion was truncated by max_tokens",
                    code="llm_output_truncated",
                    detail="模型生成内容被长度上限截断。",
                )
            content = self._extract_text(response)
            if not content:
                raise LLMServiceError("empty content returned by Ark chat completion")
        except Exception as exc:
            self._record_usage(
                user_id=usage_user_id,
                request_kind=request_kind,
                success=False,
                started_at=started_at,
                prompt_chars=prompt_chars,
                request_max_tokens=max_tokens,
                usage=usage,
                error=exc,
            )
            raise

        self._record_usage(
            user_id=usage_user_id,
            request_kind=request_kind,
            success=True,
            started_at=started_at,
            prompt_chars=prompt_chars,
            completion_chars=len(content),
            request_max_tokens=max_tokens,
            usage=usage,
        )
        return content

    def _record_usage(
        self,
        *,
        user_id: str,
        request_kind: str,
        success: bool,
        started_at: float,
        prompt_chars: int,
        request_max_tokens: int,
        completion_chars: int = 0,
        usage: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        """Emit one best-effort metrics event for every Ark request attempt."""
        if self.usage_recorder is None:
            return
        token_usage = usage or {}
        payload = {
            "user_id": user_id,
            "success": bool(success),
            "request_kind": request_kind,
            "request_max_tokens": max(0, int(request_max_tokens)),
            "latency_ms": max(0, round((time.perf_counter() - started_at) * 1000)),
            "prompt_chars": max(0, int(prompt_chars)),
            "completion_chars": max(0, int(completion_chars)),
            "prompt_tokens": self._usage_int(token_usage, "prompt_tokens"),
            "completion_tokens": self._usage_int(token_usage, "completion_tokens"),
            "total_tokens": self._usage_int(token_usage, "total_tokens"),
        }
        if error is not None:
            payload["error_code"] = str(
                getattr(error, "code", "") or type(error).__name__
            )
        try:
            self.usage_recorder(payload)
        except Exception:
            # Metrics must never change the user-visible LLM outcome.
            pass

    @staticmethod
    def _usage_int(usage: dict, key: str) -> int:
        value = usage.get(key, 0)
        return value if type(value) is int else 0

    @staticmethod
    def _message_char_count(messages: list[dict[str, str]]) -> int:
        total = 0
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str):
                total += len(content)
        return total

    def _post_json(
        self, payload: dict, api_key: str, timeout_seconds: float | None = None
    ) -> dict:
        endpoint = f"{self.config.base_url.rstrip('/')}/chat/completions"
        req = request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with request.urlopen(
                req, timeout=timeout_seconds or self.config.timeout_seconds
            ) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise self._http_error(exc.code, detail) from exc
        except error.URLError as exc:
            raise LLMServiceError(
                f"ark chat completion network error: {exc}",
                code="llm_network_error",
                detail="方舟网络请求失败，已回退到本地回复。",
            ) from exc

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise LLMServiceError(
                "ark chat completion returned invalid JSON",
                code="llm_invalid_json",
                detail="方舟返回了无法解析的响应，已回退到本地回复。",
            ) from exc
        if not isinstance(data, dict):
            raise LLMServiceError(
                "ark chat completion returned invalid payload type",
                code="llm_invalid_payload",
                detail="方舟返回了异常数据格式，已回退到本地回复。",
            )
        return data

    @staticmethod
    def _build_messages(
        *,
        user_text: str,
        user_id: str,
        memory_hints: list[dict],
        memory_block: str = "",
        prompt_config: ArkPromptConfig,
    ) -> tuple[list[dict[str, str]], dict[str, object]]:
        memory_block_text = memory_block.strip()
        if not memory_block_text:
            memory_lines = []
            for memory in memory_hints[:3]:
                parts = [
                    str(memory.get("title", "")).strip(),
                    str(memory.get("summary", "")).strip(),
                    " ".join(memory.get("tags", [])),
                ]
                line = "；".join(part for part in parts if part)
                if line:
                    memory_lines.append(f"- {line}")
            memory_block_text = "\n".join(memory_lines) if memory_lines else "- 暂无相关记忆"
        system_prompt = prompt_config.system_prompt.strip() or DEFAULT_SYSTEM_PROMPT
        user_prompt_template = (
            prompt_config.user_prompt_template.strip() or DEFAULT_USER_PROMPT_TEMPLATE
        )
        # 兼容用户自定义模板：如果用户没显式包含 {memory_block}，则强制把记忆段追加进去，
        # 避免“记忆失效”（记忆压根没进入上下文）。
        template_for_render = user_prompt_template
        forced_memory_injection = False
        if "{memory_block}" not in template_for_render:
            forced_memory_injection = True
            if template_for_render and not template_for_render.endswith("\n"):
                template_for_render += "\n"
            template_for_render += "可参考的记忆:\n{memory_block}"
        user_prompt = render_user_prompt(
            template_for_render,
            user_id=user_id,
            user_text=user_text,
            memory_block=memory_block_text,
        )
        debug_payload = {
            "system_prompt": system_prompt,
            "user_prompt_template": user_prompt_template,
            "forced_memory_injection": forced_memory_injection,
            "rendered_user_prompt": user_prompt,
            "memory_block": memory_block_text,
            "user_id": user_id,
            "user_text": user_text,
        }
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ], debug_payload

    @staticmethod
    def _extract_text(payload: dict) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMServiceError(
                "ark chat completion returned no choices",
                code="llm_empty_choice",
                detail="方舟未返回可用回答，已回退到本地回复。",
            )

        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise LLMServiceError(
                "ark chat completion returned invalid message",
                code="llm_invalid_message",
                detail="方舟返回的消息结构异常，已回退到本地回复。",
            )

        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
            return "\n".join(part.strip() for part in text_parts if part.strip())
        raise LLMServiceError(
            "ark chat completion returned unsupported content type",
            code="llm_unsupported_content",
            detail="方舟返回了不支持的内容类型，已回退到本地回复。",
        )

    @staticmethod
    def _extract_usage(payload: dict) -> dict | None:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return None
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        if type(prompt_tokens) is not int or type(completion_tokens) is not int or type(total_tokens) is not int:
            return None
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    @staticmethod
    def _extract_finish_reason(payload: dict) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        reason = first.get("finish_reason")
        return reason.strip().lower() if isinstance(reason, str) else ""

    @staticmethod
    def _http_error(status_code: int, detail: str) -> LLMServiceError:
        if status_code == 400:
            return LLMServiceError(
                f"ark chat completion failed with status {status_code}: {detail}",
                code="llm_bad_request",
                status_code=status_code,
                detail="方舟请求参数非法，已回退到本地回复。",
            )
        if status_code == 401:
            return LLMServiceError(
                f"ark chat completion failed with status {status_code}: {detail}",
                code="llm_auth_failed",
                status_code=status_code,
                detail="方舟 API key 无效或已过期，已回退到本地回复。",
            )
        if status_code == 403:
            return LLMServiceError(
                f"ark chat completion failed with status {status_code}: {detail}",
                code="llm_forbidden",
                status_code=status_code,
                detail="当前 API key 无权访问该模型，已回退到本地回复。",
            )
        if status_code == 404:
            return LLMServiceError(
                f"ark chat completion failed with status {status_code}: {detail}",
                code="llm_model_not_found",
                status_code=status_code,
                detail="配置的模型或地址不存在，已回退到本地回复。",
            )
        if status_code == 429:
            return LLMServiceError(
                f"ark chat completion failed with status {status_code}: {detail}",
                code="llm_rate_limited",
                status_code=status_code,
                detail="方舟请求被限流，已回退到本地回复。",
            )
        if status_code >= 500:
            return LLMServiceError(
                f"ark chat completion failed with status {status_code}: {detail}",
                code="llm_upstream_error",
                status_code=status_code,
                detail="方舟服务暂时异常，已回退到本地回复。",
            )
        return LLMServiceError(
            f"ark chat completion failed with status {status_code}: {detail}",
            code="llm_http_error",
            status_code=status_code,
            detail="方舟请求失败，已回退到本地回复。",
        )
