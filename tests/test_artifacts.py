from pathlib import Path

from babbla.agent_runner import Artifact, CitedAnswer, _collect_artifacts, _stage_skills


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
