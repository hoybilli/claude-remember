"""scripts/lib-copilot-adapter.sh: the payload edits the Copilot hook
wrappers make before they delegate to a core hook (issue: vscode).

Each function is sourced and called with `bash -c`, the way
tests/test_session_id_normalize_vscode.py drives lib-session-id.sh. Every
"left unchanged" case has a "changed" partner in this module, so an edit that
does nothing at all cannot pass.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ._bash_runner import decode_bash_output, resolve_bash
from ._vscode_helpers import UUID, posix

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
LIB = SCRIPTS / "lib-copilot-adapter.sh"
SESSION_LIB = SCRIPTS / "lib-session-id.sh"
BASH = resolve_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="no POSIX bash found (Git Bash on Windows)")

PREFIXED = f"agent-host-copilotcli:/{UUID}"


def _bash(body: str, *args: str, stdin: bytes = b"", env: dict | None = None):
    """Source both libraries, run BODY with ARGS as $1..; return the result.

    ARGS travel in the environment (REMEMBER_TEST_ARG_<n>), not on the
    command line: on Windows a double quote or a run of backslashes in an
    argv element does not reach Git Bash intact, and JSON is full of both."""
    env = dict(os.environ if env is None else env)
    names = []
    for n, value in enumerate(args, 1):
        env[f"REMEMBER_TEST_ARG_{n}"] = value
        names.append(f'"$REMEMBER_TEST_ARG_{n}"')
    code = (f'. "{posix(SESSION_LIB)}"\n. "{posix(LIB)}"\n'
            f'set -- {" ".join(names)}\n{body}')
    return subprocess.run([BASH, "-c", code], input=stdin,
                          capture_output=True, env=env, timeout=60)


def _out(body: str, *args: str, **kw) -> str:
    r = _bash(body, *args, **kw)
    assert r.returncode == 0, decode_bash_output(r.stderr)
    return decode_bash_output(r.stdout)


# --- remember_copilot_strip_session_id_into --------------------------------

def _strip(payload: str) -> str:
    return _out('remember_copilot_strip_session_id_into out "$1"; printf "%s" "$out"', payload)


def test_prefix_is_stripped_from_the_first_session_id():
    payload = f'{{"hook_event_name":"PostToolUse","session_id":"{PREFIXED}","cwd":"/p"}}'
    assert _strip(payload) == f'{{"hook_event_name":"PostToolUse","session_id":"{UUID}","cwd":"/p"}}'


def test_bare_uuid_payload_is_returned_byte_for_byte():
    """Must-not-change half of the pair above."""
    payload = f'{{"session_id" : "{UUID}", "x": "agent-host-a:/b"}}'
    assert _strip(payload) == payload


def test_only_the_first_session_id_is_rewritten():
    """The core hooks read the first occurrence; a later one (inside
    tool_input, say) is data and stays as it was."""
    payload = (f'{{"session_id":"{PREFIXED}",'
               f'"tool_input":{{"session_id":"agent-host-x:/other"}}}}')
    assert _strip(payload) == (f'{{"session_id":"{UUID}",'
                               f'"tool_input":{{"session_id":"agent-host-x:/other"}}}}')


def test_traversal_tail_is_left_for_the_core_validator():
    """`agent-host-x:/../../x` strips to `../../x`: the library does not
    judge it -- the core hook's own allowlist empties it (pinned end to end
    in the wrapper test modules)."""
    assert _strip('{"session_id":"agent-host-x:/../../x"}') == '{"session_id":"../../x"}'


def test_payload_without_the_key_is_unchanged():
    payload = '{"hook_event_name":"SessionStart","cwd":"agent-host-a:/b"}'
    assert _strip(payload) == payload


def test_non_string_session_id_is_unchanged():
    payload = '{"session_id": 7, "x": "agent-host-a:/b"}'
    assert _strip(payload) == payload


# --- remember_copilot_session_id_into / remember_copilot_field_into ---------

def test_session_id_into_reads_the_first_value():
    out = _out('remember_copilot_session_id_into v "$1"; rc=$?; printf "%s|%s" "$rc" "$v"',
               f'{{"session_id":"{UUID}","tool_input":{{"session_id":"later"}}}}')
    assert out == f"0|{UUID}"


def test_session_id_into_returns_1_and_empties_when_absent():
    out = _out('v=stale; remember_copilot_session_id_into v "$1"; rc=$?; printf "%s|%s" "$rc" "$v"',
               '{"cwd":"/p"}')
    assert out == "1|"


def test_field_into_unescapes_json_backslashes():
    out = _out('remember_copilot_field_into v cwd "$1"; printf "%s" "$v"',
               '{"cwd":"C:\\\\Users\\\\a b"}')
    assert out == "C:\\Users\\a b"


def test_field_into_refuses_a_key_followed_by_other_text():
    """`"reason": 1, "x": "complete"` is not a string value of reason."""
    out = _out('remember_copilot_field_into v reason "$1"; rc=$?; printf "%s|%s" "$rc" "$v"',
               '{"reason": 1, "x": "complete"}')
    assert out == "1|"
    # positive control: the same key with a string value is read
    assert _out('remember_copilot_field_into v reason "$1"; printf "%s" "$v"',
                '{"reason" : "complete"}') == "complete"


# --- remember_copilot_prepend_field_into ------------------------------------

def _prepend(payload: str, field: str, value: str) -> tuple[str, str]:
    out = _out('remember_copilot_prepend_field_into v "$1" "$2" "$3"; rc=$?; printf "%s|%s" "$rc" "$v"',
               payload, field, value)
    rc, _, text = out.partition("|")
    return rc, text


def test_prepend_puts_the_field_first():
    rc, text = _prepend('{"session_id":"s","tool_input":{"transcript_path":"decoy"}}',
                        "transcript_path", "/t/events.jsonl")
    assert rc == "0"
    assert text == ('{"transcript_path":"/t/events.jsonl","session_id":"s",'
                    '"tool_input":{"transcript_path":"decoy"}}')


def test_prepend_into_an_empty_object_adds_no_comma():
    assert _prepend(" { } ", "k", "v") == ("0", ' {"k":"v" } ')


def test_prepend_refuses_a_payload_that_is_not_an_object():
    """Must-not-change partner of test_prepend_puts_the_field_first."""
    assert _prepend("not json {}", "k", "v") == ("1", "not json {}")
    assert _prepend("", "k", "v") == ("1", "")


# --- remember_copilot_json_escape_path_into ---------------------------------

def test_escape_doubles_every_backslash():
    out = _out('remember_copilot_json_escape_path_into v "$1"; rc=$?; printf "%s|%s" "$rc" "$v"',
               "C:\\Users\\a b\\\\srv")
    assert out == "0|C:\\\\Users\\\\a b\\\\\\\\srv"


def test_escape_leaves_a_forward_slash_path_alone():
    out = _out('remember_copilot_json_escape_path_into v "$1"; printf "%s" "$v"', "/h/.copilot/x")
    assert out == "/h/.copilot/x"


def test_escape_refuses_a_double_quote():
    out = _out('v=stale; remember_copilot_json_escape_path_into v "$1"; rc=$?; printf "%s|%s" "$rc" "$v"',
               '/h/a"b')
    assert out == "1|"


def test_escaped_path_round_trips_through_the_core_reader():
    """What the wrapper writes is what the core hook reads back: the core's
    `_stdin_json_string` turns each `\\\\` into `\\` (#829), the library's
    remember_copilot_field_into does the same."""
    path = "C:\\Users\\x\\\\y"
    out = _out('remember_copilot_json_escape_path_into e "$1"\n'
               'remember_copilot_prepend_field_into p "{}" transcript_path "$e"\n'
               'remember_copilot_field_into back transcript_path "$p"\n'
               'printf "%s" "$back"', path)
    assert out == path


# --- remember_copilot_read_stdin_into ---------------------------------------

def test_read_stdin_takes_every_byte():
    payload = '{"a":"ünï – 日本"}\n{"b":1}'
    out = _out('remember_copilot_read_stdin_into v; printf "%s" "$v"', stdin=payload.encode("utf-8"))
    assert out == payload


# --- remember_copilot_delegate ----------------------------------------------

def _stub(tmp_path: Path, status: int) -> Path:
    stub = tmp_path / "core.sh"
    stub.write_bytes(("#!/bin/bash\n"
                      f'cat > "{posix(tmp_path)}/seen.bin"\n'
                      f"exit {status}\n").encode())
    return stub


@pytest.mark.parametrize("status", [0, 3])
def test_delegate_forwards_the_payload_and_the_exit_code(tmp_path, status):
    """0 is the positive control for 3: a delegate that always returned 0
    would pass the first and fail the second."""
    payload = '{"session_id":"x","p":"ünï"}'
    r = _bash('remember_copilot_delegate "$1" "$2"', posix(_stub(tmp_path, status)), payload)
    assert r.returncode == status, decode_bash_output(r.stderr)
    assert (tmp_path / "seen.bin").read_bytes() == payload.encode("utf-8")


# --- remember_copilot_adapter_init ------------------------------------------

def test_init_exports_the_root_and_the_hint(tmp_path):
    project = tmp_path / "proj"
    (project / ".remember").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items()
           if k not in ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "REMEMBER_HOST_HINT", "REMEMBER_DIR")}
    env.update({"CLAUDE_PROJECT_DIR": posix(project), "HOME": posix(tmp_path),
                "USERPROFILE": str(tmp_path), "PLUGIN_ROOT": "C:\\elsewhere\\plugin"})
    out = _out(f'remember_copilot_adapter_init "{posix(SCRIPTS)}/copilot-x-hook.sh"; rc=$?\n'
               'printf "%s|%s|%s|%s|%s|%s" "$rc" "$_COPILOT_ADAPTER_ROOT" "$PLUGIN_ROOT" '
               '"$CLAUDE_PLUGIN_ROOT" "$REMEMBER_HOST_HINT" "$_COPILOT_ADAPTER_PATHS"', env=env)
    rc, root, plugin_root, claude_root, hint, paths = out.split("|")
    assert rc == "0"
    assert root.rstrip("/").endswith(REPO_ROOT.name)
    # The host's backslash PLUGIN_ROOT does not survive: resolve-paths.sh
    # prefers PLUGIN_ROOT, so the wrapper's own root must replace it.
    assert plugin_root == root
    assert claude_root.replace("\\", "/").rstrip("/").endswith(REPO_ROOT.name)
    assert hint == "copilot"
    assert paths == "1"


def test_init_without_a_project_still_succeeds_with_paths_off(tmp_path):
    """resolve-paths.sh cannot resolve (no CLAUDE_PROJECT_DIR, no cwd): init
    still returns 0 so the wrapper delegates and the core decides. Positive
    control: the test above, where the same call resolves (paths=1)."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR",
                        "REMEMBER_HOOK_CWD", "REMEMBER_DIR")}
    out = _out(f'remember_copilot_adapter_init "{posix(SCRIPTS)}/copilot-x-hook.sh"; rc=$?\n'
               'log hook probe; printf "%s|%s" "$rc" "$_COPILOT_ADAPTER_PATHS"', env=env)
    assert out == "0|0"


def test_init_refuses_a_directory_without_lib_session_id(tmp_path):
    (tmp_path / "scripts").mkdir()
    out = _out(f'remember_copilot_adapter_init "{posix(tmp_path)}/scripts/copilot-x-hook.sh"; '
               'printf "%s" "$?"')
    assert out == "1"


# --- Style: the release-tree checker's shell rules, at authoring time -------

sys.path.insert(0, str(REPO_ROOT / ".github" / "scripts"))
from check_release_tree import CASE_STATEMENT, _NETWORK_WORD_STANDALONE  # noqa: E402

_COPILOT_SHELL = sorted(SCRIPTS.glob("copilot-*-hook.sh")) + [LIB]


def _code_lines(path: Path):
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.lstrip().startswith("#"):
            yield n, line


@pytest.mark.parametrize("path", _COPILOT_SHELL, ids=lambda p: p.name)
def test_no_case_statement_and_no_network_word_in_code_lines(path):
    """The wrappers are not on NETWORK_WORD_ALLOWLIST and must not need to
    be; comments are stripped at build time, code lines are what ships."""
    hits = [(n, line) for n, line in _code_lines(path)
            if CASE_STATEMENT.search(line) or _NETWORK_WORD_STANDALONE.search(line)
            or re.search(r"https?://", line)]
    assert hits == []


def test_the_style_scan_sees_a_violation_when_there_is_one():
    """Positive control for the scan above: the same patterns fire on lines
    that carry the shapes."""
    assert CASE_STATEMENT.search('case "$x" in')
    assert _NETWORK_WORD_STANDALONE.search('log hook "agent-host seen"')
    assert _COPILOT_SHELL, "no wrapper scripts found -- glob drift?"
