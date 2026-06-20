from babbla.session_store import ActionCursorStore


async def test_get_unknown_returns_none(tmp_path):
    store = ActionCursorStore(str(tmp_path / "s.db"))
    assert await store.get("adr:MyTV") is None
    store.close()


async def test_set_then_get_roundtrip(tmp_path):
    store = ActionCursorStore(str(tmp_path / "s.db"))
    await store.set("adr:MyTV", "0003-read-only.md")
    assert await store.get("adr:MyTV") == "0003-read-only.md"
    store.close()


async def test_set_twice_upserts(tmp_path):
    store = ActionCursorStore(str(tmp_path / "s.db"))
    await store.set("adr:MyTV", "0003-read-only.md")
    await store.set("adr:MyTV", "0004-deploy.md")
    assert await store.get("adr:MyTV") == "0004-deploy.md"
    store.close()


async def test_persists_across_instances(tmp_path):
    path = str(tmp_path / "s.db")
    store = ActionCursorStore(path)
    await store.set("adr:MyTV", "0001-hybrid.md")
    store.close()
    store2 = ActionCursorStore(path)
    assert await store2.get("adr:MyTV") == "0001-hybrid.md"
    store2.close()
