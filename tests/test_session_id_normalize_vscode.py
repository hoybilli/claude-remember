"""scripts/lib-session-id.sh: VS Code Agents prefixes are stripped before the
hooks' existing validators see the id (issue: vscode; review focus 3)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ._bash_runner import decode_bash_output, resolve_bash

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "scripts" / "lib-session-id.sh"
BASH = resolve_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="no POSIX bash found (Git Bash on Windows)")

UUID = "56adc774-ffde-4486-86ec-babdef137ae5"

# The exact validator every hook already applies, copied verbatim so the test
# proves the two layers agree rather than re-implementing either.
VALIDATE = """case "$id" in ''|.|..|-*|*[!A-Za-z0-9._-]*) id="" ;; esac"""


def _run(raw: str) -> tuple[str, str]:
    script = (
        f'. "{LIB.as_posix()}"\n'
        'id=$(remember_normalize_session_id "$1"); REMEMBER_HOST_HINT=$(remember_session_id_host_hint "$1")\n'
        f'{VALIDATE}\n'
        'printf "%s|%s" "$id" "${REMEMBER_HOST_HINT:-}"\n'
    )
    env = {k: v for k, v in os.environ.items() if k != "REMEMBER_HOST_HINT"}
    r = subprocess.run([BASH, "-c", script, "bash", raw], capture_output=True, env=env, timeout=30)
    out = decode_bash_output(r.stdout)
    assert r.returncode == 0, decode_bash_output(r.stderr)
    return tuple(out.split("|", 1))


def test_vscode_prefix_is_stripped_and_hint_set():
    assert _run(f"agent-host-copilotcli:/{UUID}") == (UUID, "copilot")


def test_plain_uuid_is_unchanged_and_no_hint():
    """Positive control: Claude Code ids pass through untouched."""
    assert _run(UUID) == (UUID, "")


def test_traversal_after_prefix_is_rejected_downstream():
    """Review focus 3: normalisation must not launder a hostile tail."""
    assert _run("agent-host-copilotcli:/../x")[0] == ""
    assert _run("agent-host-copilotcli:/")[0] == ""


def test_other_colon_forms_are_left_for_the_validator():
    """A colon without the `:/` prefix shape is not ours to rewrite."""
    assert _run("abc:def")[0] == ""
