import os

import pytest

from babbla.audit.__main__ import main

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not os.environ.get("GITHUB_TOKEN"), reason="needs GITHUB_TOKEN")
def test_live_audit_of_mytv(capsys):
    code = main(["Wkkkkk/MyTV"])
    out = capsys.readouterr().out
    assert "Wkkkkk/MyTV" in out
    assert "Verdict:" in out
    assert "  - name: MyTV" in out
    assert code in (0, 1)   # public repo with docs should not error
