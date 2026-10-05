"""session-start-hook.sh no longer sources scripts/lib-session-id.sh (it kept
the compiled hook over v0.40.0's size budget). It inlines only the prefix
half -- strip `agent-host-*:/`, and export REMEMBER_HOST_HINT=copilot when the
prefix was there -- and leaves the environment half of the host rule to
pipeline.host.copilot_session(), run by the one Python call the hook makes
when its cheap trigger fires (issue: vscode).

This file proves that split changes nothing: for every case, the old
resolver (`remember_session_id_resolve`, still in the library, still used by
the post-tool and session-end hooks) and the new inline lines + Python rule
give the same normalized id and the same host hint, and the trigger fires
whenever the old hint was copilot. The inline lines are read out of the hook
itself, so the test follows the real code.

It also pins the Python rule's variable names against pipeline.host.REGISTRY,
and the "vanished transcript" receipt that moved from the hook into
pipeline/haiku.py.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from ._bash_runner import decode_bash_output, resolve_bash

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "scripts" / "session-start-hook.sh"
LIB = REPO_ROOT / "scripts" / "lib-session-id.sh"
BASH = resolve_bash()

sys.path.insert(0, str(REPO_ROOT))
from pipeline import host as _host  # noqa: E402
from pipeline.haiku import _choose_summarizer_provider  # noqa: E402

_SIGNATURES = ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID",
               "CODEX_THREAD_ID", "ANTIGRAVITY_CONVERSATION_ID", "COPILOT_CLI",
               "COPILOT_PLUGIN_ROOT")

_IDS = ("", ".", "..", "abc", "11111111-2222-4333-8444-555555555555",
        "agent-host-copilotcli:/11111111-2222-4333-8444-555555555555",
        "agent-host-:/x", "agent-host-x:/", "agent-host-x:/../../x", "agent-host-a:/b:/c",
        "agent-host-x", "agent-host-x:", "xagent-host-y:/z", "-n", "!", "=",
        "agent-host-*:/q", "a:/b", "agent-host-x:\\y", "agent-host-x/:/y", "AGENT-HOST-x:/y")

# Per variable: unset, set, or set to a single space (the shell's -n/-z count
# a space as set; pipeline.host.detect_host's strip() would not).
_VALUES = (None, "1")
_SPACE_CASES = ({"COPILOT_CLI": " "}, {"COPILOT_PLUGIN_ROOT": " "},
                {"COPILOT_CLI": "1", "CLAUDE_CODE_ENTRYPOINT": " "},
                {"COPILOT_CLI": "1", "CODEX_THREAD_ID": " "})


def _inline_lines() -> str:
    """The hook's inline prefix lines, from `export REMEMBER_HOST_HINT=""`
    through the strip."""
    text = HOOK.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index('export REMEMBER_HOST_HINT=""\n')
    end = text.index("CURRENT_SESSION_ID=${CURRENT_SESSION_ID#agent-host-*:/}\n", start)
    block = text[start:end] + "CURRENT_SESSION_ID=${CURRENT_SESSION_ID#agent-host-*:/}\n"
    assert block.count("\n") == 3, block  # positive control: the three lines
    return block


def _envs():
    for mask in range(2 ** len(_SIGNATURES)):
        yield {name: "1" for i, name in enumerate(_SIGNATURES) if mask >> i & 1}
    yield from _SPACE_CASES


_SCRIPT = r'''
. "$1"
shift
inline() {
%s
}
while IFS= read -r line; do
    for v in CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_SESSION_ID CODEX_SESSION_ID \
             CODEX_THREAD_ID ANTIGRAVITY_CONVERSATION_ID COPILOT_CLI COPILOT_PLUGIN_ROOT; do
        unset "$v"
    done
    if [ "$line" = "--" ]; then continue; fi
    eval "$line"
    for id in "$@"; do
        remember_session_id_resolve "$id"
        CURRENT_SESSION_ID=$id
        inline
        printf '%%s\t%%s\t%%s\t%%s\n' "$REMEMBER_SESSION_ID_NORMALIZED" "$REMEMBER_SESSION_ID_HINT" \
            "$CURRENT_SESSION_ID" "$REMEMBER_HOST_HINT"
    done
done
'''


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


@pytest.mark.skipif(BASH is None, reason="no POSIX bash found (Git Bash on Windows)")
def test_inline_strip_plus_python_rule_equals_the_old_resolver(monkeypatch):
    envs = list(_envs())
    stdin = "\n".join(
        "; ".join(f"export {k}={_sh_quote(v)}" for k, v in env.items()) or ":"
        for env in envs) + "\n"
    env = {k: v for k, v in os.environ.items()
           if k not in _SIGNATURES + ("REMEMBER_HOST_HINT",)}
    r = subprocess.run([BASH, "-c", _SCRIPT % _inline_lines(), "bash", LIB.as_posix(), *_IDS],
                       input=stdin.encode(), capture_output=True, env=env, timeout=600)
    assert r.returncode == 0, decode_bash_output(r.stderr)
    rows = decode_bash_output(r.stdout).replace("\r\n", "\n").split("\n")
    assert rows[-1] == ""
    rows = rows[:-1]
    assert len(rows) == len(envs) * len(_IDS)  # positive control: every case ran
    mismatches = []
    fired_for_copilot = 0
    for n, row in enumerate(rows):
        case_env, raw = envs[n // len(_IDS)], _IDS[n % len(_IDS)]
        old_id, old_hint, new_id, prefix_hint = row.split("\t")
        for name in _SIGNATURES:
            monkeypatch.delenv(name, raising=False)
        for name, value in case_env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("REMEMBER_HOST_HINT", prefix_hint)
        new_hint = "copilot" if _host.copilot_session() else ""
        trigger = bool(prefix_hint or case_env.get("COPILOT_CLI") or case_env.get("COPILOT_PLUGIN_ROOT"))
        if (old_id, old_hint) != (new_id, new_hint) or (old_hint and not trigger):
            mismatches.append((raw, case_env, (old_id, old_hint), (new_id, new_hint), trigger))
        fired_for_copilot += bool(old_hint)
    assert mismatches == []
    assert fired_for_copilot > 0  # positive control: copilot cases were in the set


def test_python_rule_reads_the_registry_signature_names():
    """The literal names copilot_session() reads are exactly the signature
    variables of COPILOT and of every host REGISTRY lists before it."""
    before = _host.REGISTRY[:_host.REGISTRY.index(_host.COPILOT)]
    expected = {v for h in before for v in h.signature_vars} | set(_host.COPILOT.signature_vars)
    assert expected == set(_SIGNATURES)
    source = Path(_host.__file__).read_text(encoding="utf-8")
    start = source.index("def copilot_session()")
    body = source[start:source.index("\ndef ", start + 1)]
    assert "return bool(" in body  # positive control: the whole function
    for name in expected:
        assert f'os.environ.get("{name}", "")' in body, name


# ── The vanished-transcript receipt, now decided in pipeline/haiku.py ──────

FIXTURES = Path(__file__).parent / "fixtures"
_MISSING = str(FIXTURES / "does-not-exist-vscode.jsonl")


def _clear(monkeypatch):
    for var in _SIGNATURES + ("REMEMBER_HOST_HINT", "CODEX_HOME", "PLUGIN_ROOT",
                              "CLAUDE_PLUGIN_ROOT", "REMEMBER_SUMMARIZER",
                              "REMEMBER_SUMMARIZER_FALLBACK", "REMEMBER_TRANSCRIPT_PATH"):
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize("copilot_env", [{"COPILOT_CLI": "1"},
                                         {"REMEMBER_HOST_HINT": "copilot"}])
def test_missing_transcript_is_not_a_receipt_on_the_copilot_host(monkeypatch, copilot_env):
    _clear(monkeypatch)
    monkeypatch.setenv("REMEMBER_TRANSCRIPT_PATH", _MISSING)
    for name, value in copilot_env.items():
        monkeypatch.setenv(name, value)
    with patch("pipeline.haiku._warn") as warn:
        assert _choose_summarizer_provider() == "claude"
    warn.assert_not_called()


@pytest.mark.parametrize("other_env", [{}, {"COPILOT_CLI": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"}])
def test_missing_transcript_is_still_a_receipt_elsewhere(monkeypatch, other_env):
    """Positive control (#477 unchanged): any other host, including one whose
    environment merely carries COPILOT_CLI beside its own signature."""
    _clear(monkeypatch)
    monkeypatch.setenv("REMEMBER_TRANSCRIPT_PATH", _MISSING)
    for name, value in other_env.items():
        monkeypatch.setenv(name, value)
    with patch("pipeline.haiku._warn") as warn:
        assert _choose_summarizer_provider() == "claude"
    warn.assert_called_once()
    assert "does-not-exist-vscode.jsonl" in warn.call_args[0][0]
