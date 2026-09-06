from pathlib import Path

import pytest

from agent_gateway.core.models import AssistantMessage, StepFinishPart, TextPart, UserMessage
from agent_gateway.core.store import SessionNotFound, SessionStore


def test_round_trip_across_instances(tmp_path: Path):
    db = tmp_path / "g.db"
    store = SessionStore(db)
    s = store.create(str(tmp_path / "w"), "t")
    s.messages.append(UserMessage(content="hi"))
    store.save_message(s, 0)
    a = AssistantMessage(parts=[TextPart(content="yo"), StepFinishPart()])
    a.info.finish = "stop"
    a.refresh_content()
    s.messages.append(a)
    store.save_message(s, 1)
    s.status = "busy"
    store.save_session(s)
    store.close()

    reloaded = SessionStore(db)
    got = reloaded.get(s.id)
    assert got.title == "t"
    assert got.directory == s.directory
    assert got.status == "idle"
    assert [m.role for m in got.messages] == ["user", "assistant"]
    assert got.messages[1].content == "yo"
    assert got.messages[1].info.finish == "stop"
    assert got.messages[1].id == a.id


def test_message_upsert_keeps_order_and_updates_body(tmp_path: Path):
    db = tmp_path / "g.db"
    store = SessionStore(db)
    s = store.create(str(tmp_path / "w"))
    a = AssistantMessage(parts=[TextPart(content="v1")])
    s.messages.append(a)
    store.save_message(s, 0)
    a.parts[0].content = "v2"
    a.refresh_content()
    store.save_message(s, 0)
    s.messages.append(UserMessage(content="u"))
    store.save_message(s, 1)
    store.close()
    got = SessionStore(db).get(s.id)
    assert [m.content for m in got.messages] == ["v2", "u"]


def test_delete_cascades(tmp_path: Path):
    db = tmp_path / "g.db"
    store = SessionStore(db)
    s = store.create(str(tmp_path / "w"))
    s.messages.append(UserMessage(content="hi"))
    store.save_message(s, 0)
    store.delete(s.id)
    store.close()
    reloaded = SessionStore(db)
    assert len(reloaded) == 0
    with pytest.raises(SessionNotFound):
        reloaded.get(s.id)
    assert reloaded.count_messages() == 0


def test_memory_db_does_not_touch_disk(tmp_path: Path):
    store = SessionStore(None)
    store.create(str(tmp_path / "w"))
    assert len(store) == 1
    assert not list(tmp_path.glob("*.db"))
