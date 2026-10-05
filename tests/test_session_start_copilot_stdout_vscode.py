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

The envelope is built by pipeline/copilot_recap.py (Python), called once from
the hook when its cheap Copilot trigger fires, so it no longer needs jq; when
that helper cannot run, the plain recap goes out and the hook logs it.
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
from ._vscode_helpers import HOST_ENV, UUID, created_outside, hook_logs, tree

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "scripts" / "session-start-hook.sh"
BASH = resolve_bash()
pytestmark = pytest.mark.skipif(
    BASH is None or shutil.which("jq") is None, reason="needs a real bash and jq")

PLAIN_HEADER = "=== REMEMBER ==="


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
    env = {k: v for k, v in os.environ.items() if k not in HOST_ENV}
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
    return hook_logs(tmp_path / "proj" / ".remember" / "logs")


# The jq-less fallback line the hook logged before Python built the envelope;
# asserted absent now that the envelope no longer needs jq.
_NO_JQ_LOG = "copilot host without jq,"
_ENVELOPE_FAILED_LOG = "session-start: copilot envelope failed, plain recap"
_UNBUFFERED_LOG = "copilot host, recap not buffered"


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
    """Also logs no fallback line; the positives are the fallback cases
    below. The log was written at all, so an empty log cannot pass the
    negatives vacuously."""
    _assert_envelope(_run(tmp_path, UUID, {"COPILOT_CLI": "1"}))
    logs = _logs(tmp_path)
    assert "session-start" in logs
    for line in (_NO_JQ_LOG, _ENVELOPE_FAILED_LOG, _UNBUFFERED_LOG):
        assert line not in logs


def test_prefixed_session_id_gets_the_same_envelope(tmp_path):
    _assert_envelope(_run(tmp_path, f"agent-host-copilotcli:/{UUID}"))


def test_plain_uuid_keeps_plain_text(tmp_path):
    """Positive control: Claude Code's stdout shape is unchanged."""
    _assert_plain(_run(tmp_path, UUID))


def test_claude_code_signature_beats_copilot_env(tmp_path):
    _assert_plain(_run(tmp_path, UUID, {"COPILOT_CLI": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"}))


# --- Promos on: the Copilot branch skips a promo that is really due ---------

@pytest.fixture(scope="module")
def claude_promo(tmp_path_factory):
    """Claude Code path with promos on and a fresh HOME, run once for the
    module's three promo cases. Returns its parsed output, or skips them all
    when this platform selects no promo at all -- the pair must never assert
    where a promo cannot be shown."""
    out = _run(tmp_path_factory.mktemp("claude-promo"), UUID, promos=True)
    if '"systemMessage"' not in out:
        pytest.skip("this platform selected no promo for a fresh HOME "
                    "(promo gates read as unknown); the promo-on pair cannot assert here")
    return json.loads(out)


def test_promo_on_claude_code_path_shows_the_promo(claude_promo):
    """Positive control for the copilot cases below: with promos on, Claude
    Code's output is the promo envelope."""
    obj = claude_promo
    assert obj["systemMessage"]
    assert obj["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_promo_on_copilot_path_is_the_bare_envelope(tmp_path, claude_promo):
    """Same conditions, Copilot host: exactly `{"additionalContext": ...}`, no
    `systemMessage` (the promo is skipped, not merged in)."""
    out = _run(tmp_path / "copilot", UUID, {"COPILOT_CLI": "1"}, promos=True)
    assert '"systemMessage"' not in out
    _assert_envelope(out)


# --- Plain-text fallbacks on the copilot host are logged --------------------

_JQ_NAMES = ("jq", "jq.exe")


def _holds(directory, names):
    return any(os.path.isfile(os.path.join(directory, n)) for n in names)


def _path_without_jq(tmp_path):
    """PATH with jq hidden and every other tool left in place.

    A directory without jq stays as it is. A directory with jq is replaced,
    at the same PATH position, by a mirror of symlinks to everything in it
    except jq. Dropping the directory instead would also drop its neighbours:
    on macOS jq is /usr/bin/jq but cat is /bin/cat, so dropping /usr/bin loses
    `tr` (only at /usr/bin/tr) and the hook dies on `tr: command not found`.
    On Linux with merged /usr, jq and cat share a directory, which is why an
    approach that only drops directories could never run there.

    Windows: a jq directory without cat (the Chocolatey/Scoop shims case) is
    dropped, as nothing the hook needs lives there. Otherwise the mirror needs
    symlinks, which need privilege on Windows: skip when they are refused.
    """
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
            continue  # unreadable: nothing usable in it, and jq cannot be proven absent
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


def test_copilot_without_jq_still_gets_the_envelope(tmp_path):
    """Python builds the envelope, so a jq-less Copilot host now gets its
    recap injected (the one behaviour change of the size-budget work; it
    used to fall back to plain text and log _NO_JQ_LOG)."""
    path = _path_without_jq(tmp_path)
    # Positive control: jq is really hidden, and what the hook needs is not.
    # shutil.which applies PATHEXT on Windows, so the bare names work there too.
    assert shutil.which("jq", path=path) is None
    for tool in ("tr", "cat", "date"):
        assert shutil.which(tool, path=path), f"{tool} lost from the jq-less PATH"
    out = _run(tmp_path, UUID, {"COPILOT_CLI": "1", "PATH": path})
    _assert_envelope(out)
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


def test_copilot_failing_jq_no_longer_matters(tmp_path):
    """A jq that exits non-zero used to cost the envelope; Python builds it
    now, so the recap is still injected and no fallback is logged."""
    path = os.pathsep.join([str(_failing_jq_dir(tmp_path)), os.environ.get("PATH", "")])
    out = _run(tmp_path, UUID, {"COPILOT_CLI": "1", "PATH": path,
                                "REMEMBER_TOOLS_CACHE": "0"})
    _assert_envelope(out)
    assert _ENVELOPE_FAILED_LOG not in _logs(tmp_path)


# --- The Python helper cannot run: plain recap, logged ----------------------

_PY_NAMES = ("python3", "python", "py")


def _python_stub_dir(tmp_path, version_ok):
    """A directory of `python3` / `python` / `py` stubs to put first on PATH.
    Each appends its argv to calls.txt and exits 1, except that `-V` (the
    hook's interpreter probe) exits 0 when VERSION_OK. Returns (dir, calls)."""
    d = tmp_path / "python-stub"
    d.mkdir()
    calls = tmp_path / "calls.txt"
    probe = "0" if version_ok else "1"
    body = ("#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> '{calls.as_posix()}'\n"
            "for a in \"$@\"; do\n"
            f"    [ \"$a\" = -V ] && exit {probe}\n"
            "done\n"
            "exit 1\n")
    for name in _PY_NAMES:
        stub = d / name
        stub.write_bytes(body.encode())
        stub.chmod(0o755)
    return d, calls


def _calls(calls):
    return calls.read_text(encoding="utf-8") if calls.exists() else ""


def _stub_env(stub_dir, extra=None):
    env = {"PATH": os.pathsep.join([str(stub_dir), os.environ.get("PATH", "")]),
           "REMEMBER_TOOLS_CACHE": "0"}
    env.update(extra or {})
    return env


def test_copilot_without_python_falls_back_to_plain_and_logs_it(tmp_path):
    """No working interpreter (every candidate fails its `-V` probe): the
    recap still goes out, plain, and the log says the envelope failed."""
    stub_dir, calls = _python_stub_dir(tmp_path, version_ok=False)
    out = _run(tmp_path, UUID, _stub_env(stub_dir, {"COPILOT_CLI": "1"}))
    _assert_plain(out)
    assert "-V" in _calls(calls)  # positive control: the stubs were what ran
    assert "copilot_recap" not in _calls(calls)
    assert _ENVELOPE_FAILED_LOG in _logs(tmp_path)


def test_copilot_helper_failure_falls_back_to_plain_and_logs_it(tmp_path):
    """The interpreter probes fine, but the helper itself exits non-zero."""
    stub_dir, calls = _python_stub_dir(tmp_path, version_ok=True)
    out = _run(tmp_path, UUID, _stub_env(stub_dir, {"COPILOT_CLI": "1"}))
    _assert_plain(out)
    assert "-m pipeline.copilot_recap" in _calls(calls)
    assert _ENVELOPE_FAILED_LOG in _logs(tmp_path)


def test_claude_code_path_never_calls_the_helper(tmp_path):
    """Claude Code (no Copilot variable, no prefix): the trigger does not
    fire, so no interpreter is probed or run at all -- the stubs, which record
    every call, are never touched. Positive control: the helper-failure case
    above, where the same stubs do record the helper call."""
    stub_dir, calls = _python_stub_dir(tmp_path, version_ok=True)
    out = _run(tmp_path, UUID, _stub_env(stub_dir))
    _assert_plain(out)
    assert _calls(calls) == ""
    logs = _logs(tmp_path)
    assert "session-start" in logs
    assert _ENVELOPE_FAILED_LOG not in logs


def test_promo_on_copilot_helper_failure_still_skips_the_promo(tmp_path, claude_promo):
    """A failed helper leaves the host unknown, so the promo is skipped (no
    `systemMessage`, no marker burned): the plain recap goes out."""
    stub_dir, _calls_file = _python_stub_dir(tmp_path, version_ok=True)
    out = _run(tmp_path / "copilot", UUID, _stub_env(stub_dir, {"COPILOT_CLI": "1"}),
               promos=True)
    assert '"systemMessage"' not in out
    _assert_plain(out)


def _recording_python_dir(tmp_path):
    """`python3` / `python` / `py` stubs that record their argv to calls.txt
    and then run the real interpreter -- so the helper really runs, and the
    test can see that it did. Returns (dir, calls)."""
    d = tmp_path / "python-recorder"
    d.mkdir()
    calls = tmp_path / "calls.txt"
    real = Path(sys.executable).as_posix()
    body = ("#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> '{calls.as_posix()}'\n"
            "[ \"$1\" = -3 ] && shift\n"
            f"exec '{real}' \"$@\"\n")
    for name in _PY_NAMES:
        stub = d / name
        stub.write_bytes(body.encode())
        stub.chmod(0o755)
    return d, calls


def _normalised(text, root):
    """TEXT with this run's sandbox root and the log's clock and duration
    taken out, so two sandboxes' runs compare line for line."""
    lines = []
    for line in text.replace(str(root), "<root>").replace(root.as_posix(), "<root>").splitlines():
        if line[:9].count(":") == 2 and line[8:9] == " ":
            line = line[9:]
        if line.startswith("[hook] session-start took "):
            continue
        lines.append(line)
    return lines


def test_false_positive_trigger_changes_nothing(tmp_path):
    """A Claude Code session whose environment also carries COPILOT_CLI fires
    the cheap trigger: the helper runs (the recorder sees it), applies the
    exact rule -- Claude Code's signature wins -- and changes nothing. Its
    stdout and log lines equal the same run without COPILOT_CLI, which is
    the paired control (and which never calls the helper at all)."""
    results = {}
    for name, extra in (("control", {}), ("copilot-var", {"COPILOT_CLI": "1"})):
        root = tmp_path / name
        root.mkdir()
        rec_dir, calls = _recording_python_dir(root)
        env = _stub_env(rec_dir, {"CLAUDE_CODE_ENTRYPOINT": "cli", **extra})
        out = _run(root, UUID, env)
        results[name] = (_normalised(out, root), _normalised(_logs(root), root), _calls(calls))
    control, fired = results["control"], results["copilot-var"]
    assert "-m pipeline.copilot_recap" in fired[2]  # the helper really ran
    assert control[2] == ""                         # the control never forked
    _assert_plain("\n".join(control[0]))
    assert fired[0] == control[0]
    assert fired[1] == control[1]
    assert any("session-start" in line for line in control[1])


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


def test_traversal_after_the_prefix_is_rejected_by_the_hook(tmp_path):
    """`agent-host-x:/../../x` strips to `../../x`, which the hook's own
    validator must empty: the hook exits 0, says it had no usable session id,
    and writes nothing outside the sandbox store."""
    before = tree(tmp_path)
    out = _run(tmp_path, "agent-host-x:/../../x")
    assert "PROBE-RECENT-LINE" in out
    assert _NO_ID_LOG in _logs(tmp_path)
    outside = created_outside(tmp_path, before, ("proj/.remember/",),
                              exact=("proj", "proj/.remember", "home"))
    assert outside == [], outside
    assert not (tmp_path / "x").exists()


def test_prefixed_uuid_reaches_the_hook_as_a_session_id(tmp_path):
    """Positive control for the rejection above: a valid prefixed id is kept."""
    _run(tmp_path, f"agent-host-copilotcli:/{UUID}")
    logs = _logs(tmp_path)
    assert "session-start" in logs
    assert _NO_ID_LOG not in logs
