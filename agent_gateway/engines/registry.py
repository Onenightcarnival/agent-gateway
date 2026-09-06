"""引擎名 → 工厂。"""

from __future__ import annotations

from collections.abc import Callable

from ..config import Settings
from .base import AgentEngine

EngineFactory = Callable[[Settings], AgentEngine]


def _deepagents(settings: Settings) -> AgentEngine:
    from .deepagents_engine import DeepAgentsEngine

    return DeepAgentsEngine(settings)


def _openai_agents(settings: Settings) -> AgentEngine:
    from .openai_agents_engine import OpenAIAgentsEngine

    return OpenAIAgentsEngine(settings)


ENGINES: dict[str, EngineFactory] = {
    "deepagents": _deepagents,
    "openai-agents": _openai_agents,
}


def create_engine(settings: Settings) -> AgentEngine:
    try:
        factory = ENGINES[settings.engine]
    except KeyError:
        raise ValueError(
            f"unknown engine {settings.engine!r}; available: {', '.join(ENGINES)}"
        ) from None
    return factory(settings)
