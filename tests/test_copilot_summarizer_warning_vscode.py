"""The summarizer provider for a Copilot transcript is "claude", silently
(issue: vscode).

scripts/copilot-post-tool-hook.sh hands the core hook the Copilot
transcript as `transcript_path`, so the core exports REMEMBER_TRANSCRIPT_PATH
on every save and _choose_summarizer_provider sniffs a Copilot envelope each
time. There is no Copilot-native summarizer: "claude" is the answer, and it
is today's observable behaviour (the live saves logged `provider: claude`
and no warning). A warning on every save would be noise, so there is none
(spec 4.3 ruling). The Antigravity arm keeps its #567 receipt: that is the
positive control showing a non-Claude envelope still takes its own branch.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.haiku import _choose_summarizer_provider

FIXTURES = Path(__file__).parent / "fixtures"
COPILOT = FIXTURES / "vscode-events.jsonl"
ANTIGRAVITY = FIXTURES / "antigravity-transcript-563.jsonl"
CODEX = FIXTURES / "codex-rollout.jsonl"


def _clear(monkeypatch):
    for var in ("CODEX_HOME", "PLUGIN_ROOT", "CODEX_SESSION_ID", "CODEX_THREAD_ID",
                "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PLUGIN_ROOT",
                "REMEMBER_SUMMARIZER", "REMEMBER_SUMMARIZER_FALLBACK",
                "REMEMBER_TRANSCRIPT_PATH", "REMEMBER_DIR"):
        monkeypatch.delenv(var, raising=False)


def test_copilot_transcript_falls_back_to_claude_silently(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("REMEMBER_TRANSCRIPT_PATH", str(COPILOT))
    with patch("pipeline.haiku._warn") as warn:
        assert _choose_summarizer_provider() == "claude"
    warn.assert_not_called()


def test_antigravity_transcript_still_warns(monkeypatch):
    """Positive control: the same call on another non-Claude envelope takes
    its own branch and does warn, so the silence above is the Copilot arm's,
    not a sniff that never ran."""
    _clear(monkeypatch)
    monkeypatch.setenv("REMEMBER_TRANSCRIPT_PATH", str(ANTIGRAVITY))
    with patch("pipeline.haiku._warn") as warn:
        assert _choose_summarizer_provider() == "claude"
    warn.assert_called_once()
    assert "Antigravity" in warn.call_args[0][0]


def test_codex_transcript_is_still_routed_to_codex(monkeypatch):
    """Second control: the sniff really reads the file it is given."""
    _clear(monkeypatch)
    monkeypatch.setenv("REMEMBER_TRANSCRIPT_PATH", str(CODEX))
    assert _choose_summarizer_provider() == "codex"
