"""scripts/lib-session-id.sh: VS Code Agents prefixes are stripped before the
core hooks' validators see the id (issue: vscode; review focus 3).

remember_session_id_resolve is strip-only: the hint it returns says whether
the prefix was there and nothing else. Which host is running is no longer a
shell question -- only the Copilot hosts load the wrappers that call it, and
they export REMEMBER_HOST_HINT=copilot themselves
(scripts/lib-copilot-adapter.sh).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ._bash_runner import decode_bash_output, resolve_bash
from ._vscode_helpers import HOST_ENV, UUID

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "scripts" / "lib-session-id.sh"
BASH = resolve_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="no POSIX bash found (Git Bash on Windows)")

# The exact validator every core hook applies, copied verbatim so the test
# proves the two layers agree rather than re-implementing either.
VALIDATE = """case "$id" in ''|.|..|-*|*[!A-Za-z0-9._-]*) id="" ;; esac"""

_HOST_ENV = HOST_ENV + ("COPILOT_HOME", "REMEMBER_SESSION_ID_NORMALIZED",
                        "REMEMBER_SESSION_ID_HINT")


def _bash(body: str, *args: str, extra_env: dict | None = None) -> str:
    """Source the library, run `body` with `args` as $1.., return stdout. The
    inherited host signals are stripped; only `extra_env` puts any back."""
    env = {k: v for k, v in os.environ.items() if k not in _HOST_ENV}
    env.update(extra_env or {})
    r = subprocess.run([BASH, "-c", f'. "{LIB.as_posix()}"\n{body}', "bash", *args],
                       capture_output=True, env=env, timeout=30)
    assert r.returncode == 0, decode_bash_output(r.stderr)
    return decode_bash_output(r.stdout)


def run(raw: str, extra_env: dict | None = None) -> tuple[str, str]:
    """`remember_session_id_resolve`, then the core hooks' own validator:
    returns (id, hint)."""
    out = _bash(
        'remember_session_id_resolve "$1"; id=$REMEMBER_SESSION_ID_NORMALIZED\n'
        f'{VALIDATE}\n'
        'printf "%s|%s" "$id" "$REMEMBER_SESSION_ID_HINT"\n',
        raw, extra_env=extra_env)
    return tuple(out.split("|", 1))


def test_vscode_prefix_is_stripped_and_hint_set():
    assert run(f"agent-host-copilotcli:/{UUID}") == (UUID, "copilot")


def test_plain_uuid_is_unchanged_and_no_hint():
    """Positive control: Claude Code ids pass through untouched."""
    assert run(UUID) == (UUID, "")


def test_traversal_after_prefix_is_rejected_downstream():
    """Review focus 3: normalisation must not launder a hostile tail."""
    assert run("agent-host-copilotcli:/../x")[0] == ""
    assert run("agent-host-copilotcli:/")[0] == ""


def test_other_colon_forms_are_left_for_the_validator():
    """A colon without the `:/` prefix shape is not ours to rewrite."""
    assert run("abc:def")[0] == ""


@pytest.mark.parametrize("env", [{"COPILOT_CLI": "1"}, {"COPILOT_PLUGIN_ROOT": "/x"},
                                 {"COPILOT_HOME": "/h/.copilot"}],
                         ids=["copilot-cli", "copilot-plugin-root", "copilot-home"])
def test_environment_does_not_set_the_hint(env):
    """Strip-only: a bare uuid stays hint-less whatever the environment
    carries. Positive control: the prefixed id under the same environment."""
    assert run(UUID, env) == (UUID, "")
    assert run(f"agent-host-copilotcli:/{UUID}", env) == (UUID, "copilot")


# ── The no-fork entry point itself (issue: vscode, #511) ──────────────────

def _resolve_twice(first: str, second: str) -> str:
    return _bash(
        'remember_session_id_resolve "$1"\n'
        'remember_session_id_resolve "$2"\n'
        'printf "%s|%s" "$REMEMBER_SESSION_ID_NORMALIZED" "$REMEMBER_SESSION_ID_HINT"\n',
        first, second)


def test_resolve_overwrites_both_globals_on_every_call():
    """A second call with a plain id must not keep the first call's hint."""
    assert _resolve_twice(f"agent-host-copilotcli:/{UUID}", "plain-id") == "plain-id|"
    # positive control: the reverse order ends on the prefixed id's values
    assert _resolve_twice("plain-id", f"agent-host-copilotcli:/{UUID}") == f"{UUID}|copilot"


def test_resolve_with_empty_raw_sets_empty_id():
    assert _resolve_twice("x", "") == "|"


def test_resolve_body_has_no_command_substitution():
    """The point of the entry point: no `$(` / backtick fork in its body."""
    text = LIB.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("remember_session_id_resolve() {")
    body = text[start:text.index("\n}\n", start)]
    assert "remember_session_id_resolve() {" in body  # positive control: found it
    assert "$(" not in body and "`" not in body
