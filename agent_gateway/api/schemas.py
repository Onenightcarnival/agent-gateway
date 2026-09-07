from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CreateSessionBody(Lenient):
    title: str | None = None
    directory: str | None = None


class PromptPart(Lenient):
    type: str = "text"
    text: str | None = None


class PromptModel(Lenient):
    providerID: str | None = None
    modelID: str | None = None


class PromptBody(Lenient):
    parts: list[PromptPart] = Field(default_factory=list)
    model: PromptModel | None = None
    agent: str | None = None

    def text(self) -> str:
        return "\n".join(p.text for p in self.parts if p.type == "text" and p.text)


class QuestionReplyBody(Lenient):
    answers: list[list[str]] | list[str] | Any = Field(default_factory=list)

    def normalized(self) -> list[list[str]]:
        answers = self.answers
        if isinstance(answers, str):
            return [[answers]]
        return [[a] if isinstance(a, str) else list(a) for a in answers]


class PermissionReplyBody(Lenient):
    reply: Literal["once", "always", "reject"] = "once"
    message: str | None = None


class MakeDirBody(Lenient):
    parent: str
    name: str
