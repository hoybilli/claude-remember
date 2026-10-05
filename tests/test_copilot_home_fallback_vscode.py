"""scripts/lib-session-id.sh resolves the Copilot state directory exactly as
`${COPILOT_HOME:-${HOME:-}/.copilot}` did (issue: vscode; v0.40.0 merge,
review focus 2).

The release-tree checker refuses a nested default expansion, so that one
expansion became an explicit if/else. This module pins the four environments
the two forms must agree on: COPILOT_HOME set, COPILOT_HOME set but EMPTY (the
`:-` form falls back, so the if/else must test `-n`, not "is set"),
COPILOT_HOME unset with HOME set, and both unset.

The directory is `remember_copilot_state_dir_into`'s, and the observable
output that matters is what `remember_copilot_transcript_into` does with it:
the path it probes with `[ -f ... ]`. So the test shadows `[` with a shell
function that records the `-f` operand and then runs the builtin unchanged. A
real-file case below is the positive control that the recorded probe is the
path the function actually resolves and returns. doctor.sh resolves through
the same helper; one doctor run pins the empty-COPILOT_HOME fallback there.

The environment is set inside the bash script, not passed from Python: on
Windows the MSYS runtime fills in HOME for a process started without one, so
"HOME unset" is only reachable with `unset` in the shell itself.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ._bash_runner import decode_bash_output, resolve_bash
from ._vscode_helpers import UUID

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "scripts" / "lib-session-id.sh"
BASH = resolve_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="no POSIX bash found (Git Bash on Windows)")

# Records every `[ -f PATH ]` the library evaluates, then defers to the
# builtin so the function's own result is unchanged.
_PROBE = (
    '[() { if builtin [ "$1" = -f ]; then printf "probe=%s\\n" "$2"; fi; '
    'builtin [ "$@"; }\n'
)


def _probe(env_setup: str) -> list[str]:
    body = (f'. "{LIB.as_posix()}"\n{_PROBE}{env_setup}\n'
            f'remember_copilot_transcript_into "{UUID}"\n'
            'printf "rc=%s\\n" "$?"\n')
    env = {k: v for k, v in os.environ.items() if k != "COPILOT_HOME"}
    r = subprocess.run([BASH, "-c", body], capture_output=True, env=env, timeout=30)
    assert r.returncode == 0, decode_bash_output(r.stderr)
    return decode_bash_output(r.stdout).splitlines()


def _expected(base: str) -> list[str]:
    # No file exists under any of these bases, so the function returns 1
    # after its one probe.
    return [f"probe={base}/session-state/{UUID}/events.jsonl", "rc=1"]


@pytest.mark.parametrize("env_setup, base", [
    pytest.param("export COPILOT_HOME=/x/cp HOME=/x/h", "/x/cp", id="copilot-home-set"),
    pytest.param("export COPILOT_HOME= HOME=/x/h", "/x/h/.copilot", id="copilot-home-empty"),
    pytest.param("unset COPILOT_HOME; export HOME=/x/h", "/x/h/.copilot", id="copilot-home-unset"),
    pytest.param("unset COPILOT_HOME HOME", "/.copilot", id="both-unset"),
])
def test_copilot_state_dir_matches_the_nested_default(env_setup, base):
    """`remember_copilot_transcript_into` probes `<dir>/session-state/<uuid>/
    events.jsonl`, where <dir> is what `${COPILOT_HOME:-${HOME:-}/.copilot}`
    gives for the same environment."""
    assert _probe(env_setup) == _expected(base)


def test_probe_is_the_path_the_function_returns(tmp_path):
    """Positive control: with the file present, the probed path is exactly
    the one the function reports -- so the probe above observes the real
    resolution, not a side path."""
    cp = tmp_path / "cp"
    events = cp / "session-state" / UUID / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text("{}\n", encoding="utf-8")
    body = (f'. "{LIB.as_posix()}"\n{_PROBE}'
            f'export COPILOT_HOME="{cp.as_posix()}"\n'
            f'remember_copilot_transcript_into "{UUID}"\n'
            'printf "rc=%s\\nout=%s\\n" "$?" "$REMEMBER_COPILOT_TRANSCRIPT"\n')
    r = subprocess.run([BASH, "-c", body], capture_output=True, timeout=30)
    assert r.returncode == 0, decode_bash_output(r.stderr)
    lines = decode_bash_output(r.stdout).splitlines()
    want = f"{cp.as_posix()}/session-state/{UUID}/events.jsonl"
    assert lines == [f"probe={want}", "rc=0", f"out={want}"]


def test_doctor_falls_back_to_home_on_an_empty_copilot_home(tmp_path):
    """doctor.sh, COPILOT_HOME set but empty: the session-state dir it prints
    and the last save's transcript it finds are under HOME/.copilot, as the
    hooks' lookup gives. Positive control: the same run with the transcript
    under HOME/.copilot finds it (the Copilot line is printed), so the empty
    value did not simply disable the lookup."""
    from .test_doctor_copilot_only_project_vscode import (
        NEW_OK_TAIL, _new_ok_lines, _project, _run, _verdict)
    home, project, remember, _ = _project(tmp_path, claude_dir=False, copilot="home")
    out = _run(home, project, remember, {"COPILOT_HOME": ""})
    # Git Bash rewrites HOME to its own /tmp/... spelling, so the path is
    # matched from the sandbox directory down.
    state = f"/{tmp_path.name}/home/.copilot/session-state"
    dir_lines = [l for l in out.replace(chr(92), "/").splitlines()
                 if l.startswith("OK   copilot session-state dir present: ")]
    assert len(dir_lines) == 1, out
    assert dir_lines[0].endswith(
        f"{state} (VS Code Agents transcripts resolve from here -- issue: vscode)"), out
    lines = _new_ok_lines(out)
    assert len(lines) == 1 and lines[0].endswith(NEW_OK_TAIL), out
    assert f"{state}/{UUID}/events.jsonl" in lines[0].replace(chr(92), "/"), out
    assert _verdict(out).startswith("VERDICT: capture is working"), out
