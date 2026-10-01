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


# Signals the host hint reads. The suite itself runs inside a Claude Code
# session, so these must be stripped from the inherited env and only the ones a
# case names put back -- otherwise a case tests the developer's shell.
_HOST_ENV = ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "COPILOT_CLI",
             "COPILOT_PLUGIN_ROOT", "COPILOT_HOME", "REMEMBER_HOST_HINT")


def _run(raw: str, extra_env: dict | None = None) -> tuple[str, str]:
    script = (
        f'. "{LIB.as_posix()}"\n'
        'id=$(remember_normalize_session_id "$1"); REMEMBER_HOST_HINT=$(remember_session_id_host_hint "$1")\n'
        f'{VALIDATE}\n'
        'printf "%s|%s" "$id" "${REMEMBER_HOST_HINT:-}"\n'
    )
    env = {k: v for k, v in os.environ.items() if k not in _HOST_ENV}
    env.update(extra_env or {})
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


def test_bare_uuid_with_copilot_cli_env_is_copilot():
    assert _run(UUID, {"COPILOT_CLI": "1"}) == (UUID, "copilot")


def test_bare_uuid_with_copilot_plugin_root_only_is_copilot():
    assert _run(UUID, {"COPILOT_PLUGIN_ROOT": "/x"}) == (UUID, "copilot")


def test_claude_code_entrypoint_beats_copilot_env():
    """Mirrors pipeline.host.detect_host: Claude Code's signature wins."""
    assert _run(UUID, {"COPILOT_CLI": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"}) == (UUID, "")


def test_claude_code_session_id_beats_copilot_env():
    assert _run(UUID, {"COPILOT_CLI": "1", "CLAUDE_CODE_SESSION_ID": "abc"}) == (UUID, "")


def test_copilot_home_alone_is_not_a_signature():
    """A configuration path a user may set anywhere (#463)."""
    assert _run(UUID, {"COPILOT_HOME": "/h/.copilot"}) == (UUID, "")


def test_prefixed_id_in_clean_env_is_copilot():
    """Positive control for the negatives above: the id route needs no env."""
    assert _run(f"agent-host-copilotcli:/{UUID}") == (UUID, "copilot")
