from __future__ import annotations

from babbla.audit.assess import AuditReport


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
