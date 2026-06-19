from babbla.session_store import SessionStore, LobbyThreadStore, PersonalSubStore


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


async def test_lobby_store_roundtrip(tmp_path):
    store = LobbyThreadStore(str(tmp_path / "s.db"))
    await store.put("t1", "MyTV")
    assert await store.get("t1") == "MyTV"
    store.close()


async def test_lobby_store_missing_returns_none(tmp_path):
    store = LobbyThreadStore(str(tmp_path / "s.db"))
    assert await store.get("nope") is None
    store.close()


async def test_lobby_store_overwrites(tmp_path):
    store = LobbyThreadStore(str(tmp_path / "s.db"))
    await store.put("t1", "MyTV")
    await store.put("t1", "Other")
    assert await store.get("t1") == "Other"
    store.close()


async def test_lobby_store_ttl_eviction(tmp_path):
    clock = {"now": 1000.0}
    store = LobbyThreadStore(str(tmp_path / "s.db"), ttl_seconds=100, time_fn=lambda: clock["now"])
    await store.put("t1", "MyTV")
    clock["now"] = 1101.0
    assert await store.get("t1") is None
    store.close()


async def test_personal_sub_add_list_idempotent(tmp_path):
    s = PersonalSubStore(str(tmp_path / "s.db"))
    await s.add("U1", "MyTV")
    await s.add("U1", "MyTV")          # idempotent
    await s.add("U1", "Stream")
    assert await s.list_for("U1") == ("MyTV", "Stream")   # insertion order
    assert await s.list_for("U2") == ()
    s.close()


async def test_personal_sub_remove_idempotent(tmp_path):
    s = PersonalSubStore(str(tmp_path / "s.db"))
    await s.add("U1", "MyTV")
    await s.remove("U1", "MyTV")
    await s.remove("U1", "MyTV")       # no error on missing
    assert await s.list_for("U1") == ()
    s.close()


async def test_personal_sub_all_user_ids(tmp_path):
    s = PersonalSubStore(str(tmp_path / "s.db"))
    await s.add("U1", "MyTV")
    await s.add("U2", "Stream")
    assert sorted(await s.all_user_ids()) == ["U1", "U2"]
    s.close()


async def test_personal_cadence_default_none_then_roundtrip(tmp_path):
    s = PersonalSubStore(str(tmp_path / "s.db"))
    assert await s.get_cadence("U1") is None
    await s.set_cadence("U1", "daily")
    assert await s.get_cadence("U1") == "daily"
    await s.set_cadence("U1", "off")
    assert await s.get_cadence("U1") == "off"
    s.close()
