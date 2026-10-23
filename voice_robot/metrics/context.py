from contextvars import ContextVar, Token


_scenario: ContextVar[str] = ContextVar("voice_robot_scenario", default="agent")


def get_current_scenario() -> str:
    value = _scenario.get()
    return value or "agent"


def set_current_scenario(value: str) -> Token:
    return _scenario.set(value or "agent")


def reset_current_scenario(token: Token) -> None:
    _scenario.reset(token)

