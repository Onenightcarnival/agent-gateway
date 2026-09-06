import httpx

from agent_gateway.tools.http import make_async_client, make_sync_client


def test_async_client_policy():
    client = make_async_client(headers={"X": "1"}, timeout=httpx.Timeout(5))
    assert isinstance(client, httpx.AsyncClient)
    assert client.trust_env is False
    assert client.headers["X"] == "1"
    assert client.timeout == httpx.Timeout(5)


def test_sync_client_policy():
    client = make_sync_client()
    assert isinstance(client, httpx.Client)
    assert client.trust_env is False


def test_verify_disabled():
    for client in (make_async_client(), make_sync_client()):
        transport = client._transport
        pool = transport._pool
        assert pool._ssl_context.check_hostname is False
        assert pool._ssl_context.verify_mode == 0


async def test_debug_ui_is_served(client):
    for path in ("/", "/ui"):
        r = await client.get(path)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert "Agent Gateway" in r.text
        assert "/event" in r.text


async def test_list_sessions(client, workdir):
    assert (await client.get("/session")).json() == []
    r = await client.post("/session", json={"directory": workdir, "title": "a"})
    listed = (await client.get("/session")).json()
    assert [s["id"] for s in listed] == [r.json()["id"]]
    assert listed[0]["message_count"] == 0
