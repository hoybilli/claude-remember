"""session-start-hook.sh wraps its memory recap in the JSON envelope VS Code
Agents injects (top-level `additionalContext`) when the host is Copilot, and
stays plain text otherwise (issue: vscode).

Observed on VS Code 1.139.1 / Windows 11 only; macOS/Linux VS Code is reasoned.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ._bash_runner import decode_bash_output, resolve_bash

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "scripts" / "session-start-hook.sh"
BASH = resolve_bash()
pytestmark = pytest.mark.skipif(
    BASH is None or shutil.which("jq") is None, reason="needs a real bash and jq")

UUID = "56adc774-ffde-4486-86ec-babdef137ae5"

# The suite runs inside a Claude Code session: strip every host signal from the
# inherited env, then add back only what a case names.
_HOST_ENV = ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "COPILOT_CLI",
             "COPILOT_PLUGIN_ROOT", "REMEMBER_HOST_HINT")


def _run(tmp_path, session_id, extra_env=None, recent="PROBE-RECENT-LINE\n"):
    project = tmp_path / "proj"
    (project / ".remember" / "tmp").mkdir(parents=True)
    (project / ".remember" / "recent.md").write_text(recent, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in _HOST_ENV}
    env.update({"HOME": str(home), "USERPROFILE": str(home),
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "REMEMBER_DIR": str(project / ".remember"),
                "REMEMBER_PROMOS": "0"})
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.update(extra_env or {})
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": session_id,
                          "source": "startup", "cwd": str(project)})
    r = subprocess.run([BASH, HOOK.as_posix()], input=payload.encode(), env=env,
                       capture_output=True, timeout=180)
    assert r.returncode == 0, decode_bash_output(r.stderr)
    return decode_bash_output(r.stdout)


def _assert_envelope(out):
    obj = json.loads(out)
    assert "PROBE-RECENT-LINE" in obj["additionalContext"]
    assert "=== MEMORY ===" in obj["additionalContext"]
    assert "hookSpecificOutput" not in obj
    assert "systemMessage" not in obj
    return obj


def _assert_plain(out):
    assert "=== MEMORY ===" in out and "PROBE-RECENT-LINE" in out
    with pytest.raises(ValueError):
        json.loads(out)


def test_copilot_env_gets_top_level_additional_context(tmp_path):
    _assert_envelope(_run(tmp_path, UUID, {"COPILOT_CLI": "1"}))


def test_prefixed_session_id_gets_the_same_envelope(tmp_path):
    _assert_envelope(_run(tmp_path, f"agent-host-copilotcli:/{UUID}"))


def test_plain_uuid_keeps_plain_text(tmp_path):
    """Positive control: Claude Code's stdout shape is unchanged."""
    _assert_plain(_run(tmp_path, UUID))


def test_claude_code_signature_beats_copilot_env(tmp_path):
    _assert_plain(_run(tmp_path, UUID, {"COPILOT_CLI": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"}))


def test_non_ascii_recap_survives_the_envelope(tmp_path):
    out = _run(tmp_path, UUID, {"COPILOT_CLI": "1"},
               recent="PROBE-RECENT-LINE\nünï – 日本\n")
    assert "ünï – 日本" in _assert_envelope(out)["additionalContext"]
