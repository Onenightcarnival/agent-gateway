"""ask_user 工具：把引擎的反问交给网关的交互队列。

参数 schema 显式声明，不含 anyOf / null：部分 OpenAI 兼容代理遇到这类 schema 会丢弃整份工具列表。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..engines.base import InteractionPort, Question, QuestionOption

ASK_USER_DESCRIPTION = "向用户提问并等待回答。仅在缺少关键信息且无法合理假设时使用。"


class AskUserArgs(BaseModel):
    question: str = Field(description="要问用户的问题")
    options: list[str] = Field(default_factory=list, description="可选项；为空表示自由回答")


def make_ask_user(
    session_id: str, interaction: InteractionPort
) -> Callable[[str, list[str] | None], Awaitable[str]]:
    async def ask_user(question: str, options: list[str] | None = None) -> str:
        """向用户提问并等待回答。仅在缺少关键信息且无法合理假设时使用。

        Args:
            question: 要问用户的问题。
            options: 可选项列表；提供时用户从中选择。
        """
        answers = await interaction.ask_question(
            session_id,
            [Question(question=question, options=[QuestionOption(label=o) for o in options or []])],
        )
        chosen = answers[0] if answers else []
        return ", ".join(chosen) if chosen else "(no answer)"

    return ask_user


def make_ask_user_langchain_tool(session_id: str, interaction: InteractionPort) -> StructuredTool:
    return StructuredTool.from_function(
        coroutine=make_ask_user(session_id, interaction),
        name="ask_user",
        description=ASK_USER_DESCRIPTION,
        args_schema=AskUserArgs,
    )
