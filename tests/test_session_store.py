from babbla.session_store import SessionStore, LobbyThreadStore, PersonalSubStore, PersonalDigestStateStore


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


async def test_personal_digest_state_empty(tmp_path):
    s = PersonalDigestStateStore(str(tmp_path / "s.db"))
    state = await s.get("U1")
    assert state.watermarks == {} and state.last_digest_at is None
    s.close()


async def test_personal_digest_state_advance_roundtrip(tmp_path):
    s = PersonalDigestStateStore(str(tmp_path / "s.db"))
    await s.advance("U1", {"MyTV": "sha1", "Stream": "sha2"}, 1000.0)
    state = await s.get("U1")
    assert state.watermarks == {"MyTV": "sha1", "Stream": "sha2"}
    assert state.last_digest_at == 1000.0
    # isolation between users
    assert (await s.get("U2")).watermarks == {}
    s.close()


async def test_personal_topics_add_list_remove(tmp_path):
    s = PersonalSubStore(str(tmp_path / "t.db"))
    await s.add_topic("U1", "MyTV", "security", "auth, secrets, CVEs")
    await s.add_topic("U1", "MyTV", "playback", "HLS, player, buffering")
    await s.add_topic("U1", "Babbla", "lobby", "routing, classifier")
    topics = await s.topics_for("U1")
    assert topics == {
        "MyTV": (("security", "auth, secrets, CVEs"), ("playback", "HLS, player, buffering")),
        "Babbla": (("lobby", "routing, classifier"),),
    }
    await s.remove_topic("U1", "MyTV", "security")
    assert (await s.topics_for("U1"))["MyTV"] == (("playback", "HLS, player, buffering"),)
    s.close()


async def test_personal_topics_readd_updates_description_and_normalizes(tmp_path):
    s = PersonalSubStore(str(tmp_path / "t.db"))
    await s.add_topic("U1", "MyTV", "Security", "first")
    await s.add_topic("U1", "MyTV", "  security ", "second")   # same identity, normalized
    topics = await s.topics_for("U1")
    assert topics == {"MyTV": (("security", "second"),)}        # one row, updated desc
    s.close()


async def test_personal_topics_isolated_per_user(tmp_path):
    s = PersonalSubStore(str(tmp_path / "t.db"))
    await s.add_topic("U1", "MyTV", "security", "x")
    assert await s.topics_for("U2") == {}
    s.close()


# ---------------------------------------------------------------------------
# AnswerStore — maps a question (channel, parent_ts) -> bot answer message(s)
# so a deleted question can have its orphaned reply cleaned up.
# ---------------------------------------------------------------------------


async def test_answer_store_record_then_pop(tmp_path):
    from babbla.session_store import AnswerStore
    store = AnswerStore(str(tmp_path / "a.db"))
    await store.record("C1", "q1", "ans1")
    assert await store.pop("C1", "q1") == ("ans1",)
    store.close()


async def test_answer_store_pop_is_idempotent(tmp_path):
    from babbla.session_store import AnswerStore
    store = AnswerStore(str(tmp_path / "a.db"))
    await store.record("C1", "q1", "ans1")
    assert await store.pop("C1", "q1") == ("ans1",)
    assert await store.pop("C1", "q1") == ()   # already removed
    store.close()


async def test_answer_store_pop_unknown_returns_empty(tmp_path):
    from babbla.session_store import AnswerStore
    store = AnswerStore(str(tmp_path / "a.db"))
    assert await store.pop("C1", "nope") == ()
    store.close()


async def test_answer_store_multiple_answers_per_parent(tmp_path):
    from babbla.session_store import AnswerStore
    store = AnswerStore(str(tmp_path / "a.db"))
    await store.record("C1", "q1", "ans1")
    await store.record("C1", "q1", "ans2")
    assert set(await store.pop("C1", "q1")) == {"ans1", "ans2"}
    store.close()


async def test_answer_store_channel_scoped(tmp_path):
    from babbla.session_store import AnswerStore
    store = AnswerStore(str(tmp_path / "a.db"))
    await store.record("C1", "q1", "ans1")
    assert await store.pop("C2", "q1") == ()   # same parent ts, different channel
    assert await store.pop("C1", "q1") == ("ans1",)
    store.close()


async def test_answer_store_prunes_expired_on_write(tmp_path):
    from babbla.session_store import AnswerStore
    clock = {"now": 1000.0}
    store = AnswerStore(str(tmp_path / "a.db"), ttl_seconds=100, time_fn=lambda: clock["now"])
    await store.record("C1", "old", "ans-old")
    clock["now"] = 1201.0                       # past the 100s TTL
    await store.record("C1", "new", "ans-new")  # write prunes the stale row
    assert await store.pop("C1", "old") == ()
    assert await store.pop("C1", "new") == ("ans-new",)
    store.close()
