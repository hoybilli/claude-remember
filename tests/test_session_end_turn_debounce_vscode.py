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
fixed sleep. Every timing claim is anchored on the token file the detached
hook writes just before it forks the sleeper -- that write, not the moment
the host-facing process returned, is when the window starts -- and the two
debounced cases get windows wide enough that a slow hook preamble on a
loaded runner cannot eat them. Where the runner is too slow anyway, the
burst case skips with that reason instead of reporting a regression.

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
# Key value for the immediate-path cases (they never sleep on it).
WINDOW_S = 2
# The burst case needs turn 2's token on disk before turn 1's sleeper wakes:
# that gap is turn 2's whole detached preamble (~1 s on a desktop, measured
# past 3 s on a slow Windows machine, #560), so the window is set well above it.
BURST_WINDOW_S = 8
# Case 2's debounce window: only what the deferred save must wait, measured
# from the token file. Kept short so the case stays quick.
EXPIRY_WINDOW_S = 4
# Case 2's sanity bound on how long the host-facing hook took to return.
# Deliberately NOT tied to EXPIRY_WINDOW_S: the proof that the sleep is in
# the background is `returned_at < saved_at`, not this number. It is only a
# hang detector, so it gets the slow-runner figure above (~3.4 s, #560) with
# more than 2x margin, and a slow runner cannot fail the case through it.
HOOK_RETURN_BOUND_S = 10
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
        shutil.copytree(REPO_ROOT / ".claude-plugin", self.plugin / ".claude-plugin")
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

    def wait_for_token(self, other_than: str | None = None) -> tuple[str, float]:
        """Poll tmp/turn-end.<UUID> until it holds a complete token (one
        line) different from `other_than`; return (token, mtime). The read
        is repeated around the stat so a rewrite between the two cannot pair
        one turn's token with another turn's mtime."""
        path = self.remember / "tmp" / f"turn-end.{UUID}"
        found: list[tuple[str, float]] = []

        def _probe() -> bool:
            try:
                before = path.read_bytes()
                mtime = path.stat().st_mtime
                after = path.read_bytes()
            except OSError:
                return False
            if before != after or not before.endswith(b"\n"):
                return False
            token = before.decode("utf-8", errors="replace").strip()
            if not token or token == other_than:
                return False
            found.append((token, mtime))
            return True

        self.wait_for(_probe, f"a token in {path.name} other than {other_than!r}")
        return found[-1]

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
    sb = Sandbox(tmp_path, key_value=BURST_WINDOW_S)
    # Turn 1's token is taken off disk before turn 2 is launched, so T1 and
    # T2 below are known to be turn 1's and turn 2's -- launching both first
    # would let turn 2's faster preamble write first and swap them.
    sb.run_hook(copilot=True)
    token1, mtime1 = sb.wait_for_token()
    sb.run_hook(copilot=True)
    token2, mtime2 = sb.wait_for_token(other_than=token1)

    # The precondition for "one save per burst": turn 2's token landed inside
    # turn 1's window. If it did not, two saves are the CORRECT outcome, and
    # what failed is the runner's speed, not the hook.
    if mtime2 - mtime1 >= BURST_WINDOW_S:
        pytest.skip(
            f"runner too slow to exercise the burst: turn 2's token landed "
            f"{mtime2 - mtime1:.2f}s after turn 1's, outside the "
            f"{BURST_WINDOW_S}s window -- an environment problem, not a "
            f"regression in the debounce")

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
    assert f"{DEFERRED} {BURST_WINDOW_S}s (cooldowns.{KEY})" in log, log
    assert sb.saves()[0].read_text().split() == [UUID, "--force"]
    # Turn 2's sleeper is the one that saved: not before its own window.
    assert sb.saves()[0].stat().st_mtime - mtime2 >= BURST_WINDOW_S
    assert sb.token_files() == [], sb.token_files()


def test_debounce_window_expires_then_saves_and_the_hook_did_not_wait(tmp_path):
    sb = Sandbox(tmp_path, key_value=EXPIRY_WINDOW_S)
    elapsed, returned_at = sb.run_hook(copilot=True)

    # Hang detector only (see HOOK_RETURN_BOUND_S); the background proof is
    # the returned_at < saved_at assertion below.
    assert elapsed < HOOK_RETURN_BOUND_S, (
        f"hook took {elapsed:.2f}s to return (bound {HOOK_RETURN_BOUND_S}s)")

    # The window starts at the token write (the detached child writes it just
    # before forking the sleeper), not when the host-facing process returned.
    _token, token_mtime = sb.wait_for_token()
    sb.wait_for(lambda: len(sb.saves()) >= 1, "the deferred save to run")
    saved_at = sb.saves()[0].stat().st_mtime
    # The sleep is in the detached background, not in the hook the host waits
    # on: the hook had already returned when the deferred save ran.
    assert returned_at < saved_at, (
        f"the deferred save ran {returned_at - saved_at:.2f}s BEFORE the hook "
        f"returned -- the hook waited for it")
    assert saved_at - token_mtime >= EXPIRY_WINDOW_S, (
        f"save ran {saved_at - token_mtime:.2f}s after the token was written; "
        f"the debounce window is {EXPIRY_WINDOW_S}s")
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


# --- The cap -----------------------------------------------------------------

CLAMPED = "session-end: cooldowns.turn_end_debounce_seconds=999999 clamped to 3600"

# Stops the sleeping subshell (and its `sleep` child) the clamp case leaves
# behind, so no hour-long process outlives the test. `ps -ef` prints PID and
# PPID as its second and third columns, and the command with its arguments
# last, on Linux, macOS and Git Bash alike (Git Bash's `ps` has no -o).
# $1 is the pid from tmp/save-session.pid (may be empty); $2 is the path of
# this sandbox's hook copy. The second pass is the belt for a pid file that
# never showed up: any `sleep 3600` whose parent shell is running this
# sandbox's hook is the clamp case's sleeper.
_STOP_SLEEPER = r'''p=$1 hook=$2
if [ -n "$p" ]; then
    # Stop the parent first so it can fork nothing between the child listing
    # and the kill (a `sleep 3600` forked in that gap would be orphaned).
    kill -STOP "$p" 2>/dev/null
    kids=$(ps -ef 2>/dev/null | awk -v p="$p" '$3==p {print $2}')
    kill $kids "$p" 2>/dev/null
    kill -CONT "$p" 2>/dev/null
fi
ps -ef 2>/dev/null | awk '$NF=="3600" && $(NF-1) ~ /(^|\/)sleep$/ {print $2, $3}' |
while read -r kid parent; do
    case "$(ps -ef 2>/dev/null | awk -v p="$parent" '$2==p')" in
        *"$hook"*) kill "$kid" "$parent" 2>/dev/null ;;
    esac
done
exit 0
'''
# The hook writes tmp/save-session.pid only after it has forked the sleeper,
# which is after the deferral line the test waits on: poll for it.
_PID_FILE_WAIT_S = 10


def _stop_sleeper(sb: Sandbox) -> None:
    pid_file = sb.remember / "tmp" / "save-session.pid"
    pid = ""
    deadline = time.monotonic() + _PID_FILE_WAIT_S
    while time.monotonic() < deadline:
        try:
            pid = pid_file.read_text(encoding="utf-8").strip()
        except OSError:
            pid = ""
        if pid.isdigit():
            break
        time.sleep(0.1)
    hook = _posix(sb.plugin / "scripts" / "session-end-hook.sh")
    subprocess.run([BASH, "-c", _STOP_SLEEPER, "stop", pid if pid.isdigit() else "", hook],
                   capture_output=True, timeout=30, check=False)


def test_debounce_above_an_hour_is_clamped_to_an_hour(tmp_path):
    sb = Sandbox(tmp_path, key_value=999999)
    try:
        sb.run_hook(copilot=True)
        sb.wait_for(lambda: DEFERRED in sb.log_text(), "the deferral line")
        log = sb.log_text()
        assert log.count(CLAMPED) == 1, log
        assert f"{DEFERRED} 3600s (cooldowns.{KEY})" in log, log
        assert sb.saves() == []
    finally:
        _stop_sleeper(sb)


def test_debounce_at_or_below_an_hour_is_not_clamped(tmp_path):
    """Paired control: a value inside the cap is used as given, with no
    clamp line (the deferral line proves the log was read)."""
    sb = Sandbox(tmp_path, key_value=EXPIRY_WINDOW_S)
    sb.run_hook(copilot=True)
    sb.wait_for(lambda: len(sb.saves()) >= 1, "the deferred save to run")
    log = sb.log_text()
    assert f"{DEFERRED} {EXPIRY_WINDOW_S}s (cooldowns.{KEY})" in log, log
    assert "clamped to 3600" not in log, log


# --- The validator still runs after the prefix strip (end to end) ----------

def _tree(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


def test_traversal_after_the_prefix_is_rejected_by_the_hook(tmp_path):
    """`agent-host-x:/../../x` strips to `../../x`, which the hook's own
    validator must empty: the hook exits 0, logs `session=unresolved`, saves
    without an id (not debounced: nothing safe to key a token on), and
    writes nothing outside the sandbox store."""
    sb = Sandbox(tmp_path, key_value=WINDOW_S)
    before = _tree(tmp_path)
    sb.run_hook(copilot=True, session_id="agent-host-x:/../../x")
    sb.assert_immediate_single_save()
    assert "session=unresolved" in sb.log_text()
    assert sb.saves()[0].read_text().split() == ["--force"]
    new = _tree(tmp_path) - before
    allowed = ("project/.remember/", "saves/", "marker.log")
    outside = sorted(p for p in new if not p.startswith(allowed))
    assert outside == [], outside


def test_prefixed_uuid_reaches_the_save_as_the_session_id(tmp_path):
    """Positive control for the rejection above: the same prefix with a valid
    id keeps the id (immediate path, key absent)."""
    sb = Sandbox(tmp_path)
    sb.run_hook(copilot=False, session_id=f"agent-host-copilotcli:/{UUID}")
    sb.assert_immediate_single_save()
    assert f"session={UUID}" in sb.log_text()
    assert sb.saves()[0].read_text().split() == [UUID, "--force"]
