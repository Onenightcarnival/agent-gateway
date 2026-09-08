"""HTTP 路由（docs/04-http-api.md）。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from ..core.interaction import RequestNotFound
from ..core.models import dump
from ..core.store import SessionNotFound
from ..engines.base import ModelRef
from ..gateway import Gateway
from ..tools import fs_browse
from ..tools.shell import make_shell_runner
from ..tools.workspace import Workspace
from .errors import BadGateway, NotFound, ServiceUnavailable, ValidationError
from .schemas import (
    CreateSessionBody,
    MakeDirBody,
    PermissionReplyBody,
    PromptBody,
    QuestionReplyBody,
)

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _gw(request: Request) -> Gateway:
    return request.app.state.gateway


def _require_engine(gw: Gateway) -> None:
    reason = gw.unavailable_reason
    if reason is not None:
        raise ServiceUnavailable(reason)


def _session(gw: Gateway, session_id: str):
    try:
        return gw.store.get(session_id)
    except SessionNotFound:
        raise NotFound("Session not found") from None


# ---- 调试页面 ----


@router.get("/", include_in_schema=False)
@router.get("/ui", include_in_schema=False)
async def debug_ui() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html; charset=utf-8")


# ---- 目录浏览 ----


@router.get("/fs/dirs")
async def fs_dirs(path: str | None = None) -> dict:
    try:
        return fs_browse.list_dirs(path)
    except fs_browse.DirectoryNotFound as exc:
        raise NotFound(f"Directory not found: {exc}") from None


@router.post("/fs/dirs")
async def fs_mkdir(body: MakeDirBody) -> dict:
    try:
        return {"path": fs_browse.make_dir(body.parent, body.name)}
    except fs_browse.DirectoryNotFound as exc:
        raise NotFound(f"Directory not found: {exc}") from None
    except fs_browse.DirectoryExists as exc:
        raise ValidationError(f"Directory already exists: {exc}") from None
    except ValueError as exc:
        raise ValidationError(str(exc)) from None


# ---- 健康 ----


@router.get("/health")
async def health(request: Request) -> dict:
    gw = _gw(request)
    _require_engine(gw)
    info = gw.engine_info
    return {
        "engine": info.name if info else gw.settings.engine,
        "model": info.model if info else gw.settings.model_name,
        "tools": info.tool_names if info else [],
        "skills": info.skill_names if info else [],
        "sessions": len(gw.store),
        "permission_mode": gw.settings.permission_mode,
        "shell_sandbox": make_shell_runner(
            Workspace("."), enabled=gw.settings.shell_sandbox
        ).sandboxed,
    }


# ---- 会话 ----


@router.post("/session")
async def create_session(body: CreateSessionBody, request: Request) -> dict:
    if not body.directory:
        raise ValidationError("directory is required")
    gw = _gw(request)
    _require_engine(gw)
    return await gw.create_session(body.directory, body.title)


@router.get("/session")
async def list_sessions(request: Request) -> list[dict]:
    return [{**s.summary(), "message_count": len(s.messages)} for s in _gw(request).store.all()]


@router.get("/session/status")
async def session_status(request: Request) -> dict:
    return _gw(request).store.statuses()


@router.get("/session/{session_id}")
async def get_session(session_id: str, request: Request) -> dict:
    session = _session(_gw(request), session_id)
    return {**session.summary(), "message_count": len(session.messages)}


@router.delete("/session/{session_id}")
async def delete_session(session_id: str, request: Request) -> dict:
    gw = _gw(request)
    _session(gw, session_id)
    await gw.delete_session(session_id)
    return {"ok": True}


@router.post("/session/{session_id}/prompt_async", status_code=204)
async def prompt_async(session_id: str, body: PromptBody, request: Request) -> Response:
    gw = _gw(request)
    session = _session(gw, session_id)
    text = body.text()
    if not text:
        raise ValidationError("parts must contain at least one text part")
    _require_engine(gw)
    model = ModelRef(
        provider_id=body.model.providerID if body.model else None,
        model_id=body.model.modelID if body.model else None,
    )
    if not (gw.settings.model_name or model.model_id):
        raise ValidationError("model.modelID is required")
    outcome = await gw.run_turn(session, text, model)
    if outcome.kind == "error":
        raise BadGateway(outcome.message)
    return Response(status_code=204)


@router.get("/session/{session_id}/message")
async def list_messages(session_id: str, request: Request) -> list[dict]:
    session = _session(_gw(request), session_id)
    return [dump(m) for m in session.messages]


@router.post("/session/{session_id}/abort")
@router.post("/session/{session_id}/stop")
async def abort_session(session_id: str, request: Request) -> dict:
    gw = _gw(request)
    _session(gw, session_id)
    await gw.abort_turn(session_id)
    return {"ok": True}


# ---- 交互 ----


@router.get("/question")
async def list_questions(request: Request) -> list[dict]:
    return _gw(request).hub.pending_questions()


@router.post("/question/{request_id}/reply")
async def reply_question(request_id: str, body: QuestionReplyBody, request: Request) -> dict:
    try:
        _gw(request).hub.reply_question(request_id, body.normalized())
    except RequestNotFound:
        raise NotFound("Question request not found") from None
    return {"ok": True}


@router.get("/permission")
async def list_permissions(request: Request) -> list[dict]:
    return _gw(request).hub.pending_permissions()


@router.post("/permission/{request_id}/reply")
async def reply_permission(request_id: str, body: PermissionReplyBody, request: Request) -> dict:
    try:
        _gw(request).hub.reply_permission(request_id, body.reply)
    except RequestNotFound:
        raise NotFound("Permission request not found") from None
    return {"ok": True}


# ---- SSE ----


@router.get("/event")
async def event_stream(request: Request) -> StreamingResponse:
    bus = _gw(request).bus

    async def body() -> AsyncIterator[str]:
        async for event in bus.subscribe():
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0)

    return StreamingResponse(
        body(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
