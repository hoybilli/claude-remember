"""tests/conftest.py clears ambient Copilot host signals for every test
(issue: vscode).

A developer who runs `pytest` from a Copilot CLI or VS Code Agents shell tool
inherits COPILOT_CLI / COPILOT_PLUGIN_ROOT, which the hooks read to pick the
host hint; pre-existing Claude Code-path modules failed under it (demonstrated
on Linux in review). An autouse fixture cannot be overtaken from inside a test,
so the proof starts a child pytest with the signals exported and has a probe
test there report what it saw at import time and at run time.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRUBBED = ("COPILOT_CLI", "COPILOT_PLUGIN_ROOT", "REMEMBER_HOST_HINT")

# Captured at import, before any fixture runs: what the pytest process was
# started with.
_AT_IMPORT = {name: os.environ.get(name) for name in _SCRUBBED}
_EXPECT = "REMEMBER_TEST_EXPECT_AMBIENT_COPILOT"


def test_probe_copilot_signals_absent_inside_a_test():
    """Run directly, this checks the developer's own env; run by the case
    below, it is the probe."""
    if os.environ.get(_EXPECT) == "1":
        # Positive control: the process really was started with them.
        assert _AT_IMPORT == {"COPILOT_CLI": "1", "COPILOT_PLUGIN_ROOT": "/x",
                              "REMEMBER_HOST_HINT": "copilot"}, _AT_IMPORT
    for name in _SCRUBBED:
        assert name not in os.environ, name


def test_child_pytest_started_with_copilot_env_sees_none_of_it(tmp_path):
    env = dict(os.environ)
    env.update({"COPILOT_CLI": "1", "COPILOT_PLUGIN_ROOT": "/x",
                "REMEMBER_HOST_HINT": "copilot", _EXPECT: "1"})
    target = f"{Path(__file__).relative_to(REPO_ROOT).as_posix()}::test_probe_copilot_signals_absent_inside_a_test"
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=",
         "--basetemp", str(tmp_path / "child"), target],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 passed" in r.stdout, r.stdout
