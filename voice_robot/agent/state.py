from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentResult:
    response_text: str
    safety_passed: bool
    reply_source: str = "local-fallback"
    turn_id: str = ""
    fallback_reason: str | None = None
    retrieved_memories: list[dict] = field(default_factory=list)
    new_memories: list[dict] = field(default_factory=list)
    prompt_debug: dict | None = None


@dataclass
class AgentState:
    user_text: str
    user_id: str
    safety_passed: bool = True
    reply_source: str = "local-fallback"
    fallback_reason: str | None = None
    retrieved_memories: list[dict] = field(default_factory=list)
    new_memories: list[dict] = field(default_factory=list)
    response_text: str = ""
    turn_id: str = ""
    prompt_debug: dict | None = None
