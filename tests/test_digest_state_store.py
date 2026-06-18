import pytest
from babbla.session_store import DigestState, DigestStateStore


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
