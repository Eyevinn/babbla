from babbla.audit.assess import AuditReport, SurfaceFinding, OK
from babbla.audit.report import render_binding
from babbla.config import load_config


def _report(**over):
    base = dict(
        owner="Wkkkkk", repo="MyTV", visibility="public", default_branch="main",
        findings=(SurfaceFinding("README", OK, "1.8 KB", None),),
        deploy_style="Pages", deploy_detail="signal: Pages enabled",
        verdict="GOOD", exit_code=0,
    )
    base.update(over)
    return AuditReport(**base)


def test_binding_roundtrips_through_load_config(tmp_path):
    block = render_binding(_report())
    cfg_file = tmp_path / "channels.yaml"
    cfg_file.write_text("projects:\n" + block, encoding="utf-8")

    cfg = load_config(cfg_file)
    assert len(cfg.bindings) == 1
    b = cfg.bindings[0]
    assert (b.name, b.owner, b.repo, b.visibility) == ("MyTV", "Wkkkkk", "MyTV", "public")
    assert b.channel_id is None
    assert b.dm is False


def test_binding_carries_helpful_comments():
    block = render_binding(_report())
    assert "set to your Slack channel id" in block
    assert "dm: true" in block  # the guidance comment mentions the dm flag
