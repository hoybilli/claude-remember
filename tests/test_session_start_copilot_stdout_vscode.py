"""scripts/copilot-session-start-hook.sh hands the Copilot hosts the core
session-start-hook.sh's recap as the one shape they inject, a top-level
`{"additionalContext": ...}` (issue: vscode). The core hook runs unchanged
(byte-identical to upstream); the envelope is built by
pipeline/copilot_recap.py, so no jq is needed; when Python cannot build it
the plain recap goes out and the wrapper logs it.

Every case runs the real wrapper, which runs the real core hook, in a
sandbox project with its own HOME. Promos are switched off through the config
key a user would set (`features.plugin_promos: false`) except in the
promo-on pair, which asserts only where this platform selects a promo for a
fresh HOME, and says so when it does not.

The envelope shape was observed on VS Code 1.139.1 / Windows 11 and on the
Copilot CLI on Windows and macOS (docs/vscode-verification.md); these tests
were run under Git Bash on Windows (observed); POSIX is reasoned until the
Task 8 suites.
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
from ._vscode_helpers import HOST_ENV, UUID, created_outside, hook_logs, posix, tree

REPO_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = REPO_ROOT / "scripts" / "copilot-session-start-hook.sh"
CORE = REPO_ROOT / "scripts" / "session-start-hook.sh"
BASH = resolve_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="needs a real bash")

sys.path.insert(0, str(REPO_ROOT))
from pipeline.copilot_recap import envelope  # noqa: E402

PLAIN_HEADER = "=== REMEMBER ==="
ENVELOPE_FAILED_LOG = "copilot session-start: envelope failed, plain recap"
NO_ID_LOG = "capture-gap check skipped -- no session_id on stdin"


def _sandbox(tmp_path, recent="PROBE-RECENT-LINE\n", promos=False):
    project = tmp_path / "proj"
    (project / ".remember" / "tmp").mkdir(parents=True)
    (project / ".remember" / "recent.md").write_text(recent, encoding="utf-8")
    if not promos:
        (project / ".remember" / "config.json").write_text(
            json.dumps({"features": {"plugin_promos": False}}), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    return project, home


def _env(project, home, extra=None, project_dir=True):
    env = {k: v for k, v in os.environ.items() if k not in HOST_ENV}
    for name in ("CLAUDE_PROJECT_DIR", "REMEMBER_SUPPRESS_PROMO", "REMEMBER_TRACE",
                 "REMEMBER_DIR", "PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "COPILOT_HOME"):
        env.pop(name, None)
    env.update({"HOME": str(home), "USERPROFILE": str(home),
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT)})
    if project_dir:
        env["CLAUDE_PROJECT_DIR"] = str(project)
    env.update(extra or {})
    return env


def _payload(project, session_id):
    return json.dumps({"hook_event_name": "SessionStart", "session_id": session_id,
                       "source": "new", "cwd": str(project), "timestamp": "t",
                       "initial_prompt": "PROBE PROMPT"})


def _run(tmp_path, session_id=UUID, extra_env=None, recent="PROBE-RECENT-LINE\n",
         promos=False, script=WRAPPER, project_dir=True):
    """Run SCRIPT once in a sandbox; returns (stdout bytes, logs text)."""
    project, home = _sandbox(tmp_path, recent, promos)
    r = subprocess.run([BASH, posix(script)], input=_payload(project, session_id).encode(),
                       env=_env(project, home, extra_env, project_dir),
                       capture_output=True, timeout=180)
    assert r.returncode == 0, decode_bash_output(r.stderr)
    return r.stdout, hook_logs(project / ".remember" / "logs")


def _assert_envelope(out: bytes) -> dict:
    obj = json.loads(out.decode("utf-8"))
    assert set(obj) == {"additionalContext"}  # exactly the one injected key
    assert "PROBE-RECENT-LINE" in obj["additionalContext"]
    assert "=== MEMORY ===" in obj["additionalContext"]
    return obj


def _assert_plain(out: bytes) -> None:
    text = decode_bash_output(out)
    assert text.startswith(PLAIN_HEADER), text[:200]
    assert "PROBE-RECENT-LINE" in text
    assert '"additionalContext"' not in text and '"hookSpecificOutput"' not in text


# --- The envelope ----------------------------------------------------------

def test_wrapper_prints_the_bare_envelope_and_logs_no_fallback(tmp_path):
    out, logs = _run(tmp_path)
    _assert_envelope(out)
    # Positive control for the negative: the core's own line was logged.
    assert "session-start" in logs
    assert ENVELOPE_FAILED_LOG not in logs


def test_envelope_bytes_are_copilot_recaps_and_match_jq(tmp_path):
    """Byte for byte what pipeline.copilot_recap.envelope makes of the recap
    it wrapped (2-space indent, CRLF on Windows, one trailing newline), and
    the same JSON jq -Rs builds from that recap, where jq exists."""
    out, _logs = _run(tmp_path)
    recap = _assert_envelope(out)["additionalContext"].encode("utf-8")
    assert out == envelope(recap)
    jq = shutil.which("jq")
    if jq is None:
        return
    r = subprocess.run([jq, "-Rs", "{additionalContext:.}"], input=recap,
                       capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == json.loads(out)
    assert r.stdout.replace(b"\r\n", b"\n").rstrip(b"\n") == \
        out.replace(b"\r\n", b"\n").rstrip(b"\n")


def test_core_hook_alone_prints_plain_text(tmp_path):
    """Positive control: the same payload through the core hook (what Claude
    Code runs) is the plain recap -- the envelope is the wrapper's doing."""
    out, _logs = _run(tmp_path, script=CORE)
    _assert_plain(out)


def test_prefixed_session_id_gets_the_same_envelope(tmp_path):
    _assert_envelope(_run(tmp_path, f"agent-host-copilotcli:/{UUID}")[0])


def test_non_ascii_recap_survives_the_envelope(tmp_path):
    out, _logs = _run(tmp_path, recent="PROBE-RECENT-LINE\nünï – 日本\n")
    assert "ünï – 日本" in _assert_envelope(out)["additionalContext"]


def test_trace_on_still_gets_the_envelope(tmp_path):
    """REMEMBER_TRACE=1 makes the core print its recap live instead of
    buffering it (#712); the wrapper captures stdout either way."""
    out, _logs = _run(tmp_path, extra_env={"REMEMBER_TRACE": "1"})
    _assert_envelope(out)


def test_without_jq_still_gets_the_envelope(tmp_path):
    """Python builds the envelope: a PATH with no jq changes nothing."""
    path = _path_without_jq(tmp_path)
    assert shutil.which("jq", path=path) is None  # positive control: jq hidden
    for tool in ("tr", "cat", "date"):
        assert shutil.which(tool, path=path), f"{tool} lost from the jq-less PATH"
    out, logs = _run(tmp_path, extra_env={"PATH": path})
    _assert_envelope(out)
    assert "session-start" in logs and ENVELOPE_FAILED_LOG not in logs


# --- Promos: suppressed, and no marker burned ------------------------------

@pytest.fixture(scope="module")
def core_promo(tmp_path_factory):
    """The core hook alone with promos on and a fresh HOME: returns
    (parsed output, that HOME), or skips the pair when this platform selects
    no promo for a fresh HOME -- the pair must never assert where a promo
    cannot be shown."""
    root = tmp_path_factory.mktemp("core-promo")
    out, _logs = _run(root, promos=True, script=CORE)
    text = decode_bash_output(out)
    if '"systemMessage"' not in text:
        pytest.skip("this platform selected no promo for a fresh HOME; "
                    "the promo-on pair cannot assert here")
    return json.loads(text), root / "home"


def test_promo_on_core_path_shows_the_promo_and_writes_the_marker(core_promo):
    """Positive control for the wrapper case below."""
    obj, home = core_promo
    assert obj["systemMessage"]
    assert (home / ".remember" / "tmp" / "promo-notice").is_file()


def test_promo_on_wrapper_is_the_bare_envelope_and_burns_no_marker(tmp_path, core_promo):
    out, _logs = _run(tmp_path, promos=True)
    assert b'"systemMessage"' not in out
    _assert_envelope(out)
    assert not (tmp_path / "home" / ".remember" / "tmp" / "promo-notice").exists()


# --- Python missing or failing: plain recap, logged ------------------------

_PY_NAMES = ("python3", "python", "py")


def _python_stub_dir(tmp_path, version_ok):
    """`python3` / `python` / `py` stubs to put first on PATH. Each appends
    its argv to calls.txt and exits 1, except that `-V` (the interpreter
    probe) exits 0 when VERSION_OK. Returns (dir, calls file)."""
    d = tmp_path / "python-stub"
    d.mkdir()
    calls = tmp_path / "calls.txt"
    probe = "0" if version_ok else "1"
    body = ("#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> '{posix(calls)}'\n"
            "for a in \"$@\"; do\n"
            f"    [ \"$a\" = -V ] && exit {probe}\n"
            "done\n"
            "exit 1\n")
    for name in _PY_NAMES:
        stub = d / name
        stub.write_bytes(body.encode())
        stub.chmod(0o755)
    return d, calls


def _stub_env(stub_dir):
    return {"PATH": os.pathsep.join([str(stub_dir), os.environ.get("PATH", "")]),
            "REMEMBER_TOOLS_CACHE": "0"}


def _calls(calls: Path) -> str:
    return calls.read_text(encoding="utf-8") if calls.exists() else ""


def test_without_python_the_plain_recap_goes_out_and_is_logged(tmp_path):
    stub_dir, calls = _python_stub_dir(tmp_path, version_ok=False)
    out, logs = _run(tmp_path, extra_env=_stub_env(stub_dir))
    _assert_plain(out)
    assert "-V" in _calls(calls)  # positive control: the stubs are what ran
    assert "copilot_recap" not in _calls(calls)
    assert ENVELOPE_FAILED_LOG in logs


def test_helper_failure_sends_the_plain_recap_and_logs_it(tmp_path):
    stub_dir, calls = _python_stub_dir(tmp_path, version_ok=True)
    out, logs = _run(tmp_path, extra_env=_stub_env(stub_dir))
    _assert_plain(out)
    assert "-m pipeline.copilot_recap" in _calls(calls)
    assert ENVELOPE_FAILED_LOG in logs


# --- The core prints nothing: neither does the wrapper ---------------------

def test_nothing_from_the_core_means_nothing_printed(tmp_path):
    """REMEMBER_NESTED_SUMMARIZER makes the core exit 0 at once with no
    output (a summarizer's own nested session): no empty envelope is
    invented. Positive control: every envelope case above."""
    out, _logs = _run(tmp_path, extra_env={"REMEMBER_NESTED_SUMMARIZER": "1"})
    assert out == b""


def test_without_claude_project_dir_the_core_still_decides(tmp_path):
    """The wrapper cannot resolve the store without CLAUDE_PROJECT_DIR, but
    it still delegates: the core resolves the project from the payload's
    cwd, and its recap is enveloped (temp file under $TMPDIR)."""
    out, _logs = _run(tmp_path, project_dir=False,
                      extra_env={"TMPDIR": posix(tmp_path)})
    _assert_envelope(out)


# --- The core's validator still runs after the strip (end to end) ----------

def test_traversal_after_the_prefix_is_rejected_by_the_core(tmp_path):
    """`agent-host-x:/../../x` strips to `../../x`, which the core's own
    validator empties: exit 0, the no-session-id line, nothing written
    outside the sandbox store."""
    before = tree(tmp_path)
    out, logs = _run(tmp_path, "agent-host-x:/../../x")
    _assert_envelope(out)
    assert NO_ID_LOG in logs
    outside = created_outside(tmp_path, before, ("proj/.remember/",),
                              exact=("proj", "proj/.remember", "home"))
    assert outside == [], outside
    assert not (tmp_path / "x").exists()


def test_prefixed_uuid_reaches_the_core_as_a_session_id(tmp_path):
    """Positive control for the rejection above."""
    _out, logs = _run(tmp_path, f"agent-host-copilotcli:/{UUID}")
    assert "session-start" in logs
    assert NO_ID_LOG not in logs


# --- Exit status -------------------------------------------------------------

def _plugin_with_stub_core(tmp_path, status):
    """A copy of the plugin whose session-start-hook.sh prints one line and
    exits STATUS."""
    plugin = tmp_path / "plugin"
    shutil.copytree(REPO_ROOT / "scripts", plugin / "scripts")
    shutil.copytree(REPO_ROOT / ".claude-plugin", plugin / ".claude-plugin")
    shutil.copytree(REPO_ROOT / "pipeline", plugin / "pipeline",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (plugin / "scripts" / "session-start-hook.sh").write_bytes(
        f"#!/bin/bash\ncat >/dev/null\necho STUB-RECAP\nexit {status}\n".encode())
    return plugin


@pytest.mark.parametrize("status", [0, 3])
def test_wrapper_exits_with_the_cores_status(tmp_path, status):
    """0 is the control for 3. The stub's line is still enveloped."""
    plugin = _plugin_with_stub_core(tmp_path, status)
    project, home = _sandbox(tmp_path)
    env = _env(project, home, {"CLAUDE_PLUGIN_ROOT": str(plugin)})
    r = subprocess.run([BASH, posix(plugin / "scripts" / "copilot-session-start-hook.sh")],
                       input=_payload(project, UUID).encode(), env=env,
                       capture_output=True, timeout=120)
    assert r.returncode == status, decode_bash_output(r.stderr)
    assert json.loads(r.stdout) == {"additionalContext": "STUB-RECAP\n"}


def test_missing_adapter_library_exits_0_without_delegating(tmp_path):
    """A wrapper-side failure: lib-copilot-adapter.sh is gone from the
    plugin copy. The wrapper exits 0 and prints nothing, and the stub core
    never ran (its STUB-RECAP line is absent). Positive control: the
    stub-core status cases above, where the same stub's line comes out."""
    plugin = _plugin_with_stub_core(tmp_path, 3)
    (plugin / "scripts" / "lib-copilot-adapter.sh").unlink()
    project, home = _sandbox(tmp_path)
    env = _env(project, home, {"CLAUDE_PLUGIN_ROOT": str(plugin)})
    r = subprocess.run([BASH, posix(plugin / "scripts" / "copilot-session-start-hook.sh")],
                       input=_payload(project, UUID).encode(), env=env,
                       capture_output=True, timeout=120)
    assert r.returncode == 0, decode_bash_output(r.stderr)
    assert b"STUB-RECAP" not in r.stdout
    assert r.stdout == b""


# --- PATH without jq (kept from the inline port's module) -------------------

_JQ_NAMES = ("jq", "jq.exe")


def _holds(directory, names):
    return any(os.path.isfile(os.path.join(directory, n)) for n in names)


def _path_without_jq(tmp_path):
    """PATH with jq hidden and every other tool left in place: a directory
    holding jq is replaced, at the same position, by a mirror of symlinks to
    everything in it except jq (dropping it would also drop `tr` on macOS,
    where jq is /usr/bin/jq). Windows: a jq directory without `cat` (a
    Chocolatey/Scoop/WinGet shim directory) is dropped; otherwise the mirror
    needs symlinks, and the test skips when they are refused."""
    kept = []
    for n, d in enumerate(os.environ.get("PATH", "").split(os.pathsep)):
        if not d or not _holds(d, _JQ_NAMES):
            kept.append(d)
            continue
        if os.name == "nt" and not _holds(d, ("cat", "cat.exe")):
            continue
        mirror = tmp_path / f"nojq-{n}"
        mirror.mkdir()
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            if name in _JQ_NAMES:
                continue
            try:
                os.symlink(os.path.join(os.path.abspath(d), name), mirror / name)
            except FileExistsError:
                continue
            except OSError as exc:
                if os.name == "nt":
                    pytest.skip(f"cannot symlink to mirror a PATH directory: {exc}")
                continue
        kept.append(str(mirror))
    return os.pathsep.join(kept)
