import asyncio

from agent_gateway.engines.base import Question, QuestionOption, StepFinish

from .conftest import SseCollector, create_session, prompt
from .scripted_engine import AskPermission, AskQuestion, ScriptedEngine

QUESTIONS = [
    Question(
        question="用哪个方案？",
        options=[QuestionOption(label="方案 A", description="快"), QuestionOption(label="方案 B")],
    )
]


async def test_question_round_trip(client, workdir, engine: ScriptedEngine, settings):
    settings.question_timeout = 5
    engine.script = [AskQuestion(QUESTIONS), StepFinish(finish="stop")]
    sid = await create_session(client, workdir)
    async with SseCollector(client) as sse:
        task = asyncio.create_task(prompt(client, sid, "go"))
        asked = await sse.wait_for("question.asked", sid)
        pending = (await client.get("/question")).json()
        assert len(pending) == 1
        req = pending[0]
        assert req["id"] == asked["properties"]["id"]
        assert req["sessionID"] == sid
        assert req["questions"] == [
            {
                "question": "用哪个方案？",
                "options": [
                    {"label": "方案 A", "description": "快"},
                    {"label": "方案 B", "description": ""},
                ],
            }
        ]
        r = await client.post(f"/question/{req['id']}/reply", json={"answers": [["方案 B"]]})
        assert r.json() == {"ok": True}
        assert (await task).status_code == 204
    assert engine.replies == [[["方案 B"]]]
    assert (await client.get("/question")).json() == []
    last = (await client.get(f"/session/{sid}/message")).json()[-1]
    assert last["content"] == "answer=方案 B"


async def test_question_timeout_uses_first_option(client, workdir, engine: ScriptedEngine):
    engine.script = [AskQuestion(QUESTIONS), StepFinish(finish="stop")]
    sid = await create_session(client, workdir)
    assert (await prompt(client, sid, "go")).status_code == 204
    assert engine.replies == [[["方案 A"]]]
    assert (await client.get("/question")).json() == []


async def test_reply_unknown_question_is_404(client):
    r = await client.post("/question/req_nope/reply", json={"answers": [["x"]]})
    assert r.status_code == 404


async def test_permission_round_trip(client, workdir, engine: ScriptedEngine, settings):
    settings.question_timeout = 5
    engine.script = [AskPermission("bash.execute", ["rm -rf build"]), StepFinish(finish="stop")]
    sid = await create_session(client, workdir)
    async with SseCollector(client) as sse:
        task = asyncio.create_task(prompt(client, sid, "go"))
        asked = await sse.wait_for("permission.asked", sid)
        assert asked["properties"]["permission"] == "bash.execute"
        assert asked["properties"]["patterns"] == ["rm -rf build"]
        pending = (await client.get("/permission")).json()
        assert pending[0]["id"] == asked["properties"]["id"]
        assert pending[0]["sessionID"] == sid
        r = await client.post(
            f"/permission/{pending[0]['id']}/reply", json={"reply": "reject", "message": "no"}
        )
        assert r.json() == {"ok": True}
        await task
    assert engine.replies == ["reject"]
    assert (await client.get("/permission")).json() == []


async def test_permission_timeout_allows(client, workdir, engine: ScriptedEngine):
    engine.script = [AskPermission("file.write", ["a.txt"]), StepFinish(finish="stop")]
    sid = await create_session(client, workdir)
    await prompt(client, sid, "go")
    assert engine.replies == ["once"]


async def test_permission_auto_mode_skips_asking(client, workdir, engine: ScriptedEngine, settings):
    settings.permission_mode = "auto"
    engine.script = [AskPermission("file.write", ["a.txt"]), StepFinish(finish="stop")]
    sid = await create_session(client, workdir)
    async with SseCollector(client) as sse:
        await prompt(client, sid, "go")
        await sse.wait_for("session.idle", sid)
    assert "permission.asked" not in sse.types()
    assert engine.replies == ["always"]


async def test_abort_while_waiting_for_question(client, workdir, engine: ScriptedEngine, settings):
    settings.question_timeout = 5
    engine.script = [AskQuestion(QUESTIONS), StepFinish(finish="stop")]
    sid = await create_session(client, workdir)
    task = asyncio.create_task(prompt(client, sid, "go"))
    await asyncio.sleep(0.2)
    assert len((await client.get("/question")).json()) == 1
    await client.post(f"/session/{sid}/abort")
    assert (await task).status_code == 204
    assert (await client.get("/question")).json() == []
