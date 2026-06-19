import pytest
from babbla.session_store import DigestState, DigestStateStore, SharedDigestState, SharedDigestStateStore


@pytest.fixture
def store(tmp_path):
    return DigestStateStore(str(tmp_path / "babbla.db"))


async def test_unknown_channel_is_empty(store):
    st = await store.get("C0XXXXXXXXX")
    assert st == DigestState(watermark_sha=None, last_digest_at=None)


async def test_advance_then_get_roundtrips(store):
    await store.advance("C0XXXXXXXXX", "abc123", 1000.0)
    assert await store.get("C0XXXXXXXXX") == DigestState("abc123", 1000.0)


async def test_advance_is_idempotent_upsert(store):
    await store.advance("C0XXXXXXXXX", "abc123", 1000.0)
    await store.advance("C0XXXXXXXXX", "def456", 2000.0)
    assert await store.get("C0XXXXXXXXX") == DigestState("def456", 2000.0)


async def test_channels_are_independent(store):
    await store.advance("C0AAA", "a1", 10.0)
    await store.advance("C0BBB", "b1", 20.0)
    assert (await store.get("C0AAA")).watermark_sha == "a1"
    assert (await store.get("C0BBB")).watermark_sha == "b1"


@pytest.fixture
def shared(tmp_path):
    s = SharedDigestStateStore(str(tmp_path / "shared.db"))
    yield s
    s.close()


async def test_shared_unknown_channel_is_empty(shared):
    st = await shared.get("C900")
    assert st == SharedDigestState(watermarks={}, last_digest_at=None)


async def test_shared_advance_roundtrips_multiple_projects(shared):
    await shared.advance("C900", {"MyTV": "h1", "Stream": "h2"}, 1000.0)
    st = await shared.get("C900")
    assert st.watermarks == {"MyTV": "h1", "Stream": "h2"}
    assert st.last_digest_at == 1000.0


async def test_shared_advance_updates_and_keeps_timer_consistent(shared):
    await shared.advance("C900", {"MyTV": "h1", "Stream": "h2"}, 1000.0)
    await shared.advance("C900", {"MyTV": "h3", "Stream": "h2"}, 2000.0)
    st = await shared.get("C900")
    assert st.watermarks == {"MyTV": "h3", "Stream": "h2"}
    assert st.last_digest_at == 2000.0      # consistent across rows


async def test_shared_channels_independent(shared):
    await shared.advance("C900", {"MyTV": "h1"}, 10.0)
    await shared.advance("C901", {"Other": "z1"}, 20.0)
    assert (await shared.get("C900")).watermarks == {"MyTV": "h1"}
    assert (await shared.get("C901")).watermarks == {"Other": "z1"}


from babbla.session_store import ActionTimerStore


@pytest.fixture
def timer(tmp_path):
    s = ActionTimerStore(str(tmp_path / "timer.db"))
    yield s
    s.close()


async def test_timer_unknown_key_is_none(timer):
    assert await timer.get("quiz:MyTV") is None


async def test_timer_advance_roundtrips(timer):
    await timer.advance("quiz:MyTV", 1234.0)
    assert await timer.get("quiz:MyTV") == 1234.0


async def test_timer_advance_is_upsert(timer):
    await timer.advance("quiz:MyTV", 1.0)
    await timer.advance("quiz:MyTV", 2.0)
    assert await timer.get("quiz:MyTV") == 2.0
