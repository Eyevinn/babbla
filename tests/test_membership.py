from slack_sdk.errors import SlackApiError

from babbla.membership import deny_membership, make_membership


class FakeClient:
    def __init__(self, pages, *, error=False):
        # pages: list of (members_list, next_cursor) tuples
        self._pages = pages
        self.error = error
        self.calls = 0

    async def conversations_members(self, *, channel, limit=200, cursor=None):
        self.calls += 1
        if self.error:
            raise SlackApiError("boom", response={"ok": False, "error": "fetch_failed"})
        idx = 0 if cursor is None else int(cursor)
        members, next_cursor = self._pages[idx]
        meta = {"next_cursor": next_cursor or ""}
        return {"members": members, "response_metadata": meta}


async def test_member_present_first_page_true():
    client = FakeClient([(["U1", "U2"], None)])
    is_member = make_membership(client)
    assert await is_member("U1", "C1") is True


async def test_member_found_on_second_page_true():
    client = FakeClient([(["U2"], "1"), (["U1"], None)])
    is_member = make_membership(client)
    assert await is_member("U1", "C1") is True
    assert client.calls == 2  # paginated


async def test_non_member_false():
    client = FakeClient([(["U2", "U3"], None)])
    is_member = make_membership(client)
    assert await is_member("U1", "C1") is False


async def test_none_channel_returns_false_without_call():
    client = FakeClient([(["U1"], None)])
    is_member = make_membership(client)
    assert await is_member("U1", None) is False
    assert client.calls == 0


async def test_slack_error_fails_closed():
    client = FakeClient([], error=True)
    is_member = make_membership(client)
    assert await is_member("U1", "C1") is False


async def test_ttl_cache_hit_avoids_second_call_then_expires():
    client = FakeClient([(["U1"], None)])
    t = {"v": 1000.0}
    is_member = make_membership(client, ttl_seconds=5.0, now_fn=lambda: t["v"])
    assert await is_member("U1", "C1") is True
    assert await is_member("U1", "C1") is True
    assert client.calls == 1            # served from cache
    t["v"] = 1006.0                     # advance past ttl
    assert await is_member("U1", "C1") is True
    assert client.calls == 2            # re-fetched after expiry


async def test_deny_membership_always_false():
    assert await deny_membership("U1", "C1") is False
    assert await deny_membership("U1", None) is False
