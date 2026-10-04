"""session-start-hook.sh wraps its memory recap in the JSON envelope VS Code
Agents injects (top-level `additionalContext`) when the host is Copilot, and
leaves Claude Code's shape alone otherwise (issue: vscode). Claude Code's shape
is the plain-text recap when no promo is due, and the promo envelope
(`hookSpecificOutput` + `systemMessage`) when one is; the Copilot host never
gets a promo.

Promos are switched off for the plain-text cases through the config key a user
would set (`features.plugin_promos: false` in the sandbox store's config.json),
not an environment variable, so the control is the same on every platform. The
promo-on pair below asserts only where this platform actually selects a promo
for a fresh HOME, and says so when it does not.

The envelope shape was observed on VS Code 1.139.1 / Windows 11 only;
macOS/Linux VS Code is reasoned.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ._bash_runner import decode_bash_output, resolve_bash

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "scripts" / "session-start-hook.sh"
BASH = resolve_bash()
pytestmark = pytest.mark.skipif(
    BASH is None or shutil.which("jq") is None, reason="needs a real bash and jq")

sys.path.insert(0, str(REPO_ROOT))
from pipeline import host as _host  # noqa: E402

UUID = "11111111-2222-4333-8444-555555555555"
PLAIN_HEADER = "=== REMEMBER ==="

# The suite runs inside a Claude Code session (or a Copilot / Codex shell):
# strip every host signature the registry knows, plus the hint the hooks
# export, then add back only what a case names. Read from the registry, so a
# new host row cannot leave its signature in the inherited env.
_HOST_ENV = tuple(dict.fromkeys(
    tuple(v for h in _host.REGISTRY for v in h.signature_vars) + ("REMEMBER_HOST_HINT",)))


def _run(tmp_path, session_id, extra_env=None, recent="PROBE-RECENT-LINE\n", promos=False):
    """Run the real hook in a sandbox; returns (stdout, logs)."""
    project = tmp_path / "proj"
    (project / ".remember" / "tmp").mkdir(parents=True)
    (project / ".remember" / "recent.md").write_text(recent, encoding="utf-8")
    if not promos:
        (project / ".remember" / "config.json").write_text(
            json.dumps({"features": {"plugin_promos": False}}), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in _HOST_ENV}
    env.update({"HOME": str(home), "USERPROFILE": str(home),
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "REMEMBER_DIR": str(project / ".remember")})
    for name in ("CLAUDE_PROJECT_DIR", "REMEMBER_SUPPRESS_PROMO", "REMEMBER_TRACE"):
        env.pop(name, None)
    env.update(extra_env or {})
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": session_id,
                          "source": "startup", "cwd": str(project)})
    r = subprocess.run([BASH, HOOK.as_posix()], input=payload.encode(), env=env,
                       capture_output=True, timeout=180)
    assert r.returncode == 0, decode_bash_output(r.stderr)
    return decode_bash_output(r.stdout)


def _logs(tmp_path):
    return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in (tmp_path / "proj" / ".remember" / "logs").glob("*.log"))


def _assert_envelope(out):
    obj = json.loads(out)
    # Exact shape: the one top-level key VS Code was observed to inject.
    assert set(obj) == {"additionalContext"}
    assert "PROBE-RECENT-LINE" in obj["additionalContext"]
    assert "=== MEMORY ===" in obj["additionalContext"]
    return obj


def _assert_plain(out):
    """Claude Code's no-promo shape: the recap itself, unwrapped."""
    assert out.startswith(PLAIN_HEADER), out[:200]
    assert "=== MEMORY ===" in out and "PROBE-RECENT-LINE" in out
    assert '"additionalContext"' not in out, out[:200]
    assert '"hookSpecificOutput"' not in out, out[:200]


def test_copilot_env_gets_top_level_additional_context(tmp_path):
    _assert_envelope(_run(tmp_path, UUID, {"COPILOT_CLI": "1"}))


def test_prefixed_session_id_gets_the_same_envelope(tmp_path):
    _assert_envelope(_run(tmp_path, f"agent-host-copilotcli:/{UUID}"))


def test_plain_uuid_keeps_plain_text(tmp_path):
    """Positive control: Claude Code's stdout shape is unchanged."""
    _assert_plain(_run(tmp_path, UUID))


def test_claude_code_signature_beats_copilot_env(tmp_path):
    _assert_plain(_run(tmp_path, UUID, {"COPILOT_CLI": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"}))


# --- Promos on: the Copilot branch skips a promo that is really due ---------

def _claude_promo_or_skip(tmp_path):
    """Claude Code path with promos on and a fresh HOME. Returns its parsed
    output, or skips when this platform selects no promo at all -- the pair
    must never assert where a promo cannot be shown."""
    out = _run(tmp_path / "claude", UUID, promos=True)
    if '"systemMessage"' not in out:
        pytest.skip("this platform selected no promo for a fresh HOME "
                    "(promo gates read as unknown); the promo-on pair cannot assert here")
    return json.loads(out)


def test_promo_on_claude_code_path_shows_the_promo(tmp_path):
    """Positive control for the copilot case below: with promos on, Claude
    Code's output is the promo envelope."""
    obj = _claude_promo_or_skip(tmp_path)
    assert obj["systemMessage"]
    assert obj["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_promo_on_copilot_path_is_the_bare_envelope(tmp_path):
    """Same conditions, Copilot host: exactly `{"additionalContext": ...}`, no
    `systemMessage` (the promo is skipped, not merged in)."""
    _claude_promo_or_skip(tmp_path)
    out = _run(tmp_path / "copilot", UUID, {"COPILOT_CLI": "1"}, promos=True)
    assert '"systemMessage"' not in out
    _assert_envelope(out)


# --- Plain-text fallbacks on the copilot host are logged --------------------

_NO_JQ_LOG = "copilot host without jq,"
_ENVELOPE_FAILED_LOG = "copilot envelope could not be built"
_UNBUFFERED_LOG = "copilot host, recap not buffered"


def _path_without_jq():
    """PATH minus every directory holding jq, or None when jq shares a
    directory with the coreutils the hook needs (typical on Linux/macOS,
    where jq is in /usr/bin) -- it cannot be hidden there."""
    kept = []
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if d and any(os.path.isfile(os.path.join(d, n)) for n in ("jq", "jq.exe")):
            if any(os.path.isfile(os.path.join(d, n)) for n in ("cat", "cat.exe")):
                return None
            continue
        kept.append(d)
    return os.pathsep.join(kept)


def test_copilot_without_jq_logs_why_nothing_is_injected(tmp_path):
    path = _path_without_jq()
    if path is None:
        pytest.skip("jq shares a PATH directory with coreutils; cannot hide it")
    out = _run(tmp_path, UUID, {"COPILOT_CLI": "1", "PATH": path})
    _assert_plain(out)
    assert _NO_JQ_LOG in _logs(tmp_path)


def test_copilot_with_jq_does_not_log_any_fallback_line(tmp_path):
    """Negative half; the positives are the fallback cases around it. Also
    asserts the log was written at all, so an empty log cannot pass this
    vacuously."""
    _assert_envelope(_run(tmp_path, UUID, {"COPILOT_CLI": "1"}))
    logs = _logs(tmp_path)
    assert "session-start" in logs
    for line in (_NO_JQ_LOG, _ENVELOPE_FAILED_LOG, _UNBUFFERED_LOG):
        assert line not in logs


def _failing_jq_dir(tmp_path):
    """A directory holding a `jq` that exits non-zero, to put first on PATH."""
    d = tmp_path / "failing-jq"
    d.mkdir()
    stub = d / "jq"
    stub.write_bytes(b"#!/bin/sh\nexit 3\n")
    stub.chmod(0o755)
    return d


def test_copilot_jq_failure_falls_back_to_plain_and_logs_it(tmp_path):
    """The envelope's own jq call fails: the recap still goes out (plain), and
    the log says why the host will not inject it."""
    path = os.pathsep.join([str(_failing_jq_dir(tmp_path)), os.environ.get("PATH", "")])
    out = _run(tmp_path, UUID, {"COPILOT_CLI": "1", "PATH": path,
                                "REMEMBER_TOOLS_CACHE": "0"})
    assert "PROBE-RECENT-LINE" in out and '"additionalContext"' not in out
    logs = _logs(tmp_path)
    assert _ENVELOPE_FAILED_LOG in logs
    assert _NO_JQ_LOG not in logs


def test_copilot_unbuffered_recap_is_logged(tmp_path):
    """REMEMBER_TRACE=1 skips the context buffer (the #712 trace path), so the
    recap is printed live and cannot be wrapped: logged on the copilot host."""
    out = _run(tmp_path, UUID, {"COPILOT_CLI": "1", "REMEMBER_TRACE": "1"})
    assert "PROBE-RECENT-LINE" in out and '"additionalContext"' not in out
    assert _UNBUFFERED_LOG in _logs(tmp_path)


def test_claude_code_unbuffered_recap_is_not_logged(tmp_path):
    """Paired control: the same trace path on Claude Code (plain text is
    what it injects) logs nothing about it."""
    out = _run(tmp_path, UUID, {"REMEMBER_TRACE": "1"})
    assert "PROBE-RECENT-LINE" in out
    logs = _logs(tmp_path)
    assert "session-start" in logs
    assert _UNBUFFERED_LOG not in logs


def test_non_ascii_recap_survives_the_envelope(tmp_path):
    out = _run(tmp_path, UUID, {"COPILOT_CLI": "1"},
               recent="PROBE-RECENT-LINE\nünï – 日本\n")
    assert "ünï – 日本" in _assert_envelope(out)["additionalContext"]


# --- The validator still runs after the prefix strip (end to end) ----------

_NO_ID_LOG = "capture-gap check skipped -- no session_id on stdin"


def _tree(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


def test_traversal_after_the_prefix_is_rejected_by_the_hook(tmp_path):
    """`agent-host-x:/../../x` strips to `../../x`, which the hook's own
    validator must empty: the hook exits 0, says it had no usable session id,
    and writes nothing outside the sandbox store."""
    before = _tree(tmp_path)
    out = _run(tmp_path, "agent-host-x:/../../x")
    assert "PROBE-RECENT-LINE" in out
    assert _NO_ID_LOG in _logs(tmp_path)
    new = _tree(tmp_path) - before
    outside = sorted(p for p in new if p not in ("proj", "proj/.remember", "home")
                     and not p.startswith("proj/.remember/"))
    assert outside == [], outside
    assert not (tmp_path / "x").exists()


def test_prefixed_uuid_reaches_the_hook_as_a_session_id(tmp_path):
    """Positive control for the rejection above: a valid prefixed id is kept."""
    _run(tmp_path, f"agent-host-copilotcli:/{UUID}")
    logs = _logs(tmp_path)
    assert "session-start" in logs
    assert _NO_ID_LOG not in logs
