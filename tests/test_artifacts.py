from babbla.agent_runner import Artifact, CitedAnswer, _collect_artifacts, _stage_skills
from babbla.digest.poster import SlackPoster


def test_cited_answer_artifacts_default_empty():
    assert CitedAnswer(text="x", session_id=None).artifacts == ()


def test_collect_artifacts_reads_files_and_skips_hidden(tmp_path):
    (tmp_path / "architecture.html").write_text("<svg/>")
    (tmp_path / "notes.md").write_bytes(b"hi")
    hidden = tmp_path / ".claude" / "skills" / "x"
    hidden.mkdir(parents=True)
    (hidden / "SKILL.md").write_text("staged skill, not an artifact")
    arts = _collect_artifacts(str(tmp_path))
    names = {a.filename for a in arts}
    assert names == {"architecture.html", "notes.md"}
    assert Artifact("notes.md", b"hi") in arts


def test_stage_skills_copies_into_dot_claude(tmp_path):
    pool = tmp_path / "pool" / "echo-skill"
    pool.mkdir(parents=True)
    (pool / "SKILL.md").write_text("---\nname: echo-skill\ndescription: x\n---\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _stage_skills(str(tmp_path / "pool"), ("echo-skill",), str(scratch))
    assert (scratch / ".claude" / "skills" / "echo-skill" / "SKILL.md").is_file()


class FakeUploadClient:
    def __init__(self, fail=False):
        self.uploads = []
        self._fail = fail

    async def files_upload_v2(self, **kwargs):
        if self._fail:
            raise RuntimeError("missing files:write scope")
        self.uploads.append(kwargs)
        return {"ok": True}


async def test_upload_file_forwards_fields():
    client = FakeUploadClient()
    ok = await SlackPoster(client).upload_file(
        "C1", filename="architecture.html", content=b"<svg/>", thread_ts="t1"
    )
    assert ok is True
    up = client.uploads[0]
    assert up["channel"] == "C1"
    assert up["filename"] == "architecture.html"
    assert up["content"] == b"<svg/>"
    assert up["thread_ts"] == "t1"
    assert up["title"] == "architecture.html"  # defaults to filename


async def test_upload_file_degrades_on_failure():
    client = FakeUploadClient(fail=True)
    ok = await SlackPoster(client).upload_file("C1", filename="x.md", content=b"y")
    assert ok is False  # logged, not raised


async def test_adapter_uploads_artifacts_threaded():
    from babbla import slack_adapter

    class FakeOrch:
        async def handle_ask(self, **kwargs):
            return CitedAnswer(text="drew it", session_id="s",
                               artifacts=(Artifact("architecture.html", b"<svg/>"),))

    class FakeClient:
        def __init__(self):
            self.posts = []
            self.updated = []
        async def chat_postMessage(self, **kwargs):
            self.posts.append(kwargs)
            return {"ts": f"msg{len(self.posts)}"}
        async def chat_update(self, **kwargs):
            self.updated.append(kwargs)

    client = FakeClient()
    await slack_adapter.process_ask(
        text="draw", channel="C1", thread_ts="t1", is_dm=False,
        client=client, orchestrator=FakeOrch(), user_id="U1",
    )
    # First post is the placeholder; second is the artifact message.
    assert len(client.posts) == 2
    artifact_post = client.posts[1]
    assert artifact_post["thread_ts"] == "t1"
    assert "architecture.html" in artifact_post["text"]
    assert "<svg/>" in artifact_post["text"]


async def test_adapter_artifact_upload_failure_does_not_crash():
    from babbla import slack_adapter

    class FakeOrch:
        async def handle_ask(self, **kwargs):
            return CitedAnswer(text="ok", session_id="s",
                               artifacts=(Artifact("x.md", b"y"),))

    class FlakyClient:
        def __init__(self):
            self._calls = 0
        async def chat_postMessage(self, **kwargs):
            self._calls += 1
            if self._calls > 1:
                raise RuntimeError("transient failure")
            return {"ts": "ph1"}
        async def chat_update(self, **kwargs):
            pass

    # Must not raise even when the artifact post fails.
    await slack_adapter.process_ask(
        text="q", channel="C1", thread_ts="t1", is_dm=False,
        client=FlakyClient(), orchestrator=FakeOrch(), user_id="U1",
    )
