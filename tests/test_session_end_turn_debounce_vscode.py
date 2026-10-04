"""Opt-in debounce of the per-turn SessionEnd save on the Copilot host
(issue: vscode).

VS Code Agents (the GitHub Copilot harness) sends `SessionEnd` with
`reason=complete` after every turn and nothing when the session is closed, so
`scripts/session-end-hook.sh` runs one forced save -- one summarizer call --
per turn. `cooldowns.turn_end_debounce_seconds` (default 0, today's path)
lets a user trade that for one save N seconds after the last turn of a burst:
each such hook writes a fresh token to `tmp/turn-end.<session-id>` and its
backgrounded save sleeps N seconds, then saves only if its token is still the
one on disk.

The real hook runs in a sandbox plugin root whose `save-session.sh` is a
recording stub: one file per call, so the call count and each call's mtime
are what the assertions read. Waits poll for those records (bounded), never a
fixed sleep; the one timing assertion that needs a floor (case 2) compares
the stub's mtime against the moment the hook returned.

Every "must not" here (no second save, no deferral line, no token file) is
paired with a "must" in this module: cases 1 and 2 prove the deferral is
observable at all, and every immediate-path case first proves the save ran
and the hook's own log line was written.

Runs wherever `resolve_bash()` finds a real bash -- including Windows under
Git Bash, where it was written and run; POSIX is reasoned, not observed here.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from ._bash_runner import decode_bash_output, resolve_bash

REPO_ROOT = Path(__file__).resolve().parent.parent
BASH = resolve_bash()
pytestmark = pytest.mark.skipif(
    BASH is None,
    reason="no usable bash found (checked PATH, then Git-for-Windows install locations)",
)

UUID = "3f2b9c4e-7a1d-4e8b-9c0f-5d6e7f8a9b0c"
WINDOW_S = 2
POLL_DEADLINE_S = 40
KEY = "turn_end_debounce_seconds"
DEFERRED = "session-end: turn-end save deferred"
SUPERSEDED = "session-end: turn-end save superseded by a later turn"

# Everything that would let the developer's own session leak into the hook:
# host signatures (the suite usually runs inside Claude Code), every plugin
# variable (REMEMBER_DIR, REMEMBER_SESSION_END_FOREGROUND, ...), and the
# generic plugin-root name VS Code exports.
_STRIP_PREFIXES = ("CLAUDE_CODE_", "COPILOT_", "CODEX_", "ANTIGRAVITY_", "REMEMBER_")
_STRIP_NAMES = ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR",
                "_LIB_MEMORY_DIR_LOADED")


def _posix(p) -> str:
    """Forward slashes: the hook derives its own directory with
    `${BASH_SOURCE[0]%/*}`, which only splits on `/` (see
    tests/test_session_end_log_names_488.py::_posix_path)."""
    return str(p).replace("\\", "/")


def _write_lf(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


class Sandbox:
    def __init__(self, tmp_path: Path, key_value=None):
        self.tmp = tmp_path
        self.plugin = tmp_path / "plugin"
        self.project = tmp_path / "project"
        self.remember = self.project / ".remember"
        self.home = tmp_path / "home"
        self.records = tmp_path / "saves"
        for d in (self.remember / "tmp", self.remember / "logs", self.home, self.records):
            d.mkdir(parents=True)
        # Byte copies (not read_text/write_text, which would write CRLF on
        # Windows) of the real scripts and pipeline package.
        shutil.copytree(REPO_ROOT / "scripts", self.plugin / "scripts")
        shutil.copytree(REPO_ROOT / "pipeline", self.plugin / "pipeline",
                        ignore=shutil.ignore_patterns("__pycache__"))
        _write_lf(self.plugin / "scripts" / "save-session.sh",
                  "#!/bin/bash\n"
                  f'printf \'%s\\n\' "$*" > "{_posix(self.records)}/save.$$"\n'
                  "exit 0\n")
        cooldowns = {"save_seconds": 120}
        if key_value is not None:
            cooldowns[KEY] = key_value
        _write_lf(self.plugin / "config.json", json.dumps({"cooldowns": cooldowns}))

    def env(self, copilot: bool) -> dict:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(_STRIP_PREFIXES) and k not in _STRIP_NAMES}
        env.update({
            "HOME": _posix(self.home),
            "USERPROFILE": str(self.home),
            "CLAUDE_PLUGIN_ROOT": _posix(self.plugin),
            "CLAUDE_PROJECT_DIR": _posix(self.project),
            "REMEMBER_CONFIG_CACHE": "0",
            "REMEMBER_TEST_COMPLETION_MARKER": _posix(self.tmp / "marker.log"),
        })
        if copilot:
            env["COPILOT_CLI"] = "1"
        return env

    def run_hook(self, *, copilot: bool, reason: str = "complete",
                 session_id: str | None = UUID) -> tuple[float, float]:
        """Run the hook once. Returns (seconds the hook took, wall-clock
        time.time() right after it returned)."""
        body = {"hook_event_name": "SessionEnd", "reason": reason,
                "cwd": _posix(self.project)}
        if session_id is not None:
            body["session_id"] = session_id
        started = time.monotonic()
        r = subprocess.run([BASH, _posix(self.plugin / "scripts" / "session-end-hook.sh")],
                           input=json.dumps(body).encode(), env=self.env(copilot),
                           capture_output=True, timeout=60, check=False)
        elapsed = time.monotonic() - started
        returned_at = time.time()
        assert r.returncode == 0, decode_bash_output(r.stderr)
        return elapsed, returned_at

    def saves(self) -> list[Path]:
        return sorted(self.records.glob("save.*"))

    def log_text(self) -> str:
        return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                         for p in sorted((self.remember / "logs").glob("memory-*.log")))

    def launches(self) -> int:
        marker = self.tmp / "marker.log"
        if not marker.exists():
            return 0
        return marker.read_text(encoding="utf-8", errors="replace").count(
            "about to launch subshell")

    def token_files(self) -> list[Path]:
        return sorted((self.remember / "tmp").glob("turn-end.*"))

    def wait_for(self, cond, what: str) -> None:
        deadline = time.monotonic() + POLL_DEADLINE_S
        while time.monotonic() < deadline:
            if cond():
                return
            time.sleep(0.1)
        raise AssertionError(
            f"timed out after {POLL_DEADLINE_S}s waiting for {what}\n"
            f"saves={[p.name for p in self.saves()]} launches={self.launches()}\n"
            f"log:\n{self.log_text()}")

    def assert_immediate_single_save(self) -> None:
        """Shared assertions for every case that must take today's path."""
        self.wait_for(lambda: len(self.saves()) >= 1, "the save to run")
        log = self.log_text()
        # Positive control for the log-based negatives below: the hook's
        # own first line is there, so an absent "deferred" line is not an
        # absent log.
        assert "session-end: reason=" in log, log
        assert DEFERRED not in log, log
        assert SUPERSEDED not in log, log
        assert self.token_files() == [], self.token_files()
        assert len(self.saves()) == 1, [p.read_text() for p in self.saves()]


# --- The debounced path (cases 1 and 2) -------------------------------------

def test_debounce_two_turns_in_a_burst_save_exactly_once(tmp_path):
    sb = Sandbox(tmp_path, key_value=WINDOW_S)
    sb.run_hook(copilot=True)
    time.sleep(0.3)
    sb.run_hook(copilot=True)

    # Both detached children reached the fork, and each sleeper has resolved
    # one way or the other (saved, or logged superseded). Terminates on the
    # buggy outcomes too: two saves, or no debounce at all.
    sb.wait_for(lambda: sb.launches() == 2
                and len(sb.saves()) + sb.log_text().count(SUPERSEDED) >= 2,
                "both turn-end saves to resolve")
    log = sb.log_text()
    assert len(sb.saves()) == 1, (
        "two turn-end hooks inside one debounce window must save once, not "
        f"once per turn -- saves: {[p.read_text() for p in sb.saves()]}\n{log}")
    assert log.count(SUPERSEDED) == 1, log
    assert log.count(DEFERRED) == 2, log
    assert f"{DEFERRED} {WINDOW_S}s (cooldowns.{KEY})" in log, log
    assert sb.saves()[0].read_text().split() == [UUID, "--force"]
    assert sb.token_files() == [], sb.token_files()


def test_debounce_window_expires_then_saves_and_the_hook_did_not_wait(tmp_path):
    sb = Sandbox(tmp_path, key_value=WINDOW_S)
    elapsed, returned_at = sb.run_hook(copilot=True)

    # The sleep is in the detached background, not in the hook the host waits
    # on: were it in the foreground the hook could not return inside it.
    assert elapsed < WINDOW_S, f"hook took {elapsed:.2f}s against a {WINDOW_S}s window"

    sb.wait_for(lambda: len(sb.saves()) >= 1, "the deferred save to run")
    saved_at = sb.saves()[0].stat().st_mtime
    assert saved_at - returned_at >= WINDOW_S, (
        f"save ran {saved_at - returned_at:.2f}s after the hook returned; the "
        f"debounce window is {WINDOW_S}s")
    log = sb.log_text()
    assert log.count(DEFERRED) == 1, log
    assert SUPERSEDED not in log, log
    # Let any stray second call land before counting.
    time.sleep(0.5)
    assert len(sb.saves()) == 1
    assert sb.saves()[0].read_text().split() == [UUID, "--force"]
    assert sb.token_files() == [], "the winning save must remove its token file"


# --- Today's path, unchanged (cases 3-6, plus no session id) ----------------

@pytest.mark.parametrize("key_value", [None, 0], ids=["key-absent", "key-zero"])
def test_default_is_the_immediate_per_turn_save(tmp_path, key_value):
    sb = Sandbox(tmp_path, key_value=key_value)
    sb.run_hook(copilot=True)
    sb.assert_immediate_single_save()
    assert sb.saves()[0].read_text().split() == [UUID, "--force"]


def test_other_reason_is_immediate_even_with_the_key_set(tmp_path):
    sb = Sandbox(tmp_path, key_value=WINDOW_S)
    sb.run_hook(copilot=True, reason="other")
    sb.assert_immediate_single_save()


def test_other_host_is_immediate_even_with_the_key_set(tmp_path):
    sb = Sandbox(tmp_path, key_value=WINDOW_S)
    sb.run_hook(copilot=False)
    sb.assert_immediate_single_save()


@pytest.mark.parametrize("key_value", ["abc", "2s", -2, 1.5, True],
                         ids=["word", "suffix", "negative", "fraction", "bool"])
def test_non_integer_key_behaves_as_zero(tmp_path, key_value):
    sb = Sandbox(tmp_path, key_value=key_value)
    sb.run_hook(copilot=True)
    sb.assert_immediate_single_save()


def test_no_session_id_is_not_debounced(tmp_path):
    """The token file is keyed by session id; without one there is nothing
    safe to key it on, so the hook falls through to today's path."""
    sb = Sandbox(tmp_path, key_value=WINDOW_S)
    sb.run_hook(copilot=True, session_id=None)
    sb.assert_immediate_single_save()
    assert sb.saves()[0].read_text().split() == ["--force"]
