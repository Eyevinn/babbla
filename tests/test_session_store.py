from concierge.session_store import SessionStore


async def test_put_then_get_roundtrip(tmp_path):
    store = SessionStore(str(tmp_path / "s.db"))
    await store.put_session("t1", "sess-abc")
    assert await store.get_session("t1") == "sess-abc"
    store.close()


async def test_missing_thread_returns_none(tmp_path):
    store = SessionStore(str(tmp_path / "s.db"))
    assert await store.get_session("nope") is None
    store.close()


async def test_put_overwrites(tmp_path):
    store = SessionStore(str(tmp_path / "s.db"))
    await store.put_session("t1", "sess-1")
    await store.put_session("t1", "sess-2")
    assert await store.get_session("t1") == "sess-2"
    store.close()


async def test_ttl_eviction(tmp_path):
    clock = {"now": 1000.0}
    store = SessionStore(str(tmp_path / "s.db"), ttl_seconds=100, time_fn=lambda: clock["now"])
    await store.put_session("t1", "sess-old")
    clock["now"] = 1101.0  # 101s later, past the 100s TTL
    assert await store.get_session("t1") is None
    # expired row is gone, so a fresh put starts clean
    await store.put_session("t1", "sess-new")
    assert await store.get_session("t1") == "sess-new"
    store.close()


async def test_persists_across_instances(tmp_path):
    path = str(tmp_path / "s.db")
    store = SessionStore(path)
    await store.put_session("t1", "sess-abc")
    store.close()
    store2 = SessionStore(path)
    assert await store2.get_session("t1") == "sess-abc"
    store2.close()
