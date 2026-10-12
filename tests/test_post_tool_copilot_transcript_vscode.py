"""remember_copilot_transcript_into in scripts/lib-session-id.sh: the
no-fork lookup of <COPILOT_HOME or ~/.copilot>/session-state/<uuid>/
events.jsonl that the Copilot wrappers and doctor.sh share (issue: vscode,
#511). The wrapper-level tests come back with scripts/copilot-post-tool-
hook.sh (adapter-layer plan, Task 5).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ._bash_runner import decode_bash_output, resolve_bash
from ._vscode_helpers import UUID

REPO_ROOT = Path(__file__).resolve().parent.parent
BASH = resolve_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="no POSIX bash found (Git Bash on Windows)")


# --- remember_copilot_transcript_into (lib-session-id.sh) -----------------

# The no-fork lookup the wrapper and doctor.sh use: it sets
# REMEMBER_COPILOT_TRANSCRIPT in the caller's shell and returns its status
# (issue: vscode, #511). The stale value proves a miss clears it.
_LOOKUP = ('REMEMBER_COPILOT_TRANSCRIPT=stale; '
           'remember_copilot_transcript_into "$2"; rc=$?; out=$REMEMBER_COPILOT_TRANSCRIPT')


def _layout(tmp_path):
    home = tmp_path / "home"
    d = home / ".copilot" / "session-state" / UUID
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text('{"type":"user.message","data":{"content":"hi"}}\n',
                                    encoding="utf-8")
    return home


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
    home = _layout(tmp_path)
    rc, out = _lib_call(tmp_path, UUID, home)
    assert rc == 0
    assert out.replace(chr(92), "/").endswith(f"session-state/{UUID}/events.jsonl")


@pytest.mark.parametrize("hostile", HOSTILE)
def test_copilot_transcript_for_refuses_hostile_ids(tmp_path, hostile):
    home = _layout(tmp_path)
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
    """Non-vacuous: with `[` shadowed to always succeed, the guard alone
    must reject the id (a mutated guard makes this fail)."""
    rc, out = _lib_call(tmp_path, hostile, any_file_exists=True)
    assert rc != 0
    assert out == ""


def test_shadowed_test_builtin_lets_a_plain_id_through(tmp_path):
    """Positive control for the shadowed-`[` negatives."""
    rc, out = _lib_call(tmp_path, UUID, any_file_exists=True)
    assert rc == 0 and UUID in out


def test_into_clears_a_stale_value_when_absent(tmp_path):
    """The lookup empties the global on a miss and returns non-zero."""
    rc, out = _lib_call(tmp_path, UUID)  # no session-state at all
    assert rc != 0 and out == ""
    # positive control: same call with the file present fills it
    home = _layout(tmp_path)
    rc, out = _lib_call(tmp_path, UUID, home)
    assert rc == 0 and out.endswith("events.jsonl")
