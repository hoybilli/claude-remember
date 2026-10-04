"""detect_host() on a real VS Code Agents hook environment (issue: vscode)."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline import host as _host

from .test_codex_signature_463 import _load_env

FIXTURES = Path(__file__).parent / "fixtures"
ENV = FIXTURES / "vscode-env-vscode.txt"
STDIN = FIXTURES / "vscode-hook-stdin-vscode.json"

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "SessionEnd")


def _env():
    return _load_env(ENV)


def test_copilot_host_is_in_registry_with_plugin_root():
    assert _host.COPILOT in _host.REGISTRY
    assert _host.COPILOT.plugin_root(_env()) is not None


def test_project_dir_resolves_from_fixture():
    assert _host.COPILOT.project_dir(_env()) is not None


def test_detect_host_on_captured_env():
    assert _host.detect_host(_env()) is _host.COPILOT


def test_ambient_claude_code_var_does_not_steal_detection():
    # Positive control: the trap really is in the fixture.
    assert "CLAUDE_CODE_DISABLE_PRECOMPACT_SKIP" in _env()
    assert _host.detect_host(_env()) is _host.COPILOT


def test_claude_code_session_with_copilot_home_is_still_claude_code():
    # A user-set COPILOT_HOME is a configuration path, not a signature (#463).
    got = _host.detect_host({"CLAUDE_CODE_SESSION_ID": "x", "COPILOT_HOME": "/h/.copilot"})
    assert got is _host.CLAUDE_CODE
    assert "COPILOT_HOME" not in _host.COPILOT.signature_vars


def test_claude_and_codex_fixtures_unchanged():
    """Control: Claude Code and Codex detection is unchanged by the new row.

    The Codex capture is a nested session (launched from inside Claude Code),
    so it carries CLAUDE_CODE_ENTRYPOINT/SESSION_ID and Claude Code wins by
    design (#463). The inherited CLAUDE_CODE_* names are stripped so the test
    asks the Codex question on its own.
    """
    codex = {
        k: v
        for k, v in _load_env(FIXTURES / "codex-env-463.txt").items()
        if not k.startswith("CLAUDE_CODE_")
    }
    assert _host.detect_host(codex) is _host.CODEX
    assert _host.detect_host({"CLAUDE_CODE_SESSION_ID": "x"}) is _host.CLAUDE_CODE


def test_stdin_fixture_has_bare_uuid_session_id_and_cwd():
    payloads = json.loads(STDIN.read_text(encoding="utf-8"))
    assert set(_EVENTS) <= set(payloads)
    for ev in _EVENTS:
        obj = payloads[ev]
        assert _UUID.fullmatch(obj["session_id"]), ev
        assert obj["cwd"], ev
        assert "transcript_path" not in obj, ev
