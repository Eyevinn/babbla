import textwrap
import pytest
from babbla.config import load_config


def _write(tmp_path, body):
    p = tmp_path / "channels.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(p)


def test_absent_digest_block_means_disabled(tmp_path):
    cfg = load_config(_write(tmp_path, """
        projects:
          - {name: MyTV, owner: Wkkkkk, repo: MyTV, visibility: public, channel_id: C0XXXXXXXXX}
    """))
    assert cfg.bindings[0].digest is None
    assert cfg.digest_bindings() == ()


def test_cadence_off_means_disabled(tmp_path):
    cfg = load_config(_write(tmp_path, """
        projects:
          - name: MyTV
            owner: Wkkkkk
            repo: MyTV
            visibility: public
            channel_id: C0XXXXXXXXX
            digest: {cadence: off, tz: UTC, anchor: branch}
    """))
    assert cfg.bindings[0].digest is None


def test_branch_digest_parsed(tmp_path):
    cfg = load_config(_write(tmp_path, """
        projects:
          - name: MyTV
            owner: Wkkkkk
            repo: MyTV
            visibility: public
            channel_id: C0XXXXXXXXX
            digest: {cadence: weekly, tz: Europe/Stockholm, anchor: branch}
    """))
    d = cfg.bindings[0].digest
    assert (d.cadence, d.tz, d.anchor, d.deploy_workflow) == ("weekly", "Europe/Stockholm", "branch", None)
    assert cfg.digest_bindings() == (cfg.bindings[0],)


def test_deploy_digest_requires_workflow(tmp_path):
    with pytest.raises(ValueError, match="workflow"):
        load_config(_write(tmp_path, """
            projects:
              - name: S
                owner: ITV
                repo: stream-starter
                visibility: private
                channel_id: C0YYYYYYYYY
                digest: {cadence: weekly, tz: Europe/London, anchor: deploy}
        """))


def test_deploy_digest_parsed(tmp_path):
    cfg = load_config(_write(tmp_path, """
        projects:
          - name: S
            owner: ITV
            repo: stream-starter
            visibility: private
            channel_id: C0YYYYYYYYY
            digest: {cadence: weekly, tz: Europe/London, anchor: deploy, deploy: {workflow: cicd_prod.yml}}
    """))
    assert cfg.bindings[0].digest.deploy_workflow == "cicd_prod.yml"


def test_bad_cadence_rejected(tmp_path):
    with pytest.raises(ValueError, match="cadence"):
        load_config(_write(tmp_path, """
            projects:
              - {name: MyTV, owner: Wkkkkk, repo: MyTV, visibility: public, channel_id: C0XXXXXXXXX,
                 digest: {cadence: hourly, tz: UTC, anchor: branch}}
        """))


def test_bad_anchor_rejected(tmp_path):
    with pytest.raises(ValueError, match="anchor"):
        load_config(_write(tmp_path, """
            projects:
              - {name: MyTV, owner: Wkkkkk, repo: MyTV, visibility: public, channel_id: C0XXXXXXXXX,
                 digest: {cadence: weekly, tz: UTC, anchor: tags}}
        """))


def test_bad_tz_rejected(tmp_path):
    with pytest.raises(ValueError, match="tz|zone"):
        load_config(_write(tmp_path, """
            projects:
              - {name: MyTV, owner: Wkkkkk, repo: MyTV, visibility: public, channel_id: C0XXXXXXXXX,
                 digest: {cadence: weekly, tz: Mars/Phobos, anchor: branch}}
        """))


def test_digest_binding_needs_channel_id(tmp_path):
    # A digest-enabled project with no channel_id is excluded from digest_bindings.
    cfg = load_config(_write(tmp_path, """
        projects:
          - name: DMOnly
            owner: o
            repo: r
            visibility: public
            dm: true
            digest: {cadence: weekly, tz: UTC, anchor: branch}
    """))
    assert cfg.digest_bindings() == ()


def _write_cfg(tmp_path, body):
    p = tmp_path / "channels.yaml"
    p.write_text(body)
    return p


_PROJECT_WITH_TOPIC = (
    "projects:\n"
    "  - name: MyTV\n    owner: o\n    repo: MyTV\n    visibility: public\n"
    "    channel_id: C1\n    dm: false\n"
    "    digest:\n      cadence: weekly\n      tz: UTC\n      anchor: branch\n"
    "      topic:\n        name: security\n        description: auth, secrets, CVEs\n"
)


def test_digest_topic_parses(tmp_path):
    from babbla.config import load_config, Topic
    cfg = load_config(_write_cfg(tmp_path, _PROJECT_WITH_TOPIC))
    assert cfg.bindings[0].digest.topic == Topic(name="security", description="auth, secrets, CVEs")


def test_digest_topic_absent_is_none(tmp_path):
    from babbla.config import load_config
    body = (
        "projects:\n  - name: MyTV\n    owner: o\n    repo: MyTV\n    visibility: public\n"
        "    channel_id: C1\n    dm: false\n"
        "    digest:\n      cadence: weekly\n      tz: UTC\n      anchor: branch\n"
    )
    cfg = load_config(_write_cfg(tmp_path, body))
    assert cfg.bindings[0].digest.topic is None


def test_digest_topic_missing_name_raises(tmp_path):
    import pytest
    from babbla.config import load_config
    body = (
        "projects:\n  - name: MyTV\n    owner: o\n    repo: MyTV\n    visibility: public\n"
        "    channel_id: C1\n    dm: false\n"
        "    digest:\n      cadence: weekly\n      tz: UTC\n      anchor: branch\n"
        "      topic:\n        description: only a description\n"
    )
    with pytest.raises(ValueError, match="topic requires both name and description"):
        load_config(_write_cfg(tmp_path, body))


def test_digest_topic_missing_description_raises(tmp_path):
    import pytest
    from babbla.config import load_config
    body = (
        "projects:\n  - name: MyTV\n    owner: o\n    repo: MyTV\n    visibility: public\n"
        "    channel_id: C1\n    dm: false\n"
        "    digest:\n      cadence: weekly\n      tz: UTC\n      anchor: branch\n"
        "      topic:\n        name: security\n"
    )
    with pytest.raises(ValueError, match="topic requires both name and description"):
        load_config(_write_cfg(tmp_path, body))


_PROJECT_WITH_SIGNALS = (
    "projects:\n"
    "  - name: MyTV\n    owner: o\n    repo: MyTV\n    visibility: public\n"
    "    channel_id: C1\n    dm: false\n"
    "    digest:\n      cadence: weekly\n      tz: UTC\n      anchor: branch\n"
    "      topic:\n        name: security\n        description: auth, secrets, CVEs\n"
    "        labels: [security, area/auth]\n"
    "        paths: ['src/babbla/access.py', 'src/babbla/**']\n"
)


def test_digest_topic_parses_labels_and_paths(tmp_path):
    from babbla.config import load_config, Topic
    cfg = load_config(_write_cfg(tmp_path, _PROJECT_WITH_SIGNALS))
    topic = cfg.bindings[0].digest.topic
    assert topic == Topic(
        name="security", description="auth, secrets, CVEs",
        labels=("security", "area/auth"),
        paths=("src/babbla/access.py", "src/babbla/**"),
    )
    assert topic.has_signals is True


def test_digest_topic_without_signals_has_empty_tuples(tmp_path):
    from babbla.config import load_config
    cfg = load_config(_write_cfg(tmp_path, _PROJECT_WITH_TOPIC))
    topic = cfg.bindings[0].digest.topic
    assert topic.labels == () and topic.paths == ()
    assert topic.has_signals is False


def test_digest_topic_labels_must_be_a_list(tmp_path):
    import pytest
    from babbla.config import load_config
    body = (
        "projects:\n  - name: MyTV\n    owner: o\n    repo: MyTV\n    visibility: public\n"
        "    channel_id: C1\n    dm: false\n"
        "    digest:\n      cadence: weekly\n      tz: UTC\n      anchor: branch\n"
        "      topic:\n        name: security\n        description: auth\n"
        "        labels: not-a-list\n"
    )
    with pytest.raises(ValueError, match="topic.labels must be a list of strings"):
        load_config(_write_cfg(tmp_path, body))
