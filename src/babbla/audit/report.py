from __future__ import annotations

from babbla.audit.assess import AuditReport, OK, THIN, MISSING


def render_binding(report: AuditReport) -> str:
    """The channels.yaml list-item block (indented under `projects:`)."""
    return (
        f"  - name: {report.repo}\n"
        f"    owner: {report.owner}\n"
        f"    repo: {report.repo}\n"
        f"    visibility: {report.visibility}  # GitHub value; 'internal' is an org choice\n"
        f"    channel_id: null  # set to your Slack channel id\n"
        f"    dm: false         # set true for the one DM-bound pilot project (dm: true)\n"
    )


_SYMBOLS = {OK: "✓", THIN: "⚠", MISSING: "✗"}
_ASCII = {OK: "OK", THIN: "THIN", MISSING: "MISSING"}
_RULE = "─" * 64


def _marker(status: str, color: bool) -> str:
    return _SYMBOLS[status] if color else f"[{_ASCII[status]}]"


def render_report(report: AuditReport, *, color: bool = True) -> str:
    """Full human-readable audit readout, ending with the channels.yaml stub."""
    lines: list[str] = []
    lines.append(f"Babbla repo audit — {report.owner}/{report.repo}")
    lines.append(f"Visibility: {report.visibility} · default branch: {report.default_branch}")
    lines.append("")
    lines.append('Why-surfaces (repo = source of truth for "why")')
    for f in report.findings:
        lines.append(f"  {_marker(f.status, color)} {f.name:<16} {f.detail}")
    lines.append("")
    lines.append(f"Deploy style: {report.deploy_style}  ({report.deploy_detail})")
    lines.append("")
    lines.append(f"Verdict: {report.verdict}")

    recs = [f.recommendation for f in report.findings if f.recommendation]
    if recs:
        lines.append("Recommendations:")
        for r in recs:
            lines.append(f"  • {r}")
    lines.append("")
    lines.append("Add this to config/channels.yaml under `projects:` " + _RULE[:20])
    lines.append(render_binding(report).rstrip("\n"))
    lines.append(_RULE)
    return "\n".join(lines) + "\n"
