"""doctor.sh must not call a VS Code Agents-only project a #144 slug mismatch
(issue: vscode).

The verdict ladder's "capture is working" arm (#880) and the summarizer arm
above it (#870) are guarded by ``{ -z SESSION_DIR || -d SESSION_DIR }``:
Claude Code's own transcript directory for the project must exist, or the
ladder falls through to "session dir slug does not match Claude Code's
transcript directory (#144); restarting will not help". A project that has
only ever been driven through VS Code Agents (or the Copilot CLI) never gets
that directory -- its transcript is ``~/.copilot/session-state/<uuid>/
events.jsonl`` -- so a healthy project with real saves was told it is broken
in a way a restart cannot fix.

The ruling: Claude Code's session directory is evidence only when the last
successful save came from a Claude Code session. When the session id recorded
in ``last-save.json`` names an existing Copilot events file, the clause is
satisfied. Every "must not be #144" case below is paired with the same
fixture minus the Copilot transcript, which must still reach #144 unchanged:
an arm that stopped emitting #144 altogether would otherwise pass them all.

Runs under Git Bash on Windows via ``resolve_bash()`` -- not blanket-skipped
like most doctor modules -- because this host is Windows-first.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ._bash_runner import decode_bash_output, resolve_bash

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCTOR = REPO_ROOT / "scripts" / "doctor.sh"
BASH = resolve_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="no POSIX bash found (Git Bash on Windows)")

sys.path.insert(0, str(REPO_ROOT))

from pipeline.slug import session_dir_slug as _slug  # noqa: E402

UUID = "11111111-2222-4333-8444-555555555555"
VERDICT_144 = (
    "VERDICT: problem -- session dir slug does not match Claude Code's "
    "transcript directory (#144); restarting will not help"
)
VERDICT_870 = "VERDICT: problem -- the summarizer's last attempt failed"
NEW_OK = "OK   last save came from a VS Code Agents / Copilot session ("
NEW_OK_TAIL = "); Claude Code's transcript dir is not expected (issue: vscode)"


def _project(tmp_path: Path, *, claude_dir: bool, copilot: str | None,
             summary_failure: bool = False):
    """copilot: None (no transcript), "home" (under HOME/.copilot) or
    "copilot_home" (under a separate COPILOT_HOME)."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    remember = project / ".remember"
    tmp = remember / "tmp"
    tmp.mkdir(parents=True)
    home.mkdir(parents=True, exist_ok=True)
    if claude_dir:
        (home / ".claude" / "projects" / _slug(str(project))).mkdir(parents=True)
    # A healthy capture as the Copilot route leaves it: the hook ran, found the
    # transcript (capture-alive), and a save completed for that session id.
    (tmp / "post-tool-ran").write_text("", encoding="utf-8")
    (tmp / "capture-alive").write_text(UUID, encoding="utf-8")
    (tmp / "last-save.json").write_text(
        '{"session": "%s", "line": 42}' % UUID, encoding="utf-8")
    if summary_failure:
        (tmp / "last-summary-failure").write_text("1\n", encoding="utf-8")
    copilot_home = None
    if copilot == "home":
        copilot_home = home / ".copilot"
    elif copilot == "copilot_home":
        copilot_home = tmp_path / "elsewhere-copilot"
    if copilot_home is not None:
        d = copilot_home / "session-state" / UUID
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text(
            '{"type":"user.message","data":{"content":"hi"}}\n', encoding="utf-8")
    return home, project, remember, copilot_home


def _run(home: Path, project: Path, remember: Path,
         extra_env: dict[str, str] | None = None) -> str:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_CODE_", "COPILOT_", "REMEMBER_", "CODEX_",
                                "ANTIGRAVITY_"))}
    for k in ("CLAUDE_CONFIG_DIR", "CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT"):
        env.pop(k, None)
    env.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "CLAUDE_PROJECT_DIR": str(project),
        "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
        "REMEMBER_DIR": str(remember),
        # lib-memory-dir.sh's reentrancy guard -- keeps the run off the real
        # config-merge path, as the other doctor modules do.
        "_LIB_MEMORY_DIR_LOADED": "1",
    })
    env.update(extra_env or {})
    r = subprocess.run([BASH, DOCTOR.as_posix()], env=env,
                       capture_output=True, timeout=300)
    assert r.returncode == 0, decode_bash_output(r.stderr)
    return decode_bash_output(r.stdout)


def _verdict(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.startswith("VERDICT:"):
            return line
    raise AssertionError("no VERDICT line in output:\n" + stdout)


def _new_ok_lines(stdout: str) -> list[str]:
    return [l for l in stdout.splitlines() if l.startswith(NEW_OK)]


def test_copilot_only_project_with_saves_is_capture_working(tmp_path):
    """The reported shape: no Claude Code session dir at all, a save recorded
    for a session whose Copilot transcript exists. Before the fix this read
    #144 and told a healthy user restarting would not help."""
    home, project, remember, _ = _project(tmp_path, claude_dir=False, copilot="home")

    out = _run(home, project, remember)

    assert _verdict(out).startswith("VERDICT: capture is working"), out
    lines = _new_ok_lines(out)
    assert len(lines) == 1, out
    assert lines[0].endswith(NEW_OK_TAIL), out
    assert f"session-state/{UUID}/events.jsonl" in lines[0].replace("\\", "/"), out


def test_without_the_copilot_transcript_the_144_verdict_is_unchanged(tmp_path):
    """Positive control for the case above: same fixture, no Copilot events
    file. A missing Claude Code session dir with nothing else to explain it is
    still #144, word for word."""
    home, project, remember, _ = _project(tmp_path, claude_dir=False, copilot=None)

    out = _run(home, project, remember)

    assert _verdict(out) == VERDICT_144, out
    assert _new_ok_lines(out) == [], out


def test_claude_code_project_is_still_capture_working(tmp_path):
    """Control: the ordinary Claude Code shape (session dir present, no Copilot
    transcript) reads exactly as before, and does not claim a Copilot save."""
    home, project, remember, _ = _project(tmp_path, claude_dir=True, copilot=None)

    out = _run(home, project, remember)

    assert "OK   Session dir exists" in out, (
        "fixture error: the Claude Code session dir was not found by doctor, "
        "so this control is not testing the shape it names:\n" + out)
    assert _verdict(out).startswith("VERDICT: capture is working"), out
    assert _new_ok_lines(out) == [], out


def test_copilot_home_is_honoured(tmp_path):
    """The transcript lives under COPILOT_HOME, not HOME/.copilot -- the same
    lookup the hooks use, so doctor must agree with them about where it is."""
    home, project, remember, copilot_home = _project(
        tmp_path, claude_dir=False, copilot="copilot_home")

    out = _run(home, project, remember, {"COPILOT_HOME": str(copilot_home)})

    assert _verdict(out).startswith("VERDICT: capture is working"), out
    assert len(_new_ok_lines(out)) == 1, out


def test_copilot_only_project_with_a_failing_summarizer_gets_the_870_verdict(tmp_path):
    """The #870 arm carries the same guard; a Copilot-only project whose
    summarizer is failing must hear about the summarizer, not #144."""
    home, project, remember, _ = _project(
        tmp_path, claude_dir=False, copilot="home", summary_failure=True)

    out = _run(home, project, remember)

    assert _verdict(out).startswith(VERDICT_870), out


def test_failing_summarizer_without_copilot_transcript_is_still_144(tmp_path):
    """Positive control for the #870 case above: no Copilot transcript, so the
    structural #144 cause still outranks the summarizer, as #870 intended."""
    home, project, remember, _ = _project(
        tmp_path, claude_dir=False, copilot=None, summary_failure=True)

    out = _run(home, project, remember)

    assert _verdict(out) == VERDICT_144, out
