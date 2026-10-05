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
from ._vscode_helpers import UUID, created_outside, hook_logs, tree

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "scripts" / "post-tool-hook.sh"
BASH = resolve_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="no POSIX bash found (Git Bash on Windows)")


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


def _run(home, project, remember, session_id=f"agent-host-copilotcli:/{UUID}"):
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home),
           "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT), "REMEMBER_DIR": str(remember),
           "REMEMBER_DEBUG": "1"}
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("REMEMBER_TRANSCRIPT_PATH", None)
    env.pop("COPILOT_HOME", None)
    payload = json.dumps({"hook_event_name": "PostToolUse",
                          "session_id": session_id,
                          "cwd": str(project), "tool_name": "bash", "tool_input": {}})
    r = subprocess.run([BASH, HOOK.as_posix()], input=payload.encode(), env=env,
                       capture_output=True, timeout=120)
    assert r.returncode == 0, decode_bash_output(r.stderr)
    return hook_logs(remember / "logs")


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


def test_traversal_after_the_prefix_is_rejected_by_the_hook(tmp_path):
    """`agent-host-x:/../../x` strips to `../../x`, which the hook's own
    validator must empty before the Copilot lookup builds a path from it. A
    decoy events file sits exactly where that path would land (`<home>/x/`),
    so a missing validator resolves it rather than failing vacuously. The
    hook exits 0, gives the no-transcript notice the positive control above
    gives, and writes nothing outside the sandbox store."""
    home, project, remember = _layout(tmp_path, with_copilot=True)
    decoy = home / "x"
    decoy.mkdir()
    (decoy / "events.jsonl").write_text(
        '{"type":"user.message","data":{"content":"hi"}}\n', encoding="utf-8")
    before = tree(tmp_path)
    logs = _run(home, project, remember, session_id="agent-host-x:/../../x")
    assert "no session dir for this project" in logs
    assert "x/events.jsonl" not in logs.replace("\\", "/")
    outside = created_outside(tmp_path, before, ("proj/.remember/",))
    assert outside == [], outside


# The no-fork lookup post-tool-hook.sh and doctor.sh use: it sets
# REMEMBER_COPILOT_TRANSCRIPT in the caller's shell and returns its status
# (issue: vscode, #511). The stale value proves a miss clears it.
_LOOKUP = ('REMEMBER_COPILOT_TRANSCRIPT=stale; '
           'remember_copilot_transcript_into "$2"; rc=$?; out=$REMEMBER_COPILOT_TRANSCRIPT')


def _lib_call(tmp_path, arg, home=None, any_file_exists=False):
    home = home or (tmp_path / "home")
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    env.pop("COPILOT_HOME", None)
    env.pop("REMEMBER_COPILOT_TRANSCRIPT", None)
    lib = (REPO_ROOT / "scripts" / "lib-session-id.sh").as_posix()
    # any_file_exists shadows `[` so every -f test succeeds: only the id guard can
    # refuse then (a dir named "a\b" cannot be created on Windows filesystems).
    shadow = '[() { return 0; }; ' if any_file_exists else ''
    code = 'source "$1"; ' + shadow + _LOOKUP + '; printf "%s|%s" "$rc" "$out"'
    r = subprocess.run([BASH, "-c", code, "x", lib, arg], env=env, capture_output=True, timeout=60)
    rc, _, out = decode_bash_output(r.stdout).partition("|")
    return int(rc), out


HOSTILE = ["", ".", "..", "../x", "a/b", "a" + chr(92) + "b", ".." + chr(92) + ".." + chr(92) + "x", "a:b"]


def test_copilot_transcript_for_positive_control(tmp_path):
    home, _project, _remember = _layout(tmp_path, with_copilot=True)
    rc, out = _lib_call(tmp_path, UUID, home)
    assert rc == 0
    assert out.replace(chr(92), "/").endswith(f"session-state/{UUID}/events.jsonl")


@pytest.mark.parametrize("hostile", HOSTILE)
def test_copilot_transcript_for_refuses_hostile_ids(tmp_path, hostile):
    home, _project, _remember = _layout(tmp_path, with_copilot=True)
    # Decoys so a missing guard would resolve a real file rather than fail vacuously.
    ss = home / ".copilot" / "session-state"
    (ss / "events.jsonl").write_text("{}", encoding="utf-8")
    (ss.parent / "x").mkdir()
    (ss.parent / "x" / "events.jsonl").write_text("{}", encoding="utf-8")  # "../x" target
    (ss / "a").mkdir()
    (ss / "a" / "events.jsonl").write_text("{}", encoding="utf-8")  # "a/b" neighbour
    rc, out = _lib_call(tmp_path, hostile, home)
    assert rc != 0
    assert out == ""


@pytest.mark.parametrize("hostile", HOSTILE)
def test_copilot_transcript_for_guard_refuses_even_if_file_exists(tmp_path, hostile):
    """Non-vacuous: with `[` shadowed to always succeed, the case guard alone
    must reject the id (a mutated guard makes this fail)."""
    rc, out = _lib_call(tmp_path, hostile, any_file_exists=True)
    assert rc != 0
    assert out == ""


def test_shadowed_test_builtin_lets_a_plain_id_through(tmp_path):
    """Positive control for the shadowed-`[` negatives."""
    rc, out = _lib_call(tmp_path, UUID, any_file_exists=True)
    assert rc == 0 and UUID in out


def test_into_clears_a_stale_value_when_absent(tmp_path):
    """The lookup empties the global on a miss (the hook keeps no previous
    call's path), and returns non-zero."""
    rc, out = _lib_call(tmp_path, UUID)  # no session-state at all
    assert rc != 0 and out == ""
    # positive control: same call with the file present fills it
    home, _project, _remember = _layout(tmp_path, with_copilot=True)
    rc, out = _lib_call(tmp_path, UUID, home)
    assert rc == 0 and out.endswith("events.jsonl")
