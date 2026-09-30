"""VS Code Agents (Copilot harness) transcript envelope: sniff (issue: vscode).

The Copilot harness embedded in VS Code writes its transcript to
~/.copilot/session-state/<uuid>/events.jsonl, one object per line shaped
{"type": "<dotted.name>", "data": {...}, "id", "timestamp", "parentId"}.
Neither Claude Code's (`message`/`type` in user|assistant|...), Codex's
(`payload`) nor Antigravity's (`step_index`/`source`) reader can place it.

Fixture tests/fixtures/vscode-events.jsonl is a trimmed, sanitized copy of a
real session captured on Windows 11 / VS Code 1.139.1 on 2026-09-30 -- not
constructed.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline import host as _host

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
VSCODE_EVENTS = os.path.join(FIXTURES, "vscode-events.jsonl")
CODEX_ROLLOUT = os.path.join(FIXTURES, "codex-rollout.jsonl")
CLAUDE_SAMPLE = os.path.join(FIXTURES, "sample-session.jsonl")
ANTIGRAVITY = os.path.join(FIXTURES, "antigravity-transcript-563.jsonl")


def _lines(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def test_every_fixture_line_sniffs_as_copilot():
    """Positive control: every real line, whatever its dotted type, is copilot."""
    lines = _lines(VSCODE_EVENTS)
    assert lines, "fixture is empty"
    assert {_host.sniff_envelope(o) for o in lines} == {"copilot"}


def test_other_hosts_still_sniff_as_themselves():
    """Paired control: teaching the sniffer a new shape must not steal the others."""
    assert _host.sniff_envelope(_lines(CODEX_ROLLOUT)[0]) == "codex"
    assert _host.sniff_envelope(_lines(ANTIGRAVITY)[0]) == "antigravity"
    claude = next(o for o in _lines(CLAUDE_SAMPLE) if o.get("type") in ("user", "assistant"))
    assert _host.sniff_envelope(claude) == "claude-code"


def test_copilot_marker_requires_dotted_type_and_data_dict():
    """Negative controls for the structural marker."""
    assert _host.sniff_envelope({"type": "user", "data": {"x": 1}}) != "copilot"      # no dot
    assert _host.sniff_envelope({"type": "user.message", "data": "str"}) != "copilot"  # data not dict
    assert _host.sniff_envelope({"type": "user.message"}) != "copilot"                # no data
    assert _host.sniff_envelope({"type": "user.message", "data": {}}) == "copilot"     # minimal positive
