"""pipeline/copilot_recap.py: the Python half of session-start-hook.sh's
Copilot recap emit (issue: vscode). The envelope it writes must be byte for
byte what the hook used to print with `jq -Rs '{additionalContext:.}'` and
`printf '%s\\n'` -- compared here against the real jq on whatever platform
runs the suite (the Windows text-mode CRLF behaviour included, observed with
jq 1.8.2 on Windows 11; Linux/macOS are compared on their own CI legs).
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline import copilot_recap  # noqa: E402

JQ = shutil.which("jq")
needs_jq = pytest.mark.skipif(JQ is None, reason="needs jq to compare against")

_HOST_ENV = ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID",
             "CODEX_THREAD_ID", "ANTIGRAVITY_CONVERSATION_ID", "COPILOT_CLI",
             "COPILOT_PLUGIN_ROOT", "REMEMBER_HOST_HINT", "REMEMBER_DIR")

_NAMED = [
    b"",
    b"\n",
    b"plain recap\n=== MEMORY ===\nPROBE\n\n",
    b'tab\there "quotes" back\\slash /solidus <>&\n',
    b"ctl \x00\x01\x08\x0c\x1b\x1f del \x7f\n",
    b"crlf\r\nlone cr\r end\r",
    "non-ascii ü 日本   \U0001F600\n".encode(),
    b"\xff", b"\xe6\x97", b"\xe6\x97\n\n", b"a\xc3(b", b"\xed\xa0\x80",
    b"\xf4\x90\x80\x80", b"\xc0\xaf", b"\xe0\x80\x80", b"\xf0\x80\x80\x80x",
    b"a" * 5000 + "é日".encode() + b"\n",
]

_ALPHABET = [b"a", b"\r", b"\n", b"\r\n", b"\x7f", b"\x00", b"\xc3", b"\xbc", b"\xe6",
             b"\x97", b"\xa5", b"\xed", b"\xa0", b"\x80", b"\xf4", b"\x90", b"\xf0",
             b"\x9f", b"\xc0", b"\xff", b'"', b"\\", b"\t", b"\xe2", b"\xa8"]


def _fuzz(count=200, seed=5):
    rng = random.Random(seed)
    return [b"".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 12)))
            for _ in range(count)]


def _jq_envelope(recap: bytes) -> bytes:
    """What the hook printed: `$(jq ...)` drops the trailing newlines, then
    `printf '%s\\n'` adds one back."""
    out = subprocess.run([JQ, "-Rs", "{additionalContext:.}"], input=recap,
                         capture_output=True, check=True).stdout
    return out.rstrip(b"\n") + b"\n"


@needs_jq
@pytest.mark.parametrize("recap", _NAMED, ids=range(len(_NAMED)))
def test_envelope_is_byte_identical_to_jq_named(recap):
    assert copilot_recap.envelope(recap) == _jq_envelope(recap)


@needs_jq
def test_envelope_is_byte_identical_to_jq_fuzzed():
    bad = [c for c in _fuzz() if copilot_recap.envelope(c) != _jq_envelope(c)]
    assert bad == []


def test_envelope_shape_on_both_line_ending_modes():
    """Platform-independent: one top-level key, the recap as its value; the
    Windows mode differs only in CRLF handling."""
    recap = b"line one\r\nline two\n"
    unix = copilot_recap.envelope(recap, windows=False)
    win = copilot_recap.envelope(recap, windows=True)
    assert unix == b'{\n  "additionalContext": "line one\\r\\nline two\\n"\n}\n'
    assert win == b'{\r\n  "additionalContext": "line one\\nline two\\n"\r\n}\r\n'
    assert json.loads(unix) == {"additionalContext": "line one\r\nline two\n"}


def _env(monkeypatch, **values):
    for name in _HOST_ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_main_wraps_the_recap_in_place_on_the_copilot_host(tmp_path, monkeypatch, capsys):
    _env(monkeypatch, COPILOT_CLI="1")
    recap = tmp_path / "ctx"
    recap.write_bytes(b"=== REMEMBER ===\nPROBE\n")
    assert copilot_recap.main([str(recap)]) == 0
    assert capsys.readouterr().out == "copilot"
    assert recap.read_bytes() == copilot_recap.envelope(b"=== REMEMBER ===\nPROBE\n")


def test_main_leaves_other_hosts_alone(tmp_path, monkeypatch, capsys):
    """Paired with the case above: same file, Claude Code's signature set."""
    _env(monkeypatch, COPILOT_CLI="1", CLAUDE_CODE_ENTRYPOINT="cli")
    recap = tmp_path / "ctx"
    recap.write_bytes(b"=== REMEMBER ===\nPROBE\n")
    assert copilot_recap.main([str(recap)]) == 0
    assert capsys.readouterr().out == ""
    assert recap.read_bytes() == b"=== REMEMBER ===\nPROBE\n"


def test_main_logs_an_unbuffered_recap(tmp_path, monkeypatch, capsys):
    _env(monkeypatch, REMEMBER_HOST_HINT="copilot", REMEMBER_DIR=str(tmp_path))
    assert copilot_recap.main([]) == 0
    assert capsys.readouterr().out == "copilot"
    logs = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "logs").glob("*.log"))
    assert f"[hook] {copilot_recap.UNBUFFERED_LOG}" in logs


def test_main_logs_nothing_unbuffered_off_the_copilot_host(tmp_path, monkeypatch, capsys):
    _env(monkeypatch, REMEMBER_DIR=str(tmp_path))
    assert copilot_recap.main([]) == 0
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "logs").exists()


def test_a_failed_write_puts_the_recap_back(tmp_path, monkeypatch):
    """The hook's fallback `cat`s the file after a failure, so it must hold
    the plain recap again, not a half-written envelope."""
    _env(monkeypatch, COPILOT_CLI="1")
    recap = tmp_path / "ctx"
    original = b"=== REMEMBER ===\n" + b"PROBE\n" * 100
    recap.write_bytes(original)
    real_envelope = copilot_recap.envelope

    class Half(bytes):
        pass

    def half_then_fail(data):
        return Half(real_envelope(data))

    real_open = open

    class FailingWrites:
        def __init__(self, fh):
            self._fh = fh
            self._writes = 0

        def __getattr__(self, name):
            return getattr(self._fh, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

        def write(self, data):
            self._writes += 1
            if isinstance(data, Half):
                self._fh.write(data[: len(data) // 2])
                raise OSError("disk full")
            return self._fh.write(data)

    def fake_open(path, mode="r", *args, **kwargs):
        return FailingWrites(real_open(path, mode, *args, **kwargs))

    monkeypatch.setattr(copilot_recap, "envelope", half_then_fail)
    monkeypatch.setattr("builtins.open", fake_open)
    with pytest.raises(OSError):
        copilot_recap.main([str(recap)])
    monkeypatch.undo()
    assert recap.read_bytes() == original
