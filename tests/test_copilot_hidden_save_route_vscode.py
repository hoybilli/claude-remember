"""The Copilot PostToolUse save on Windows goes through upstream's hidden
launcher (#1002) (issue: vscode).

Since v0.42.3 the core post-tool-hook.sh starts its save on Windows with
`wscript.exe //B windows-hidden-run.vbs ...` in the foreground, under a 15 s
watchdog, and falls back to the old `nohup ... &` when that fails
(scripts/lib-detach.sh). The Copilot hosts start the wrapper through
scripts/run-hook.ps1, so these cases run the full manifest `powershell` value
for copilot-post-tool-hook.sh, as the hosts do, against a sandbox copy of the
plugin, and pin:

- the launcher returns long before a slow save finishes, on the hidden route
  and on the nohup fallback, and the save still runs to the end;
- which route ran, read from what the save sees: wscript.exe is a native
  program, so Git Bash rewrites a POSIX-looking value such as /c/route-probe
  to C:/route-probe on the way to it, and the save inherits the rewritten
  value; the nohup child keeps /c/route-probe;
- the REAL save-session.sh, started over the hidden route with those C:/
  values, resolves the same .remember/ and reads the Copilot transcript as
  it does over nohup (it stops at the human-message gate, before any
  summarizer call).

The fallback is forced by removing windows-hidden-run.vbs from the sandbox
copy (lib-detach.sh returns 1 without it). Each case has its own TMPDIR, so
the route's failure cache (`$TMPDIR/remember-detach-windows-cache`) never
touches the developer's own. Windows only: no other platform takes this route
(read from lib-detach.sh). Observed on Windows 11 with Git Bash 5.2.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from ._vscode_helpers import UUID, hook_logs, posix
from .test_hooks_json import _launcher_env, _needs_win_launcher, _run_launcher

pytestmark = _needs_win_launcher

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = "copilot-post-tool-hook.sh"
SLOW_SAVE_S = 15
RETURN_BOUND_S = SLOW_SAVE_S - 5
PROBE = "/c/route-probe"
HIDDEN_PROBE = "C:/route-probe"
CACHE = "remember-detach-windows-cache"
# More lines than the core's default delta threshold (50): one call saves.
EVENT_LINE = b'{"type":"user.message","data":{"content":"hi"}}\n'
EVENT_LINES = 60
_STRIP_PREFIXES = ("CLAUDE_CODE_", "COPILOT_", "CODEX_", "ANTIGRAVITY_", "REMEMBER_")
_STRIP_NAMES = ("CLAUDE_PROJECT_DIR", "PLUGIN_ROOT", "HOME", "USERPROFILE", "TMPDIR")


class Plugin:
    """A sandbox plugin, project, HOME and TMPDIR, plus the Copilot
    transcript of session UUID. All of them sit under a directory whose
    name has a space, as a Windows profile path often does."""

    def __init__(self, tmp_path: Path, hidden: bool = True, real_save: bool = False):
        tmp_path = tmp_path / "a space"
        self.root = tmp_path / "plugin"
        self.project = tmp_path / "proj"
        self.remember = self.project / ".remember"
        self.home = tmp_path / "home"
        self.tmpdir = tmp_path / "tmpdir"
        self.marker = tmp_path / "save-marker.txt"
        for d in (self.remember / "tmp", self.remember / "logs", self.home, self.tmpdir):
            d.mkdir(parents=True)
        shutil.copytree(REPO_ROOT / "scripts", self.root / "scripts")
        shutil.copytree(REPO_ROOT / ".claude-plugin", self.root / ".claude-plugin")
        shutil.copytree(REPO_ROOT / "pipeline", self.root / "pipeline",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(REPO_ROOT / "config.example.json", self.root / "config.json")
        if not hidden:
            (self.root / "scripts" / "windows-hidden-run.vbs").unlink()
        if real_save:
            # Stop the real save at the human-message gate: extract runs,
            # the summarizer is never called.
            (self.remember / "config.json").write_text(json.dumps(
                {"thresholds": {"min_human_messages": 1000,
                                "min_exchanges_without_human": 1000}}), encoding="utf-8")
        else:
            (self.root / "scripts" / "save-session.sh").write_bytes((
                "#!/bin/bash\n"
                f'm="{posix(self.marker)}"\n'
                'printf "probe=%s\\nstarted\\n" "${REMEMBER_ROUTE_PROBE:-}" >> "$m"\n'
                f"sleep {SLOW_SAVE_S}\n"
                'printf "finished\\n" >> "$m"\n'
                "exit 0\n").encode())
        events = self.home / ".copilot" / "session-state" / UUID / "events.jsonl"
        events.parent.mkdir(parents=True)
        events.write_bytes(EVENT_LINE * EVENT_LINES)

    def launch(self):
        """The manifest value, timed until the caller sees stdout close."""
        # The developer's shell (often a Claude Code or Copilot session) must
        # not leak a host signature, a project or a REMEMBER_* setting in.
        env = {k: v for k, v in _launcher_env(self.root).items()
               if not k.upper().startswith(_STRIP_PREFIXES) and k.upper() not in _STRIP_NAMES}
        env.update({"HOME": posix(self.home), "USERPROFILE": str(self.home),
                    "CLAUDE_PROJECT_DIR": posix(self.project), "TMPDIR": posix(self.tmpdir),
                    "REMEMBER_CONFIG_CACHE": "0", "REMEMBER_DEBUG": "1",
                    "REMEMBER_ROUTE_PROBE": PROBE})
        payload = json.dumps({"hook_event_name": "PostToolUse",
                              "session_id": f"agent-host-copilotcli:/{UUID}",
                              "cwd": str(self.project), "tool_name": "bash",
                              "tool_input": {}, "tool_result": {}}).encode()
        started = time.monotonic()
        result = _run_launcher(self.root, SCRIPT, env, manifest=True, payload=payload)
        return result, time.monotonic() - started

    def cache_verdict(self) -> str:
        cache = self.tmpdir / CACHE
        if not cache.is_file():
            return ""
        for line in cache.read_text(encoding="utf-8").splitlines():
            if line.startswith("VERDICT="):
                return line.split("=", 1)[1]
        return ""

    def marker_lines(self, deadline_s: float) -> list[str]:
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            if self.marker.is_file() and "finished" in self.marker.read_text(encoding="utf-8"):
                break
            time.sleep(0.2)
        return self.marker.read_text(encoding="utf-8").split() if self.marker.is_file() else []

    def logs_when(self, needle: str, deadline_s: float) -> str:
        deadline = time.monotonic() + deadline_s
        logs = ""
        while time.monotonic() < deadline:
            logs = hook_logs(self.remember / "logs")
            if needle in logs:
                break
            time.sleep(0.2)
        return logs


def _skip_if_the_watchdog_fired(plugin: Plugin) -> None:
    if plugin.cache_verdict() == "slow":
        pytest.skip("wscript.exe outlived lib-detach.sh's 15 s watchdog on this run "
                    "(a loaded host); the nohup fallback ran instead")


def test_hidden_route_returns_before_a_slow_save_finishes(tmp_path):
    plugin = Plugin(tmp_path, hidden=True)
    result, took = plugin.launch()
    _skip_if_the_watchdog_fired(plugin)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert took < RETURN_BOUND_S, f"the launcher held the caller for {took:.1f}s"
    lines = plugin.marker_lines(SLOW_SAVE_S + 30)
    assert lines == [f"probe={HIDDEN_PROBE}", "started", "finished"], lines
    assert plugin.cache_verdict() == ""  # nothing failed, nothing cached


def test_nohup_fallback_returns_before_a_slow_save_finishes(tmp_path):
    """Control for the route witness above: without the VBS the core falls
    back to nohup, the save still runs to the end, the launcher still returns
    at once, and the save sees the probe unrewritten."""
    plugin = Plugin(tmp_path, hidden=False)
    result, took = plugin.launch()
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert took < RETURN_BOUND_S, f"the launcher held the caller for {took:.1f}s"
    lines = plugin.marker_lines(SLOW_SAVE_S + 30)
    assert lines == [f"probe={PROBE}", "started", "finished"], lines
    assert plugin.cache_verdict() == "unusable"  # lib-detach.sh saw no VBS


GATE_LINE = "human msgs < 1000, skip"


def _real_save(tmp_path, hidden: bool) -> str:
    plugin = Plugin(tmp_path, hidden=hidden, real_save=True)
    result, _took = plugin.launch()
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    if hidden:
        _skip_if_the_watchdog_fired(plugin)
    logs = plugin.logs_when(GATE_LINE, 90)
    assert GATE_LINE in logs, logs
    return logs


def _save_line(logs: str) -> str:
    return next(line for line in logs.splitlines() if "save-session: PROJECT_DIR=" in line)


def test_real_save_over_the_hidden_route_reads_the_copilot_transcript(tmp_path):
    """U2 of the v0.43.1 survey: the real save-session.sh, given C:/ roots by
    the hidden route, writes to this project's .remember/ (where the log is
    read from) and extracts the Copilot transcript's 60 messages."""
    logs = _real_save(tmp_path, hidden=True)
    assert "PIPELINE_DIR=C:/" in _save_line(logs), _save_line(logs)
    assert f"{EVENT_LINES} exchanges ({EVENT_LINES} human)" in logs, logs
    assert "FAILED at line" not in logs and "ERROR" not in logs, logs


def test_real_save_over_nohup_reads_the_same_transcript(tmp_path):
    """Control: the same save over the nohup fallback, POSIX roots."""
    logs = _real_save(tmp_path, hidden=False)
    assert "PIPELINE_DIR=/" in _save_line(logs), _save_line(logs)
    assert f"{EVENT_LINES} exchanges ({EVENT_LINES} human)" in logs, logs
    assert "FAILED at line" not in logs and "ERROR" not in logs, logs
