"""Pins the summarizer provider choice for a SUPPLIED Copilot transcript path:
under REMEMBER_SUMMARIZER=auto, a VS Code Agents / Copilot transcript in
REMEMBER_TRANSCRIPT_PATH resolves "claude" (there is no Copilot-native
summarizer) and logs the fallback (issue: vscode; #567 precedent).

The live host does not supply one: VS Code sends no `transcript_path` and no
hook exports the resolved Copilot file, so on the live path the provider is
still "claude" but this warning is not logged (observed in the live run's
save log: `provider: claude`, no warning). These tests set the variable by
hand to exercise the branch."""
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
