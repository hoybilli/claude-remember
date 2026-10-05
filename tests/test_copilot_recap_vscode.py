"""pipeline/copilot_recap.py: the Python half of session-start-hook.sh's
Copilot recap emit (issue: vscode). The envelope it writes must be byte for
byte what the hook used to print. The oracle is the hook's own two old lines,
run through bash with whatever jq that bash finds -- the jq the hook would
have used:

    _REMEMBER_HOST_JSON=$(jq -Rs '{additionalContext:.}' < "$file")
    printf '%s\\n' "$_REMEMBER_HOST_JSON"

so the command substitution's own trimming is part of what is compared (Git
Bash's `$(...)` drops a trailing CR as well as the LF a native Windows jq
writes). Without bash or jq the comparisons skip, never pass vacuously.
"""
from __future__ import annotations

import functools
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

from ._bash_runner import decode_bash_output, resolve_bash
from ._vscode_helpers import HOST_ENV, hook_logs

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline import copilot_recap  # noqa: E402

BASH = resolve_bash()

_OLD_TWO_LINES = r'''
for f in "$1"/in/*; do
    _REMEMBER_HOST_JSON=$(jq -Rs '{additionalContext:.}' < "$f")
    printf '%s\n' "$_REMEMBER_HOST_JSON" > "$1/out/${f##*/}"
done
'''


@functools.lru_cache(maxsize=1)
def _bash_jq() -> str:
    """The jq the hook's bash would run, or "" when there is none."""
    if BASH is None:
        return ""
    r = subprocess.run([BASH, "-c", "command -v jq"], capture_output=True, timeout=60)
    return decode_bash_output(r.stdout).strip() if r.returncode == 0 else ""


needs_jq = pytest.mark.skipif(
    not _bash_jq(), reason="needs bash and a jq it can find: the old hook lines are the oracle")


def _old_outputs(recaps: list[bytes], tmp_path: Path) -> list[bytes]:
    """What the old hook printed for each recap, from ONE bash run of its two
    old lines."""
    (tmp_path / "in").mkdir()
    (tmp_path / "out").mkdir()
    names = [f"recap-{n:05d}" for n in range(len(recaps))]
    for name, recap in zip(names, recaps):
        (tmp_path / "in" / name).write_bytes(recap)
    r = subprocess.run([BASH, "-c", _OLD_TWO_LINES, "bash", tmp_path.as_posix()],
                       capture_output=True, timeout=600)
    assert r.returncode == 0, decode_bash_output(r.stderr)
    return [(tmp_path / "out" / name).read_bytes() for name in names]

_HOST_ENV = HOST_ENV + ("REMEMBER_DIR",)

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


def _mismatches(recaps, tmp_path):
    old = _old_outputs(recaps, tmp_path)
    assert len(old) == len(recaps) and all(old)  # positive control: every case ran
    return [(r, o, copilot_recap.envelope(r)) for r, o in zip(recaps, old)
            if copilot_recap.envelope(r) != o]


@needs_jq
def test_envelope_is_byte_identical_to_the_old_hook_lines_named(tmp_path):
    assert _mismatches(_NAMED, tmp_path) == []


@needs_jq
def test_envelope_is_byte_identical_to_the_old_hook_lines_fuzzed(tmp_path):
    assert _mismatches(_fuzz(), tmp_path) == []


def test_envelope_shape_on_both_line_ending_modes():
    """Platform-independent: one top-level key, the recap as its value; the
    Windows mode differs only in CRLF handling -- CRLF inside the envelope,
    and the single LF the old `printf '%s\\n'` ended it with."""
    recap = b"line one\r\nline two\n"
    unix = copilot_recap.envelope(recap, windows=False)
    win = copilot_recap.envelope(recap, windows=True)
    assert unix == b'{\n  "additionalContext": "line one\\r\\nline two\\n"\n}\n'
    assert win == b'{\r\n  "additionalContext": "line one\\nline two\\n"\r\n}\n'
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
    logs = hook_logs(tmp_path / "logs")
    assert f"[hook] {copilot_recap.UNBUFFERED_LOG}" in logs


def test_main_logs_nothing_unbuffered_off_the_copilot_host(tmp_path, monkeypatch, capsys):
    _env(monkeypatch, REMEMBER_DIR=str(tmp_path))
    assert copilot_recap.main([]) == 0
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "logs").exists()


# ── A failed rewrite leaves the plain recap ─────────────────────────────────
# The hook's fallback `cat`s the file after a failure, so it must hold the
# plain recap again, not a half-written envelope. The disk-full error is
# simulated at the two points it can surface: part way through a write, and
# at truncate time (where a buffered file would have flushed). Each case also
# checks the failure really fired, so it cannot pass by never failing.

_ORIGINAL = b"=== REMEMBER ===\n" + b"PROBE\n" * 100  # well under 8 KB


def _disk_full():
    return OSError(28, "No space left on device")


def test_a_write_failing_part_way_puts_the_recap_back(tmp_path, monkeypatch):
    _env(monkeypatch, COPILOT_CLI="1")
    recap = tmp_path / "ctx"
    recap.write_bytes(_ORIGINAL)
    real_write = copilot_recap.os.write
    fired = []

    def write_half_then_fail(fd, data):
        if not fired:
            fired.append(True)
            real_write(fd, bytes(data[: len(data) // 2]))
            raise _disk_full()
        return real_write(fd, data)

    monkeypatch.setattr(copilot_recap.os, "write", write_half_then_fail)
    with pytest.raises(OSError):
        copilot_recap.main([str(recap)])
    monkeypatch.undo()
    assert fired
    assert recap.read_bytes() == _ORIGINAL


def test_a_truncate_failing_puts_the_recap_back(tmp_path, monkeypatch):
    _env(monkeypatch, COPILOT_CLI="1")
    recap = tmp_path / "ctx"
    recap.write_bytes(_ORIGINAL)
    real_ftruncate = copilot_recap.os.ftruncate
    fired = []

    def fail_once(fd, length):
        if not fired:
            fired.append(True)
            raise _disk_full()
        return real_ftruncate(fd, length)

    monkeypatch.setattr(copilot_recap.os, "ftruncate", fail_once)
    with pytest.raises(OSError):
        copilot_recap.main([str(recap)])
    monkeypatch.undo()
    assert fired
    assert recap.read_bytes() == _ORIGINAL


def test_a_successful_rewrite_holds_exactly_the_envelope(tmp_path, monkeypatch):
    """Positive control for the two above: no failure, the envelope, and not
    a byte of the longer original left behind past its end."""
    _env(monkeypatch, COPILOT_CLI="1")
    recap = tmp_path / "ctx"
    recap.write_bytes(_ORIGINAL)
    assert copilot_recap.main([str(recap)]) == 0
    assert recap.read_bytes() == copilot_recap.envelope(_ORIGINAL)
