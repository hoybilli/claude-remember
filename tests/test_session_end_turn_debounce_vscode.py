"""Opt-in debounce of the per-turn SessionEnd save on the Copilot hosts, in
scripts/copilot-session-end-hook.sh (issue: vscode).

VS Code Agents and the Copilot desktop app send `SessionEnd` with
`reason=complete` after every turn, so the core session-end-hook.sh runs one
forced save -- one summarizer call -- per turn.
`cooldowns.turn_end_debounce_seconds` (default 0: delegate at once) trades
that for one save N seconds after the last turn of a burst: the wrapper
writes a fresh token to `tmp/turn-end.<session-id>`, detaches a sleeper and
returns; the sleeper delegates to the core only if its token is still the
one on disk when the window ends.

The real wrapper and the real core run in a sandbox plugin root whose
`save-session.sh` is a recording stub: one file per call, so the call count
and each call's mtime are what the assertions read. Waits poll (bounded),
never a fixed sleep. Timing claims are anchored on the token file the
wrapper writes just before it detaches the sleeper.

Every "must not" (no second save, no deferral line, no token file) is paired
with a "must" in this module: the two debounced cases prove the deferral is
observable at all, and every immediate case first proves the save ran and
the core's own log line was written.

Run under Git Bash on Windows (observed); POSIX is reasoned until the Task 8
suites.
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
from ._vscode_helpers import UUID, created_outside, hook_logs, posix, tree

REPO_ROOT = Path(__file__).resolve().parent.parent
BASH = resolve_bash()
pytestmark = pytest.mark.skipif(
    BASH is None,
    reason="no usable bash found (checked PATH, then Git-for-Windows install locations)",
)

WRAPPER = "copilot-session-end-hook.sh"
CORE = "session-end-hook.sh"
# Key value for the immediate-path cases (they never sleep on it).
WINDOW_S = 2
# The burst case needs turn 2's token on disk before turn 1's sleeper wakes.
BURST_WINDOW_S = 8
EXPIRY_WINDOW_S = 4
# The expiry case's proof that the window is slept in the background: the
# hook returns at least this much sooner than EXPIRY_WINDOW_S (a wrapper that
# slept the window in-process could not), and before the save lands.
HOOK_RETURN_MARGIN_S = 1
POLL_DEADLINE_S = 40
KEY = "turn_end_debounce_seconds"
DEFERRED = "copilot session-end: turn-end save deferred"
SUPERSEDED = "copilot session-end: turn-end save superseded by a later turn"
CLAMPED = "copilot session-end: turn_end_debounce_seconds clamped to 3600"
NOT_DEFERRED = "copilot session-end: could not write token, turn-end save not deferred"
EVENT_LINES = 60

_STRIP_PREFIXES = ("CLAUDE_CODE_", "COPILOT_", "CODEX_", "ANTIGRAVITY_", "REMEMBER_")
_STRIP_NAMES = ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR",
                "_LIB_MEMORY_DIR_LOADED")


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
        shutil.copytree(REPO_ROOT / "scripts", self.plugin / "scripts")
        shutil.copytree(REPO_ROOT / ".claude-plugin", self.plugin / ".claude-plugin")
        shutil.copytree(REPO_ROOT / "pipeline", self.plugin / "pipeline",
                        ignore=shutil.ignore_patterns("__pycache__"))
        stub = self.plugin / "scripts" / "save-session.sh"
        _write_lf(stub, "#!/bin/bash\n"
                        f'printf \'%s\\n\' "$*" > "{posix(self.records)}/save.$$"\n'
                        "exit 0\n")
        stub.chmod(0o755)
        cooldowns = {"save_seconds": 120}
        if key_value is not None:
            cooldowns[KEY] = key_value
        _write_lf(self.plugin / "config.json", json.dumps({"cooldowns": cooldowns}))

    def env(self) -> dict:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(_STRIP_PREFIXES) and k not in _STRIP_NAMES}
        env.update({
            "HOME": posix(self.home),
            "USERPROFILE": str(self.home),
            "CLAUDE_PLUGIN_ROOT": posix(self.plugin),
            "CLAUDE_PROJECT_DIR": posix(self.project),
            "REMEMBER_CONFIG_CACHE": "0",
            "REMEMBER_TEST_COMPLETION_MARKER": posix(self.tmp / "marker.log"),
        })
        return env

    def run_hook(self, *, script: str = WRAPPER, reason: str = "complete",
                 session_id: str | None = UUID) -> tuple[float, float]:
        """Run SCRIPT once. Returns (seconds it took, time.time() after)."""
        body = {"hook_event_name": "SessionEnd", "reason": reason,
                "cwd": posix(self.project), "timestamp": "t"}
        if session_id is not None:
            body["session_id"] = session_id
        started = time.monotonic()
        r = subprocess.run([BASH, posix(self.plugin / "scripts" / script)],
                           input=json.dumps(body).encode(), env=self.env(),
                           capture_output=True, timeout=60, check=False)
        elapsed = time.monotonic() - started
        returned_at = time.time()
        assert r.returncode == 0, decode_bash_output(r.stderr)
        return elapsed, returned_at

    def invoke_wrapper(self) -> subprocess.CompletedProcess:
        """One SessionEnd through the wrapper; the result, unchecked."""
        body = {"hook_event_name": "SessionEnd", "reason": "complete",
                "cwd": posix(self.project), "timestamp": "t", "session_id": UUID}
        return subprocess.run([BASH, posix(self.plugin / "scripts" / WRAPPER)],
                              input=json.dumps(body).encode(), env=self.env(),
                              capture_output=True, timeout=60, check=False)

    def stub_core(self, status: int) -> Path:
        """Replace the core session-end-hook.sh with a stub that drains stdin,
        touches a marker, prints STUB-CORE and exits STATUS. Returns the
        marker path."""
        marker = self.tmp / "core-ran"
        _write_lf(self.plugin / "scripts" / CORE,
                  "#!/bin/bash\ncat >/dev/null\n"
                  f': > "{posix(marker)}"\n'
                  f"echo STUB-CORE\nexit {status}\n")
        return marker

    def run_post_tool(self) -> None:
        """One Copilot PostToolUse through the post-tool wrapper, with a
        Copilot transcript long enough to trigger a delta save."""
        events = self.home / ".copilot" / "session-state" / UUID / "events.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)
        events.write_bytes(b'{"type":"user.message","data":{"content":"hi"}}\n' * EVENT_LINES)
        body = {"hook_event_name": "PostToolUse", "session_id": UUID,
                "cwd": posix(self.project), "tool_name": "bash", "tool_input": {}}
        r = subprocess.run([BASH, posix(self.plugin / "scripts" / "copilot-post-tool-hook.sh")],
                           input=json.dumps(body).encode(), env=self.env(),
                           capture_output=True, timeout=60, check=False)
        assert r.returncode == 0, decode_bash_output(r.stderr)

    def saves(self) -> list[Path]:
        return sorted(self.records.glob("save.*"))

    def save_args(self) -> list[list[str]]:
        return [p.read_text(encoding="utf-8").split() for p in self.saves()]

    def log_text(self) -> str:
        return hook_logs(self.remember / "logs", "memory-*.log")

    def _marker_count(self, line: str) -> int:
        marker = self.tmp / "marker.log"
        if not marker.exists():
            return 0
        return marker.read_text(encoding="utf-8", errors="replace").count(line)

    def launches(self) -> int:
        return self._marker_count("about to launch subshell")

    def completions(self) -> int:
        return self._marker_count("save-session.sh exited status=")

    def token_path(self) -> Path:
        return self.remember / "tmp" / f"turn-end.{UUID}"

    def token_files(self) -> list[Path]:
        return sorted((self.remember / "tmp").glob("turn-end.*"))

    def wait_for_token(self, other_than: str | None = None) -> tuple[str, float]:
        """Poll the token file until it holds a complete token different from
        OTHER_THAN; return (token, mtime)."""
        path = self.token_path()
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
            f"saves={self.save_args()} launches={self.launches()}\n"
            f"log:\n{self.log_text()}")

    def autonomous_save_logs(self) -> list[Path]:
        """The per-spawn logs the core post-tool-hook.sh seeds synchronously,
        just before it backgrounds save-session.sh."""
        return sorted((self.remember / "logs" / "autonomous").glob("save-*.log"))

    def stand_down_sleeper(self) -> None:
        """Teardown for a case that deferred: end its sleeper the way a later
        turn would, with a new token (it re-reads at least every 5 s and
        exits on the mismatch). A no-op when nothing was deferred, so a
        failure before the deferral is reported as itself, not as a timeout
        here."""
        if DEFERRED not in self.log_text():
            return
        self.token_path().write_text("test-teardown\n", encoding="utf-8")
        self.wait_for(lambda: SUPERSEDED in self.log_text(), "the sleeper to stand down")

    def assert_immediate_single_save(self) -> None:
        """Shared assertions for every case that must delegate at once."""
        self.wait_for(lambda: len(self.saves()) >= 1, "the save to run")
        log = self.log_text()
        # Positive control for the log negatives: the core's own first line.
        assert "session-end: reason=" in log, log
        assert DEFERRED not in log, log
        assert SUPERSEDED not in log, log
        assert self.token_files() == [], self.token_files()
        self.wait_for(lambda: self.completions() >= 1, "the backgrounded save to finish")
        assert len(self.saves()) == 1, self.save_args()


# --- The debounced path -------------------------------------------------------

def test_debounce_two_turns_in_a_burst_save_exactly_once(tmp_path):
    sb = Sandbox(tmp_path, key_value=BURST_WINDOW_S)
    sb.run_hook()
    token1, mtime1 = sb.wait_for_token()
    sb.run_hook()
    token2, mtime2 = sb.wait_for_token(other_than=token1)
    if mtime2 - mtime1 >= BURST_WINDOW_S:
        pytest.skip(
            f"runner too slow to exercise the burst: turn 2's token landed "
            f"{mtime2 - mtime1:.2f}s after turn 1's, outside the {BURST_WINDOW_S}s window")
    sb.wait_for(lambda: len(sb.saves()) + sb.log_text().count(SUPERSEDED) >= 2,
                "both turn-end sleepers to resolve")
    sb.wait_for(lambda: sb.completions() >= 1, "the backgrounded save to finish")
    log = sb.log_text()
    assert len(sb.saves()) == 1, f"{sb.save_args()}\n{log}"
    assert log.count(SUPERSEDED) == 1, log
    assert log.count(DEFERRED) == 2, log
    assert f"{DEFERRED} {BURST_WINDOW_S}s (cooldowns.{KEY})" in log, log
    assert sb.save_args() == [[UUID, "--force"]]
    # Turn 2's sleeper saved, and not before its own window.
    assert sb.saves()[0].stat().st_mtime - mtime2 >= BURST_WINDOW_S
    assert sb.token_files() == [], sb.token_files()


def test_debounce_window_expires_then_saves_and_the_hook_did_not_wait(tmp_path):
    sb = Sandbox(tmp_path, key_value=EXPIRY_WINDOW_S)
    elapsed, returned_at = sb.run_hook()
    assert elapsed < EXPIRY_WINDOW_S - HOOK_RETURN_MARGIN_S, \
        f"hook took {elapsed:.2f}s to return: it slept the {EXPIRY_WINDOW_S}s window itself"
    _token, token_mtime = sb.wait_for_token()
    sb.wait_for(lambda: len(sb.saves()) >= 1, "the deferred save to run")
    saved_at = sb.saves()[0].stat().st_mtime
    assert returned_at < saved_at, "the hook waited for the deferred save"
    assert saved_at - token_mtime >= EXPIRY_WINDOW_S
    log = sb.log_text()
    assert log.count(DEFERRED) == 1, log
    # Also the clamp case's paired control: a value inside the cap is used as
    # given, with no clamp line.
    assert f"{DEFERRED} {EXPIRY_WINDOW_S}s (cooldowns.{KEY})" in log, log
    assert CLAMPED not in log, log
    assert SUPERSEDED not in log, log
    sb.wait_for(lambda: sb.completions() >= 1, "the backgrounded save to finish")
    assert sb.save_args() == [[UUID, "--force"]]
    assert sb.token_files() == [], "the winning sleeper must remove its token file"


def test_post_tool_delta_saves_are_not_paused_during_the_window(tmp_path):
    """Accepted behaviour change (spec 4.4): the core's tmp/save-session.pid
    is not held while the sleeper waits, so a PostToolUse inside the window
    still triggers its delta save (argv: the id, no --force)."""
    sb = Sandbox(tmp_path, key_value=BURST_WINDOW_S)
    try:
        sb.run_hook()
        sb.wait_for_token()
        sb.run_post_tool()
        # Positive control for the next case's negative: the core seeded its
        # spawn log before it returned.
        assert len(sb.autonomous_save_logs()) == 1, sb.autonomous_save_logs()
        sb.wait_for(lambda: [UUID] in sb.save_args(), "the delta save")
        assert sb.token_path().exists(), "the window had already closed"
    finally:
        sb.stand_down_sleeper()


# A bash loop of short sleeps rather than `exec sleep 30`: on Git Bash,
# killing the Popen handle of an exec'd `sleep` leaves sleep.exe running. The
# loop ends on its own once STOP exists, or after about 60 s at most.
_HOLDER = ('echo $$ > "$1"; i=0; '
           'while [ ! -f "$2" ] && [ "$i" -lt 300 ]; do sleep 0.2; i=$((i + 1)); done')


def test_a_live_save_pid_does_pause_the_delta_save(tmp_path):
    """Positive control for the case above: the same PostToolUse while
    tmp/save-session.pid names a live process is skipped by the core -- so
    the save above ran because the window holds no pid, not because the
    core ignores the pid file. The core logs nothing when it skips, so the
    proof is what it does synchronously before it returns when it does
    spawn (seeds logs/autonomous/save-*.log, rewrites the pid file): absent
    here, present in the case above."""
    sb = Sandbox(tmp_path)
    pid_file = tmp_path / "holder.pid"
    stop = tmp_path / "holder.stop"
    holder = subprocess.Popen([BASH, "-c", _HOLDER, "holder", posix(pid_file), posix(stop)])
    try:
        sb.wait_for(lambda: pid_file.exists() and pid_file.read_text().strip().isdigit(),
                    "the holder's pid")
        held = pid_file.read_text().strip()
        save_pid = sb.remember / "tmp" / "save-session.pid"
        save_pid.write_text(held + "\n")
        sb.run_post_tool()
        assert sb.autonomous_save_logs() == [], sb.autonomous_save_logs()
        assert save_pid.read_text().strip() == held
        assert sb.saves() == [], sb.save_args()
    finally:
        stop.write_text("")
        try:
            holder.wait(timeout=30)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.wait(timeout=30)


# --- Delegating at once --------------------------------------------------------

@pytest.mark.parametrize("key_value", [None, 0], ids=["key-absent", "key-zero"])
def test_default_is_the_immediate_per_turn_save(tmp_path, key_value):
    sb = Sandbox(tmp_path, key_value=key_value)
    sb.run_hook()
    sb.assert_immediate_single_save()
    assert sb.save_args() == [[UUID, "--force"]]


def test_other_reason_is_immediate_even_with_the_key_set(tmp_path):
    sb = Sandbox(tmp_path, key_value=WINDOW_S)
    sb.run_hook(reason="other")
    sb.assert_immediate_single_save()


def test_the_core_hook_alone_never_debounces(tmp_path):
    """Claude Code runs the core hook directly: the key does nothing there
    (the debounce lives only in the Copilot wrapper)."""
    sb = Sandbox(tmp_path, key_value=WINDOW_S)
    sb.run_hook(script=CORE)
    sb.assert_immediate_single_save()


@pytest.mark.parametrize("key_value", ["abc", "2s", -2, 1.5, True, "1234567890"],
                         ids=["word", "suffix", "negative", "fraction", "bool", "ten-digits"])
def test_non_integer_key_behaves_as_zero(tmp_path, key_value):
    sb = Sandbox(tmp_path, key_value=key_value)
    sb.run_hook()
    sb.assert_immediate_single_save()


def test_no_session_id_is_not_debounced(tmp_path):
    """The token file is keyed by session id; without one there is nothing
    safe to key it on."""
    sb = Sandbox(tmp_path, key_value=WINDOW_S)
    sb.run_hook(session_id=None)
    sb.assert_immediate_single_save()
    assert sb.save_args() == [["--force"]]


def test_token_write_failure_saves_at_once_and_says_so(tmp_path):
    """A directory where the token file goes: the write fails, the save is
    not deferred, and the log says why."""
    sb = Sandbox(tmp_path, key_value=WINDOW_S)
    sb.token_path().mkdir()
    sb.run_hook()
    sb.wait_for(lambda: len(sb.saves()) >= 1, "the save to run")
    log = sb.log_text()
    assert NOT_DEFERRED in log, log
    assert DEFERRED not in log, log
    sb.wait_for(lambda: sb.completions() >= 1, "the backgrounded save to finish")
    assert sb.save_args() == [[UUID, "--force"]]


# --- The cap -------------------------------------------------------------------

def test_debounce_above_an_hour_is_clamped_to_an_hour(tmp_path):
    sb = Sandbox(tmp_path, key_value=999999)
    try:
        sb.run_hook()
        sb.wait_for(lambda: DEFERRED in sb.log_text(), "the deferral line")
        log = sb.log_text()
        assert log.count(CLAMPED) == 1, log
        assert f"{DEFERRED} 3600s (cooldowns.{KEY})" in log, log
        assert sb.saves() == []
    finally:
        # The hour-long sleeper must not outlive the test.
        sb.stand_down_sleeper()
    assert sb.saves() == []

# Its paired control (a value inside the cap, not clamped) is the expiry case.


# --- The core's validator still runs after the strip -----------------------------

def test_traversal_after_the_prefix_is_rejected_by_the_core(tmp_path):
    """`agent-host-x:/../../x` strips to `../../x`: not uuid-shaped, so no
    token file is keyed on it; the core's validator empties it (`session=
    unresolved`), saves without an id, and nothing is written outside the
    sandbox store."""
    sb = Sandbox(tmp_path, key_value=WINDOW_S)
    before = tree(tmp_path)
    sb.run_hook(session_id="agent-host-x:/../../x")
    sb.assert_immediate_single_save()
    assert "session=unresolved" in sb.log_text()
    assert sb.save_args() == [["--force"]]
    outside = created_outside(tmp_path, before, ("project/.remember/", "saves/", "marker.log"))
    assert outside == [], outside


def test_prefixed_uuid_reaches_the_save_as_the_session_id(tmp_path):
    """Positive control for the rejection above (immediate path, key absent)."""
    sb = Sandbox(tmp_path)
    sb.run_hook(session_id=f"agent-host-copilotcli:/{UUID}")
    sb.assert_immediate_single_save()
    assert f"session={UUID}" in sb.log_text()
    assert sb.save_args() == [[UUID, "--force"]]


# --- Exit status and the missing library -----------------------------------------

@pytest.mark.parametrize("status", [0, 3])
def test_wrapper_exits_with_the_cores_status(tmp_path, status):
    """Delegating at once (key absent), the core's exit code is forwarded; 0
    is the control for 3. Also the positive control for the missing-library
    case below: the same stub core runs (marker written, its line printed)
    when the library is present."""
    sb = Sandbox(tmp_path)
    marker = sb.stub_core(status)
    r = sb.invoke_wrapper()
    assert r.returncode == status, decode_bash_output(r.stderr)
    assert marker.is_file()
    assert decode_bash_output(r.stdout).strip() == "STUB-CORE"


def test_missing_adapter_library_exits_0_without_delegating(tmp_path):
    """A wrapper-side failure: lib-copilot-adapter.sh is gone from the
    plugin copy. The wrapper exits 0 (not the stub's 3), prints nothing, and
    the stub core never ran."""
    sb = Sandbox(tmp_path)
    marker = sb.stub_core(3)
    (sb.plugin / "scripts" / "lib-copilot-adapter.sh").unlink()
    r = sb.invoke_wrapper()
    assert r.returncode == 0, decode_bash_output(r.stderr)
    assert r.stdout == b""
    assert not marker.exists()
