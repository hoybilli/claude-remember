"""A VS Code Agents / Copilot transcript under REMEMBER_SUMMARIZER=auto resolves
"claude" (no Copilot-native summarizer) and says so (issue: vscode; #567 precedent)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.haiku import _choose_summarizer_provider

FIXTURES = Path(__file__).parent / "fixtures"
COPILOT = FIXTURES / "vscode-events.jsonl"
CLAUDE = FIXTURES / "sample-session.jsonl"


def _clear(monkeypatch):
    for var in ("CODEX_HOME", "PLUGIN_ROOT", "CODEX_SESSION_ID", "CODEX_THREAD_ID",
                "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PLUGIN_ROOT",
                "REMEMBER_SUMMARIZER", "REMEMBER_SUMMARIZER_FALLBACK",
                "REMEMBER_TRANSCRIPT_PATH", "REMEMBER_DIR"):
        monkeypatch.delenv(var, raising=False)


def test_copilot_transcript_warns_then_falls_back_to_claude(monkeypatch, capsys):
    _clear(monkeypatch)
    monkeypatch.setenv("REMEMBER_TRANSCRIPT_PATH", str(COPILOT))
    assert _choose_summarizer_provider() == "claude"
    err = capsys.readouterr().err.lower()
    assert "copilot" in err and "claude" in err


def test_claude_transcript_stays_silent(monkeypatch, capsys):
    """Positive control's must-not-fire half."""
    _clear(monkeypatch)
    monkeypatch.setenv("REMEMBER_TRANSCRIPT_PATH", str(CLAUDE))
    assert _choose_summarizer_provider() == "claude"
    assert "copilot" not in capsys.readouterr().err.lower()
