"""HTTP 路由（docs/04-http-api.md）。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from ..core.interaction import RequestNotFound
from ..core.models import dump
from ..core.store import SessionNotFound
from ..engines.base import ModelRef
from ..gateway import Gateway
from .errors import BadGateway, NotFound, SessionBusy, ValidationError
from .schemas import CreateSessionBody, PermissionReplyBody, PromptBody, QuestionReplyBody

router = APIRouter()


def _gw(request: Request) -> Gateway:
    return request.app.state.gateway


def _session(gw: Gateway, session_id: str):
    try:
        return gw.store.get(session_id)
    except SessionNotFound:
        raise NotFound("Session not found") from None


# ---- 健康 ----


@router.get("/health")
async def health(request: Request) -> dict:
    gw = _gw(request)
    info = gw.engine_info
    return {
        "engine": info.name if info else gw.settings.engine,
        "model": info.model if info else gw.settings.model_name,
        "tools": info.tool_names if info else [],
        "skills": info.skill_names if info else [],
        "sessions": len(gw.store),
        "permission_mode": gw.settings.permission_mode,
    }


# ---- 会话 ----


@router.post("/session")
async def create_session(body: CreateSessionBody, request: Request) -> dict:
    if not body.directory:
        raise ValidationError("directory is required")
    return await _gw(request).create_session(body.directory, body.title)


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
    if session.status == "busy":
        raise SessionBusy("Session is busy")
    model = ModelRef(
        provider_id=body.model.providerID if body.model else None,
        model_id=body.model.modelID if body.model else None,
    )
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
