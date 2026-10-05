"""scripts/lib-session-id.sh resolves the Copilot state directory exactly as
`${COPILOT_HOME:-${HOME:-}/.copilot}` did (issue: vscode; v0.40.0 merge,
review focus 2).

The release-tree checker refuses a nested default expansion, so that one
expansion became an explicit if/else. This module pins the four environments
the two forms must agree on: COPILOT_HOME set, COPILOT_HOME set but EMPTY (the
`:-` form falls back, so the if/else must test `-n`, not "is set"),
COPILOT_HOME unset with HOME set, and both unset.

The library exposes no single point for the directory: it lives in a local
of `remember_copilot_transcript_into`, which consumes it only as the path it
probes with `[ -f ... ]`. So the observable output is that probe: the test
shadows `[` with a shell function that records the `-f` operand and then runs
the builtin unchanged. A real-file case below is the positive control that
the recorded probe is the path the function actually resolves and returns.

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

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "scripts" / "lib-session-id.sh"
BASH = resolve_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="no POSIX bash found (Git Bash on Windows)")

UUID = "11111111-2222-4333-8444-555555555555"

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
