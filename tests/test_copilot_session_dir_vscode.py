"""find_session() resolves a VS Code Agents / Copilot uuid to
~/.copilot/session-state/<uuid>/events.jsonl (issue: vscode).

Keyed on the file existing, never on host detection (review focus 4)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline import host as _host
from pipeline.extract import find_session

from ._vscode_helpers import UUID


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("COPILOT_HOME", raising=False)
    monkeypatch.delenv("REMEMBER_TRANSCRIPT_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return tmp_path


def _make_copilot(home, uuid):
    d = home / ".copilot" / "session-state" / uuid
    d.mkdir(parents=True, exist_ok=True)
    p = d / "events.jsonl"
    p.write_text('{"type":"session.start","data":{}}\n', encoding="utf-8")
    return p


def test_copilot_transcript_found_by_uuid(home):
    p = _make_copilot(home, UUID)
    assert _host.copilot_transcript_for(UUID) == str(p)
    assert find_session(UUID, str(home / "proj")) == str(p)


def test_copilot_home_override_wins(home, tmp_path, monkeypatch):
    other = tmp_path / "elsewhere"
    monkeypatch.setenv("COPILOT_HOME", str(other))
    d = other / "session-state" / UUID
    d.mkdir(parents=True)
    p = d / "events.jsonl"
    p.write_text("{}\n", encoding="utf-8")
    assert _host.copilot_transcript_for(UUID) == str(p)


def test_absent_file_falls_through_to_claude_lookup(home):
    """Negative half of review focus 4: no session-state file -> existing behaviour."""
    assert _host.copilot_transcript_for(UUID) is None
    with pytest.raises(FileNotFoundError):
        find_session(UUID, str(home / "proj"))


def test_non_uuid_ids_never_touch_session_state(home):
    """Every decoy sits exactly where a joined bad id would land, so a missing
    uuid check would return it. Built from plain names only: no `..` component
    is ever passed to mkdir (POSIX raises FileExistsError on
    `session-state/..`; Windows collapses it lexically)."""
    state = home / ".copilot" / "session-state"
    state.mkdir(parents=True)
    decoy = '{"type":"session.start","data":{}}\n'
    # "" and "." -> session-state/events.jsonl; ".." -> .copilot/events.jsonl
    (state / "events.jsonl").write_text(decoy, encoding="utf-8")
    (home / ".copilot" / "events.jsonl").write_text(decoy, encoding="utf-8")
    # a pre-existing non-uuid session directory
    (state / "not-a-uuid").mkdir()
    (state / "not-a-uuid" / "events.jsonl").write_text(decoy, encoding="utf-8")
    # uuid + "/x" -> session-state/<uuid>/x/events.jsonl
    (state / UUID / "x").mkdir(parents=True)
    (state / UUID / "x" / "events.jsonl").write_text(decoy, encoding="utf-8")
    for bad in ("", ".", "..", "agent-host-copilotcli:/" + UUID, "not-a-uuid", UUID + "/x"):
        assert _host.copilot_transcript_for(bad) is None, bad
    # Positive control: the same tree with a real uuid session resolves.
    p = _make_copilot(home, UUID)
    assert _host.copilot_transcript_for(UUID) == str(p)


def test_supplied_transcript_path_still_wins(home, monkeypatch):
    supplied = home / "supplied.jsonl"
    supplied.write_text("{}\n", encoding="utf-8")
    _make_copilot(home, UUID)
    monkeypatch.setenv("REMEMBER_TRANSCRIPT_PATH", str(supplied))
    assert find_session(UUID, str(home / "proj")) == str(supplied)
