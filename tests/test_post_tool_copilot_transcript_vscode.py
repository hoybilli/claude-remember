"""The Copilot transcript under <COPILOT_HOME or ~/.copilot>/session-state/
<uuid>/events.jsonl (issue: vscode).

Hook level: scripts/copilot-post-tool-hook.sh finds that file and hands it
to the unchanged core post-tool-hook.sh as the payload's first
`transcript_path`, so the core saves from it. Each case runs the real
wrapper and the real core in a sandbox plugin whose save-session.sh is a
recording stub (argv and REMEMBER_TRANSCRIPT_PATH, one file per call).

Library level: remember_copilot_transcript_into in scripts/lib-session-id.sh,
the no-fork lookup the wrapper and doctor.sh share (#511).
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
pytestmark = pytest.mark.skipif(BASH is None, reason="no POSIX bash found (Git Bash on Windows)")

NO_SESSION_DIR = "no session dir for this project"
# More lines than the core's default delta threshold (50), so one tool call
# triggers a save without touching config.
EVENT_LINES = 60
_STRIP_PREFIXES = ("CLAUDE_CODE_", "COPILOT_", "CODEX_", "ANTIGRAVITY_", "REMEMBER_")
_STRIP_NAMES = ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR")
POLL_DEADLINE_S = 40


def _events(path: Path, lines: int = EVENT_LINES) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"type":"user.message","data":{"content":"hi"}}\n' * lines)


def _same(seen: str, path: Path, root: Path) -> bool:
    """SEEN (a path as bash exported it) names PATH, a file under ROOT (the
    test's tmp_path). Compared from ROOT's own name down: Git Bash may spell
    the same directory /tmp/... where Python wrote C:/Users/.../Temp/..."""
    rel = path.relative_to(root.parent).as_posix()
    return seen.replace("\\", "/").endswith("/" + rel)


class Sandbox:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.plugin = tmp_path / "plugin"
        self.project = tmp_path / "proj"
        self.remember = self.project / ".remember"
        self.home = tmp_path / "home"
        self.records = tmp_path / "saves"
        for d in (self.remember / "tmp", self.remember / "logs", self.home, self.records):
            d.mkdir(parents=True)
        shutil.copytree(REPO_ROOT / "scripts", self.plugin / "scripts")
        shutil.copytree(REPO_ROOT / ".claude-plugin", self.plugin / ".claude-plugin")
        shutil.copytree(REPO_ROOT / "pipeline", self.plugin / "pipeline",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(REPO_ROOT / "config.example.json", self.plugin / "config.json")
        stub = self.plugin / "scripts" / "save-session.sh"
        stub.write_bytes(("#!/bin/bash\n"
                          f'printf \'%s\\n%s\\n\' "$*" "${{REMEMBER_TRANSCRIPT_PATH:-}}"'
                          f' > "{posix(self.records)}/save.$$"\n'
                          "exit 0\n").encode())
        stub.chmod(0o755)

    def copilot_events(self, base: Path | None = None) -> Path:
        path = (base or self.home / ".copilot") / "session-state" / UUID / "events.jsonl"
        _events(path)
        return path

    def invoke(self, script: str = "copilot-post-tool-hook.sh",
               session_id=f"agent-host-copilotcli:/{UUID}", tool_input=None,
               extra_env=None) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(_STRIP_PREFIXES) and k not in _STRIP_NAMES}
        env.update({"HOME": posix(self.home), "USERPROFILE": str(self.home),
                    "CLAUDE_PLUGIN_ROOT": posix(self.plugin),
                    "CLAUDE_PROJECT_DIR": posix(self.project),
                    "REMEMBER_CONFIG_CACHE": "0", "REMEMBER_DEBUG": "1"})
        env.update(extra_env or {})
        payload = json.dumps({"hook_event_name": "PostToolUse", "session_id": session_id,
                              "cwd": str(self.project), "tool_name": "bash",
                              "tool_input": tool_input or {}, "tool_result": {}})
        return subprocess.run([BASH, posix(self.plugin / "scripts" / script)],
                              input=payload.encode(), env=env, capture_output=True, timeout=120)

    def run(self, script: str = "copilot-post-tool-hook.sh", session_id=f"agent-host-copilotcli:/{UUID}",
            tool_input=None, extra_env=None) -> str:
        r = self.invoke(script, session_id, tool_input, extra_env)
        assert r.returncode == 0, decode_bash_output(r.stderr)
        return hook_logs(self.remember / "logs")

    def stub_core(self, status: int) -> Path:
        """Replace the core post-tool-hook.sh with a stub that drains stdin,
        touches a marker, prints STUB-CORE and exits STATUS. Returns the
        marker path."""
        marker = self.tmp / "core-ran"
        (self.plugin / "scripts" / "post-tool-hook.sh").write_bytes(
            ("#!/bin/bash\ncat >/dev/null\n"
             f': > "{posix(marker)}"\n'
             f"echo STUB-CORE\nexit {status}\n").encode())
        return marker

    def saves(self) -> list[list[str]]:
        return [p.read_text(encoding="utf-8").splitlines() for p in sorted(self.records.glob("save.*"))]

    def wait_for_save(self) -> list[str]:
        deadline = time.monotonic() + POLL_DEADLINE_S
        while time.monotonic() < deadline:
            done = [s for s in self.saves() if len(s) == 2]
            if done:
                return done[0]
            time.sleep(0.1)
        raise AssertionError(f"no save within {POLL_DEADLINE_S}s\nlog:\n"
                             f"{hook_logs(self.remember / 'logs')}")


def test_copilot_transcript_is_what_the_core_saves_from(tmp_path):
    sb = Sandbox(tmp_path)
    events = sb.copilot_events()
    logs = sb.run()
    argv, transcript = sb.wait_for_save()
    # The core trusted the payload's (stripped) session id and exported the
    # synthesised path for the save.
    assert argv == UUID
    assert _same(transcript, events, tmp_path)
    assert NO_SESSION_DIR not in logs
    assert "falling back to newest" not in logs


def test_without_a_copilot_transcript_the_core_takes_its_own_fallback(tmp_path):
    """Positive control for the negatives above: same payload, no file -> no
    key is added and the core's upstream notice is what the user sees."""
    sb = Sandbox(tmp_path)
    logs = sb.run()
    assert NO_SESSION_DIR in logs
    assert sb.saves() == []


def test_the_synthesised_path_beats_a_transcript_path_inside_tool_input(tmp_path):
    """A tool call whose own input carries a `transcript_path` key (an agent
    editing a hooks config, say) must not redirect the save: the wrapper's
    key comes first, and the core reads the first occurrence."""
    sb = Sandbox(tmp_path)
    events = sb.copilot_events()
    decoy = tmp_path / "decoy.jsonl"
    _events(decoy)
    sb.run(tool_input={"transcript_path": str(decoy)})
    _argv, transcript = sb.wait_for_save()
    assert _same(transcript, events, tmp_path)


def test_the_core_alone_would_follow_the_decoy(tmp_path):
    """Positive control for the case above: without the wrapper the same
    payload's tool_input value is what the core reads, so the ordering is
    what protects the save."""
    sb = Sandbox(tmp_path)
    sb.copilot_events()
    decoy = tmp_path / "decoy.jsonl"
    _events(decoy)
    sb.run(script="post-tool-hook.sh", session_id=UUID,
           tool_input={"transcript_path": str(decoy)})
    _argv, transcript = sb.wait_for_save()
    assert _same(transcript, decoy, tmp_path)


def test_traversal_after_the_prefix_is_rejected(tmp_path):
    """`agent-host-x:/../../x` strips to `../../x`: the lookup refuses it (a
    `/`), no key is added, and the core's validator empties it. A decoy
    events file sits where that path would land (`<home>/.copilot/x/`), so a
    missing guard would resolve it rather than fail vacuously."""
    sb = Sandbox(tmp_path)
    sb.copilot_events()
    decoy = sb.home / ".copilot" / "x" / "events.jsonl"
    _events(decoy)
    before = tree(tmp_path)
    logs = sb.run(session_id="agent-host-x:/../../x")
    assert NO_SESSION_DIR in logs
    assert sb.saves() == []
    outside = created_outside(tmp_path, before, ("proj/.remember/",))
    assert outside == [], outside


def test_native_windows_copilot_home_round_trips(tmp_path):
    """COPILOT_HOME as the host passes it: on Windows a backslash path
    (observed: the Copilot hosts hand Windows paths to Git Bash). The path
    the core exports is exactly the one the wrapper found -- escaped on the
    way in, unescaped by the core (#829). On POSIX it is the plain path."""
    sb = Sandbox(tmp_path)
    base = tmp_path / "copilot-home"
    events = sb.copilot_events(base)
    sb.run(extra_env={"COPILOT_HOME": str(base)})
    _argv, transcript = sb.wait_for_save()
    assert transcript == f"{base}/session-state/{UUID}/events.jsonl"
    assert Path(transcript).resolve() == events.resolve()


@pytest.mark.skipif(os.name == "nt", reason="a backslash cannot be part of a Windows file name")
def test_a_double_backslash_in_the_path_survives(tmp_path):
    """Without the escaping, the core would read `a\\\\b` as `a\\b` and miss
    the file. POSIX only: there a backslash is an ordinary name byte."""
    sb = Sandbox(tmp_path)
    base = tmp_path / "co\\\\pilot"
    events = sb.copilot_events(base)
    sb.run(extra_env={"COPILOT_HOME": str(base)})
    _argv, transcript = sb.wait_for_save()
    assert transcript == str(events)


# --- The core's line-count file, shared by every events.jsonl (#913) -------

# Upstream #913 keeps a per-transcript line count in
# $REMEMBER_DIR/tmp/transcript-scan.<transcript basename minus .jsonl>. Every
# Copilot transcript is named events.jsonl, so all Copilot sessions of a
# project share transcript-scan.events. The core only checks that the byte at
# the stored offset is a newline, which another session's file can satisfy;
# the wrapper therefore empties that file when a different session id scans.
OTHER_UUID = "22222222-3333-4444-8555-666666666666"
SCAN_FILE = "transcript-scan.events"


def _sized_lines(size: int, count: int) -> bytes:
    """COUNT JSON lines of exactly SIZE bytes each (newline included)."""
    head, tail = b'{"t":"u","p":"', b'"}\n'
    return (head + b"x" * (size - len(head) - len(tail)) + tail) * count


def _session_events(sb: Sandbox, uuid: str, data: bytes) -> Path:
    path = sb.home / ".copilot" / "session-state" / uuid / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _scan_count(sb: Sandbox) -> str:
    return (sb.remember / "tmp" / SCAN_FILE).read_text(encoding="utf-8").strip()


def test_a_new_copilot_session_gets_its_own_line_count(tmp_path):
    """Session A leaves `500 10`; session B's file is 40 lines of 25 bytes,
    with a newline at byte 500, so the core would resume from A's offset and
    count 10 + 20 = 30. The wrapper makes it count B from the start: 40."""
    sb = Sandbox(tmp_path)
    _session_events(sb, UUID, _sized_lines(50, 10))
    sb.run(session_id=UUID)
    assert _scan_count(sb) == "500 10"  # positive control: the core wrote it
    _session_events(sb, OTHER_UUID, _sized_lines(25, 40))
    sb.run(session_id=f"agent-host-copilotcli:/{OTHER_UUID}")
    assert _scan_count(sb) == "1000 40"
    owner = (sb.remember / "tmp" / "copilot-scan-owner").read_text(encoding="utf-8")
    assert owner.strip() == OTHER_UUID


def test_the_same_session_keeps_its_incremental_count(tmp_path):
    """Control for the reset above: a second call of the SAME session must
    not empty the file. The stored count is made deliberately wrong (99
    lines at a real line boundary), so only a resumed scan reports
    99 + 5 = 104; a reset would report the true 15."""
    sb = Sandbox(tmp_path)
    events = _session_events(sb, UUID, _sized_lines(50, 10))
    sb.run(session_id=UUID)
    assert _scan_count(sb) == "500 10"
    (sb.remember / "tmp" / SCAN_FILE).write_bytes(b"500 99\n")  # LF, as the core writes it
    with events.open("ab") as f:
        f.write(_sized_lines(50, 5))
    sb.run(session_id=UUID)
    assert _scan_count(sb) == "750 104"


def test_alternating_sessions_each_count_their_own_file(tmp_path):
    """Two Copilot chats of one project taking turns (VS Code allows
    several): A, then B, then A again with 15 more lines. B leaves
    `1000 40`; A's file is then 25 lines of 50 bytes (1250 bytes, a newline
    at byte 999), so the core's sanity check accepts B's offset and, unguarded,
    would resume there and report 40 + 5 = 45. A's count must be its own
    file's: 25 lines."""
    sb = Sandbox(tmp_path)
    a = _session_events(sb, UUID, _sized_lines(50, 10))
    sb.run(session_id=UUID)
    _session_events(sb, OTHER_UUID, _sized_lines(25, 40))
    sb.run(session_id=OTHER_UUID)
    assert _scan_count(sb) == "1000 40"
    with a.open("ab") as f:
        f.write(_sized_lines(50, 15))
    sb.run(session_id=UUID)
    assert _scan_count(sb) == "1250 25"


def test_an_unusable_owner_record_costs_a_rescan_not_the_hook(tmp_path):
    """copilot-scan-owner that cannot be read or written (here a directory):
    the hook still exits 0, writes nothing about it to hook-errors.log
    (bootstrap-dirs.sh sends the hooks' stderr there, and doctor.sh reports
    that file), and the count is still B's own, from a full scan. Positive
    control for the count: the reset case above. Positive control for the
    log assertion: test_hook_stderr_reaches_hook_errors_log below, which
    shows this Sandbox's hook stderr does land in that file."""
    sb = Sandbox(tmp_path)
    (sb.remember / "tmp" / "copilot-scan-owner").mkdir()
    _session_events(sb, UUID, _sized_lines(50, 10))
    sb.run(session_id=UUID)
    _session_events(sb, OTHER_UUID, _sized_lines(25, 40))
    sb.run(session_id=OTHER_UUID)
    assert _scan_count(sb) == "1000 40"
    errors = sb.remember / "logs" / "hook-errors.log"
    assert "copilot-scan-owner" not in (errors.read_text(encoding="utf-8", errors="replace")
                                        if errors.exists() else "")


def test_hook_stderr_reaches_hook_errors_log(tmp_path):
    """Positive control for the hook-errors.log assertion above: the
    adapter's init sources bootstrap-dirs.sh, which points the wrapper's fd 2
    at logs/hook-errors.log, and the core inherits that fd. A stub core that
    writes a marker to stderr must find it in that file. If the Sandbox ever
    stops routing stderr there (REMEMBER_TRACE=1, an xtrace), this fails
    instead of the negative assertion passing vacuously."""
    sb = Sandbox(tmp_path)
    marker = "STUB-CORE-STDERR-7f3a"
    (sb.plugin / "scripts" / "post-tool-hook.sh").write_bytes(
        ("#!/bin/bash\ncat >/dev/null\n"
         f"echo {marker} >&2\nexit 0\n").encode())
    _session_events(sb, UUID, _sized_lines(50, 10))
    sb.run(session_id=UUID)
    errors = sb.remember / "logs" / "hook-errors.log"
    assert errors.is_file()
    assert marker in errors.read_text(encoding="utf-8", errors="replace")


# --- Exit status and the missing library -----------------------------------

@pytest.mark.parametrize("status", [0, 3])
def test_wrapper_exits_with_the_cores_status(tmp_path, status):
    """The core's exit code is forwarded; 0 is the control for 3. Also the
    positive control for the missing-library case below: the same stub core
    runs (marker written, its line printed) when the library is present."""
    sb = Sandbox(tmp_path)
    marker = sb.stub_core(status)
    r = sb.invoke()
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
    r = sb.invoke()
    assert r.returncode == 0, decode_bash_output(r.stderr)
    assert r.stdout == b""
    assert not marker.exists()


# --- remember_copilot_transcript_into (lib-session-id.sh) -----------------

# The no-fork lookup the wrapper and doctor.sh use: it sets
# REMEMBER_COPILOT_TRANSCRIPT in the caller's shell and returns its status
# (issue: vscode, #511). The stale value proves a miss clears it.
_LOOKUP = ('REMEMBER_COPILOT_TRANSCRIPT=stale; '
           'remember_copilot_transcript_into "$2"; rc=$?; out=$REMEMBER_COPILOT_TRANSCRIPT')


def _layout(tmp_path):
    home = tmp_path / "home"
    d = home / ".copilot" / "session-state" / UUID
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text('{"type":"user.message","data":{"content":"hi"}}\n',
                                    encoding="utf-8")
    return home


def _lib_call(tmp_path, arg, home=None, any_file_exists=False):
    home = home or (tmp_path / "home")
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    env.pop("COPILOT_HOME", None)
    env.pop("REMEMBER_COPILOT_TRANSCRIPT", None)
    lib = (REPO_ROOT / "scripts" / "lib-session-id.sh").as_posix()
    # any_file_exists shadows `[` so every -f test succeeds: only the id guard can
    # refuse then (a dir named "a\b" cannot be created on Windows filesystems).
    shadow = '[() { return 0; }; ' if any_file_exists else ''
    code = 'source "$1"; ' + shadow + _LOOKUP + '; printf "%s|%s" "$rc" "$out"'
    r = subprocess.run([BASH, "-c", code, "x", lib, arg], env=env, capture_output=True, timeout=60)
    rc, _, out = decode_bash_output(r.stdout).partition("|")
    return int(rc), out


HOSTILE = ["", ".", "..", "../x", "a/b", "a" + chr(92) + "b", ".." + chr(92) + ".." + chr(92) + "x", "a:b"]


def test_copilot_transcript_for_positive_control(tmp_path):
    home = _layout(tmp_path)
    rc, out = _lib_call(tmp_path, UUID, home)
    assert rc == 0
    assert out.replace(chr(92), "/").endswith(f"session-state/{UUID}/events.jsonl")


@pytest.mark.parametrize("hostile", HOSTILE)
def test_copilot_transcript_for_refuses_hostile_ids(tmp_path, hostile):
    home = _layout(tmp_path)
    # Decoys so a missing guard would resolve a real file rather than fail vacuously.
    ss = home / ".copilot" / "session-state"
    (ss / "events.jsonl").write_text("{}", encoding="utf-8")
    (ss.parent / "x").mkdir()
    (ss.parent / "x" / "events.jsonl").write_text("{}", encoding="utf-8")  # "../x" target
    (ss / "a").mkdir()
    (ss / "a" / "events.jsonl").write_text("{}", encoding="utf-8")  # "a/b" neighbour
    rc, out = _lib_call(tmp_path, hostile, home)
    assert rc != 0
    assert out == ""


@pytest.mark.parametrize("hostile", HOSTILE)
def test_copilot_transcript_for_guard_refuses_even_if_file_exists(tmp_path, hostile):
    """Non-vacuous: with `[` shadowed to always succeed, the guard alone
    must reject the id (a mutated guard makes this fail)."""
    rc, out = _lib_call(tmp_path, hostile, any_file_exists=True)
    assert rc != 0
    assert out == ""


def test_shadowed_test_builtin_lets_a_plain_id_through(tmp_path):
    """Positive control for the shadowed-`[` negatives."""
    rc, out = _lib_call(tmp_path, UUID, any_file_exists=True)
    assert rc == 0 and UUID in out


def test_into_clears_a_stale_value_when_absent(tmp_path):
    """The lookup empties the global on a miss and returns non-zero."""
    rc, out = _lib_call(tmp_path, UUID)  # no session-state at all
    assert rc != 0 and out == ""
    # positive control: same call with the file present fills it
    home = _layout(tmp_path)
    rc, out = _lib_call(tmp_path, UUID, home)
    assert rc == 0 and out.endswith("events.jsonl")
