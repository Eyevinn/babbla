from babbla.audit.assess import AuditReport, SurfaceFinding, OK, THIN, MISSING
from babbla.audit.report import render_report


def _report(**over):
    base = dict(
        owner="Wkkkkk", repo="MyTV", visibility="public", default_branch="main",
        findings=(
            SurfaceFinding("README", OK, "1.8 KB", None),
            SurfaceFinding("PR bodies", THIN, "4/20 recent PRs have descriptive bodies",
                           "Write descriptive PR bodies — see docs/RECOMMENDATIONS.md §1."),
            SurfaceFinding("CLAUDE.md", MISSING, "absent", None),
        ),
        deploy_style="Pages", deploy_detail="signal: Pages enabled",
        verdict="GOOD", exit_code=0,
    )
    base.update(over)
    return AuditReport(**base)


def test_readout_has_header_findings_deploy_verdict_and_binding():
    text = render_report(_report(), color=False)
    assert "Wkkkkk/MyTV" in text
    assert "README" in text and "1.8 KB" in text
    assert "Deploy style: Pages" in text
    assert "Verdict: GOOD" in text
    assert "config/channels.yaml" in text          # the stub section header
    assert "  - name: MyTV" in text                 # the stub itself is embedded


def test_recommendations_listed_for_thin_surfaces():
    text = render_report(_report(), color=False)
    assert "docs/RECOMMENDATIONS.md §1" in text


def test_no_color_uses_ascii_markers():
    text = render_report(_report(), color=False)
    assert "OK" in text and "THIN" in text and "MISSING" in text
    assert "✓" not in text and "⚠" not in text and "✗" not in text


def test_color_uses_symbols():
    text = render_report(_report(), color=True)
    assert "✓" in text   # ok
    assert "⚠" in text   # thin
    assert "✗" in text   # missing
