"""The Copilot host rule on the Python side (issue: vscode).

The shell half is gone: only the Copilot hosts load the wrappers
(hooks/hooks.copilot.json), and they export REMEMBER_HOST_HINT=copilot
(scripts/lib-copilot-adapter.sh). Python still applies
pipeline.host.copilot_session() -- the hint first, else the first REGISTRY
host whose signature is set -- for pipeline/copilot_recap.py and the
"vanished transcript" receipt in pipeline/haiku.py, both pinned here.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from ._vscode_helpers import HOST_ENV

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from pipeline import host as _host  # noqa: E402
from pipeline.haiku import _choose_summarizer_provider  # noqa: E402

# Written out, not read from REGISTRY, so a name dropped from the registry is
# caught here.
_SIGNATURES = ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID",
               "CODEX_THREAD_ID", "ANTIGRAVITY_CONVERSATION_ID", "COPILOT_CLI",
               "COPILOT_PLUGIN_ROOT")


def test_shell_signature_names_are_the_registry_names():
    """_SIGNATURES are exactly the signature variables of COPILOT and of
    every host REGISTRY lists before it -- the set copilot_session() walks."""
    before = _host.REGISTRY[:_host.REGISTRY.index(_host.COPILOT)]
    expected = {v for h in before for v in h.signature_vars} | set(_host.COPILOT.signature_vars)
    assert expected == set(_SIGNATURES)


# ── The vanished-transcript receipt, decided in pipeline/haiku.py ──────────

FIXTURES = Path(__file__).parent / "fixtures"
_MISSING = str(FIXTURES / "does-not-exist-vscode.jsonl")


def _clear(monkeypatch):
    for var in HOST_ENV + ("CODEX_HOME", "PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT",
                           "REMEMBER_SUMMARIZER", "REMEMBER_SUMMARIZER_FALLBACK",
                           "REMEMBER_TRANSCRIPT_PATH"):
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
