from pathlib import Path


async def test_fs_dirs_defaults_to_home(client):
    r = await client.get("/fs/dirs")
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == str(Path.home())
    assert body["roots"]
    assert all(Path(root).is_dir() for root in body["roots"])


async def test_fs_dirs_lists_only_visible_subdirectories(client, tmp_path: Path):
    (tmp_path / "b").mkdir()
    (tmp_path / "a").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file.txt").write_text("x")
    r = await client.get("/fs/dirs", params={"path": str(tmp_path)})
    body = r.json()
    assert body["path"] == str(tmp_path.resolve())
    assert body["parent"] == str(tmp_path.resolve().parent)
    assert body["entries"] == [
        {"name": "a", "path": str(tmp_path.resolve() / "a")},
        {"name": "b", "path": str(tmp_path.resolve() / "b")},
    ]


async def test_fs_dirs_root_has_no_parent(client):
    root = (await client.get("/fs/dirs")).json()["roots"][0]
    body = (await client.get("/fs/dirs", params={"path": root})).json()
    assert body["parent"] is None


async def test_fs_dirs_missing_or_file_is_404(client, tmp_path: Path):
    r = await client.get("/fs/dirs", params={"path": str(tmp_path / "nope")})
    assert r.status_code == 404
    assert r.json()["code"] == "NOT_FOUND"
    f = tmp_path / "f.txt"
    f.write_text("x")
    assert (await client.get("/fs/dirs", params={"path": str(f)})).status_code == 404


async def test_fs_mkdir(client, tmp_path: Path):
    r = await client.post("/fs/dirs", json={"parent": str(tmp_path), "name": "案例1"})
    assert r.status_code == 200
    assert r.json() == {"path": str(tmp_path.resolve() / "案例1")}
    assert (tmp_path / "案例1").is_dir()
    r = await client.post("/fs/dirs", json={"parent": str(tmp_path), "name": "案例1"})
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"
    r = await client.post("/fs/dirs", json={"parent": str(tmp_path), "name": "../x"})
    assert r.status_code == 400
    assert (
        await client.post("/fs/dirs", json={"parent": str(tmp_path / "nope"), "name": "x"})
    ).status_code == 404


async def test_ui_has_directory_browser(client):
    text = (await client.get("/")).text
    assert "/fs/dirs" in text
    assert "浏览" in text
