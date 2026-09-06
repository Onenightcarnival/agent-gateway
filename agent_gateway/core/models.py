"""会话与消息模型（docs/03-message-model.md）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .ids import new_id, now_iso

SessionStatus = Literal["idle", "busy"]


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    content: str = ""


class ToolState(BaseModel):
    status: Literal["running", "completed", "error"]
    title: str
    output: str | None = None


class ToolPart(BaseModel):
    type: Literal["tool"] = "tool"
    tool: str
    call_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    state: ToolState


class StepFinishPart(BaseModel):
    type: Literal["step-finish"] = "step-finish"


Part = TextPart | ToolPart | StepFinishPart


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AssistantInfo(BaseModel):
    role: Literal["assistant"] = "assistant"
    finish: Literal["stop", "tool-calls"] | None = None
    aborted: bool | None = None
    aborted_reason: str | None = None
    error: str | None = None


class UserMessage(BaseModel):
    id: str = Field(default_factory=lambda: new_id("msg"))
    role: Literal["user"] = "user"
    content: str
    created_at: str = Field(default_factory=now_iso)


class AssistantMessage(BaseModel):
    id: str = Field(default_factory=lambda: new_id("msg"))
    role: Literal["assistant"] = "assistant"
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    info: AssistantInfo = Field(default_factory=AssistantInfo)
    parts: list[Part] = Field(default_factory=list)

    def refresh_content(self) -> None:
        self.content = "".join(p.content for p in self.parts if isinstance(p, TextPart))

    @property
    def is_final(self) -> bool:
        return self.info.finish == "stop" and any(isinstance(p, StepFinishPart) for p in self.parts)


class ToolMessage(BaseModel):
    id: str = Field(default_factory=lambda: new_id("msg"))
    role: Literal["tool"] = "tool"
    tool_call_id: str
    tool_name: str
    content: str
    created_at: str = Field(default_factory=now_iso)


Message = UserMessage | AssistantMessage | ToolMessage


class Session(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ses"))
    title: str
    directory: str
    created_at: str = Field(default_factory=now_iso)
    status: SessionStatus = "idle"
    messages: list[Message] = Field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "directory": self.directory,
            "created_at": self.created_at,
            "status": self.status,
        }


def dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(exclude_none=True)
