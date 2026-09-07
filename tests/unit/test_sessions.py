from pathlib import Path

from .conftest import create_session
from .scripted_engine import ScriptedEngine


async def test_create_session_returns_spec_shape_and_creates_directory(client, workdir, engine):
    r = await client.post("/session", json={"title": "群助手", "directory": workdir})
    assert r.status_code == 200
    body = r.json()
    assert body["id"].startswith("ses_")
    assert body["title"] == "群助手"
    assert body["status"] == "idle"
    assert body["created_at"].endswith("Z")
    assert Path(workdir).is_dir()
    assert engine.opened[0].id == body["id"]
    assert engine.opened[0].directory == str(Path(workdir).resolve())


async def test_create_session_without_title_generates_one(client, workdir):
    r = await client.post("/session", json={"directory": workdir})
    assert r.status_code == 200
    assert r.json()["title"]


async def test_create_session_requires_directory(client):
    r = await client.post("/session", json={"title": "x"})
    assert r.status_code == 400
    assert r.json() == {"code": "VALIDATION_ERROR", "message": "directory is required"}


async def test_get_session_includes_message_count(client, workdir):
    sid = await create_session(client, workdir)
    r = await client.get(f"/session/{sid}")
    assert r.status_code == 200
    assert r.json()["message_count"] == 0
    assert r.json()["status"] == "idle"


async def test_get_unknown_session_is_404(client):
    r = await client.get("/session/ses_nope")
    assert r.status_code == 404
    assert r.json() == {"code": "NOT_FOUND", "message": "Session not found"}


async def test_session_status_route_wins_over_session_id(client, workdir):
    a = await create_session(client, workdir)
    b = await create_session(client, workdir)
    r = await client.get("/session/status")
    assert r.status_code == 200
    assert r.json() == {a: {"type": "idle"}, b: {"type": "idle"}}


async def test_delete_session(client, workdir, engine: ScriptedEngine):
    sid = await create_session(client, workdir)
    r = await client.delete(f"/session/{sid}")
    assert r.json() == {"ok": True}
    assert engine.closed == [sid]
    assert (await client.get(f"/session/{sid}")).status_code == 404
    assert (await client.delete(f"/session/{sid}")).status_code == 404


async def test_unknown_route_uses_error_format(client):
    r = await client.get("/nope")
    assert r.status_code == 404
    assert r.json()["code"] == "NOT_FOUND"


async def test_health_reports_engine(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] == "scripted"
    assert body["model"] == "scripted-model"
    assert body["tools"] == ["t1"]
    assert body["sessions"] == 0
    assert isinstance(body["shell_sandbox"], bool)
