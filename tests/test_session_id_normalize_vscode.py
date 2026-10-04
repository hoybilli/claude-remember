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

UUID = "11111111-2222-4333-8444-555555555555"

# The exact validator every hook already applies, copied verbatim so the test
# proves the two layers agree rather than re-implementing either.
VALIDATE = """case "$id" in ''|.|..|-*|*[!A-Za-z0-9._-]*) id="" ;; esac"""


sys.path.insert(0, str(REPO_ROOT))
from pipeline import host as _host  # noqa: E402

# Every host registered before COPILOT in pipeline.host.REGISTRY: detect_host
# returns the first host whose signature is set, so each of these must win
# over Copilot's signature in the shell hint too (read from the registry, not
# copied, so a new host row cannot silently drift).
_EARLIER_HOSTS = _host.REGISTRY[:_host.REGISTRY.index(_host.COPILOT)]
_EARLIER_SIGNATURES = tuple(v for h in _EARLIER_HOSTS for v in h.signature_vars)

# Signals the host hint reads. The suite itself runs inside a Claude Code
# session, so these must be stripped from the inherited env and only the ones a
# case names put back -- otherwise a case tests the developer's shell.
_HOST_ENV = tuple(dict.fromkeys(
    ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID",
     "CODEX_THREAD_ID", "ANTIGRAVITY_CONVERSATION_ID", "COPILOT_CLI",
     "COPILOT_PLUGIN_ROOT", "COPILOT_HOME", "REMEMBER_HOST_HINT",
     "REMEMBER_SESSION_ID_NORMALIZED", "REMEMBER_SESSION_ID_HINT")
    + _EARLIER_SIGNATURES + _host.COPILOT.signature_vars))

# Two entry points share one rule: the print helpers (run inside $(...)) and
# the no-fork `remember_session_id_resolve`, which sets two globals in the
# caller's shell (issue: vscode, #511). Every case below runs against both.
_CALLS = {
    "print": 'id=$(remember_normalize_session_id "$1"); '
             'REMEMBER_HOST_HINT=$(remember_session_id_host_hint "$1")',
    "resolve": 'remember_session_id_resolve "$1"; id=$REMEMBER_SESSION_ID_NORMALIZED; '
               'REMEMBER_HOST_HINT=$REMEMBER_SESSION_ID_HINT',
}


@pytest.fixture(params=sorted(_CALLS))
def mode(request):
    return request.param


def _run_mode(mode: str, raw: str, extra_env: dict | None = None) -> tuple[str, str]:
    script = (
        f'. "{LIB.as_posix()}"\n'
        f'{_CALLS[mode]}\n'
        f'{VALIDATE}\n'
        'printf "%s|%s" "$id" "${REMEMBER_HOST_HINT:-}"\n'
    )
    env = {k: v for k, v in os.environ.items() if k not in _HOST_ENV}
    env.update(extra_env or {})
    r = subprocess.run([BASH, "-c", script, "bash", raw], capture_output=True, env=env, timeout=30)
    out = decode_bash_output(r.stdout)
    assert r.returncode == 0, decode_bash_output(r.stderr)
    return tuple(out.split("|", 1))


@pytest.fixture
def run(mode):
    return lambda raw, extra_env=None: _run_mode(mode, raw, extra_env)


def test_vscode_prefix_is_stripped_and_hint_set(run):
    assert run(f"agent-host-copilotcli:/{UUID}") == (UUID, "copilot")


def test_plain_uuid_is_unchanged_and_no_hint(run):
    """Positive control: Claude Code ids pass through untouched."""
    assert run(UUID) == (UUID, "")


def test_traversal_after_prefix_is_rejected_downstream(run):
    """Review focus 3: normalisation must not launder a hostile tail."""
    assert run("agent-host-copilotcli:/../x")[0] == ""
    assert run("agent-host-copilotcli:/")[0] == ""


def test_other_colon_forms_are_left_for_the_validator(run):
    """A colon without the `:/` prefix shape is not ours to rewrite."""
    assert run("abc:def")[0] == ""


def test_bare_uuid_with_copilot_cli_env_is_copilot(run):
    assert run(UUID, {"COPILOT_CLI": "1"}) == (UUID, "copilot")


def test_bare_uuid_with_copilot_plugin_root_only_is_copilot(run):
    assert run(UUID, {"COPILOT_PLUGIN_ROOT": "/x"}) == (UUID, "copilot")


def test_claude_code_entrypoint_beats_copilot_env(run):
    """Mirrors pipeline.host.detect_host: Claude Code's signature wins."""
    assert run(UUID, {"COPILOT_CLI": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"}) == (UUID, "")


def test_claude_code_session_id_beats_copilot_env(run):
    assert run(UUID, {"COPILOT_CLI": "1", "CLAUDE_CODE_SESSION_ID": "abc"}) == (UUID, "")


def test_codex_thread_id_beats_copilot_env(run):
    """A Codex session whose env also carries COPILOT_CLI keeps its plain recap,
    as pipeline.host.detect_host says codex (CODEX precedes COPILOT)."""
    assert run(UUID, {"COPILOT_CLI": "1", "CODEX_THREAD_ID": "t-1"}) == (UUID, "")


def test_codex_session_id_beats_copilot_env(run):
    assert run(UUID, {"COPILOT_CLI": "1", "CODEX_SESSION_ID": "s-1"}) == (UUID, "")


def test_antigravity_conversation_id_beats_copilot_env(run):
    assert run(UUID, {"COPILOT_PLUGIN_ROOT": "/x",
                      "ANTIGRAVITY_CONVERSATION_ID": "c-1"}) == (UUID, "")


@pytest.mark.parametrize("earlier_var", _EARLIER_SIGNATURES)
@pytest.mark.parametrize("copilot_var", _host.COPILOT.signature_vars)
def test_hint_agrees_with_detect_host_for_every_earlier_signature(run, earlier_var, copilot_var):
    """Registry-derived parity: whatever detect_host says for this env, the
    shell hint says copilot exactly when detect_host says COPILOT."""
    env = {copilot_var: "1", earlier_var: "x"}
    assert _host.detect_host(env) is not _host.COPILOT
    assert run(UUID, env) == (UUID, "")
    # positive control: the same Copilot var alone is copilot on both sides
    assert _host.detect_host({copilot_var: "1"}) is _host.COPILOT
    assert run(UUID, {copilot_var: "1"}) == (UUID, "copilot")


def test_copilot_home_alone_is_not_a_signature(run):
    """A configuration path a user may set anywhere (#463)."""
    assert run(UUID, {"COPILOT_HOME": "/h/.copilot"}) == (UUID, "")


def test_prefixed_id_in_clean_env_is_copilot(run):
    """Positive control for the negatives above: the id route needs no env."""
    assert run(f"agent-host-copilotcli:/{UUID}") == (UUID, "copilot")


# ── The no-fork entry point itself (issue: vscode, #511) ──────────────────

def _resolve_twice(first: str, second: str, extra_env: dict | None = None) -> str:
    script = (
        f'. "{LIB.as_posix()}"\n'
        'remember_session_id_resolve "$1"\n'
        'remember_session_id_resolve "$2"\n'
        'printf "%s|%s" "$REMEMBER_SESSION_ID_NORMALIZED" "$REMEMBER_SESSION_ID_HINT"\n'
    )
    env = {k: v for k, v in os.environ.items() if k not in _HOST_ENV}
    env.update(extra_env or {})
    r = subprocess.run([BASH, "-c", script, "bash", first, second],
                       capture_output=True, env=env, timeout=30)
    assert r.returncode == 0, decode_bash_output(r.stderr)
    return decode_bash_output(r.stdout)


def test_resolve_overwrites_both_globals_on_every_call():
    """A second call with a plain id must not keep the first call's hint."""
    assert _resolve_twice(f"agent-host-copilotcli:/{UUID}", "plain-id") == "plain-id|"
    # positive control: the reverse order ends on the prefixed id's values
    assert _resolve_twice("plain-id", f"agent-host-copilotcli:/{UUID}") == f"{UUID}|copilot"


def test_resolve_with_empty_raw_sets_empty_id():
    assert _resolve_twice("x", "") == "|"
    assert _resolve_twice("x", "", {"COPILOT_CLI": "1"}) == "|copilot"


def test_resolve_body_has_no_command_substitution():
    """The point of the entry point: no `$(` / backtick fork in its body."""
    text = LIB.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("remember_session_id_resolve() {")
    body = text[start:text.index("\n}\n", start)]
    assert "remember_session_id_resolve() {" in body  # positive control: found it
    assert "$(" not in body and "`" not in body
