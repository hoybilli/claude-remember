"""post-tool-hook.sh finds a VS Code Agents transcript under
~/.copilot/session-state/<uuid>/events.jsonl when stdin names the prefixed
id and no Claude Code session dir exists (issue: vscode)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from ._bash_runner import decode_bash_output, resolve_bash

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "scripts" / "post-tool-hook.sh"
BASH = resolve_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="no POSIX bash found (Git Bash on Windows)")

UUID = "56adc774-ffde-4486-86ec-babdef137ae5"


def _layout(tmp_path, with_copilot: bool):
    home = tmp_path / "home"
    project = tmp_path / "proj"
    remember = project / ".remember"
    for d in (home, project, remember / "tmp", remember / "logs"):
        d.mkdir(parents=True, exist_ok=True)
    if with_copilot:
        d = home / ".copilot" / "session-state" / UUID
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text(
            '{"type":"user.message","data":{"content":"hi"}}\n' * 3, encoding="utf-8")
    return home, project, remember


def _run(home, project, remember):
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home),
           "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT), "REMEMBER_DIR": str(remember),
           "REMEMBER_DEBUG": "1"}
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("REMEMBER_TRANSCRIPT_PATH", None)
    env.pop("COPILOT_HOME", None)
    payload = json.dumps({"hook_event_name": "PostToolUse",
                          "session_id": f"agent-host-copilotcli:/{UUID}",
                          "cwd": str(project), "tool_name": "bash", "tool_input": {}})
    r = subprocess.run([BASH, HOOK.as_posix()], input=payload.encode(), env=env,
                       capture_output=True, timeout=120)
    assert r.returncode == 0, decode_bash_output(r.stderr)
    logs = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in (remember / "logs").glob("*.log"))
    return logs


def test_copilot_transcript_is_resolved(tmp_path):
    home, project, remember = _layout(tmp_path, with_copilot=True)
    logs = _run(home, project, remember)
    assert f"session-state/{UUID}/events.jsonl" in logs.replace("\\", "/")
    assert "falling back to newest" not in logs
    assert "no session dir for this project" not in logs


def test_without_copilot_transcript_old_warning_still_fires(tmp_path):
    """Positive control for the negatives above: same payload, no file -> the
    existing 'no session dir' notice is what the user sees."""
    home, project, remember = _layout(tmp_path, with_copilot=False)
    logs = _run(home, project, remember)
    assert "no session dir for this project" in logs
