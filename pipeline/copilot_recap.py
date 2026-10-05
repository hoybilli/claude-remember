r"""The Copilot half of session-start-hook.sh's recap emit (issue: vscode).

Usage (from the hook, run from the plugin root)::

    python -m pipeline.copilot_recap [RECAP_FILE]

The hook calls this once, and only when its cheap trigger fires (the session
id carried the ``agent-host-*:/`` prefix, or ``COPILOT_CLI`` /
``COPILOT_PLUGIN_ROOT`` is non-empty). It lives here rather than inline in
the hook to keep the compiled hook inside v0.40.0's size budget
(``HOOK_SCRIPT_MAX_BYTES``).

1. Applies the exact host rule, ``pipeline.host.copilot_session`` (described
   in ``scripts/lib-session-id.sh``).
2. Not the Copilot host: changes nothing, prints nothing, exits 0. The hook's
   own branches (promo envelope, or the plain recap) then run unchanged.
3. Copilot host, RECAP_FILE given (the hook buffered its recap there):
   rewrites RECAP_FILE, in place, to the recap wrapped as a top-level
   ``{"additionalContext": ...}`` -- the only shape VS Code was observed to
   inject (VS Code 1.139.1 / Windows 11) -- byte for byte what the hook used
   to print with ``jq -Rs '{additionalContext:.}'`` (see ``envelope``). The
   hook then prints the file with its plain ``cat``. Python builds it, so the
   envelope no longer needs jq.
4. Copilot host, no RECAP_FILE (the recap went out live because a trace is
   running or tmp/ is not writable): logs that, to the hook's own daily log.
5. Copilot host either way: prints ``copilot`` (no newline), which the hook
   stores as REMEMBER_HOST_HINT and uses to skip promos on that host.

Any failure raises, so the interpreter exits non-zero and the hook falls back
to the plain recap and logs it. A write that fails part way puts the
original bytes back first (``_wrap_in_place`` says exactly what that
guarantees), so that fallback prints the recap, not a half-written envelope.
"""

from __future__ import annotations

import json
import os
import sys

from . import host as _host
from .log import log

UNBUFFERED_LOG = (
    "session-start: copilot host, recap not buffered (trace on or tmp/ not "
    "writable), printed as plain text, which this host does not inject"
)

_REPLACEMENT = "�"


def _jq_decode(data: bytes) -> str:
    """DATA as text, the way ``jq -Rs`` reads it: valid UTF-8 as is (only
    invalid input takes the slow path). jq reads raw input a line at a time,
    newline included, and replaces each invalid sequence in a line with one
    U+FFFD by its own rule (src/jv_unicode.c, ``jvp_utf8_next``), which
    groups bytes differently from Python's ``errors="replace"``: an encoded
    surrogate or a code point above U+10FFFF is one U+FFFD in jq but one per
    byte in Python, and a sequence cut off by the end of its line swallows
    the rest of the line -- the newline too. Both observed against jq 1.8.2
    (tests/test_copilot_recap_vscode.py compares with the real jq)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    lines = data.split(b"\n")
    chunks = [line + b"\n" for line in lines[:-1]]
    if lines[-1]:
        chunks.append(lines[-1])
    return "".join(_jq_decode_line(chunk) for chunk in chunks)


def _jq_decode_line(data: bytes) -> str:
    """One raw-input line, replaced as ``jvp_utf8_next`` replaces it."""
    out = []
    i, n = 0, len(data)
    while i < n:
        first = data[i]
        if first < 0x80:
            out.append(chr(first))
            i += 1
            continue
        if 0xC2 <= first <= 0xDF:
            length, cp, floor = 2, first & 0x1F, 0x80
        elif 0xE0 <= first <= 0xEF:
            length, cp, floor = 3, first & 0x0F, 0x800
        elif 0xF0 <= first <= 0xF4:
            length, cp, floor = 4, first & 0x07, 0x10000
        else:
            out.append(_REPLACEMENT)
            i += 1
            continue
        if i + length > n:
            out.append(_REPLACEMENT)
            break
        good = True
        for k in range(1, length):
            ch = data[i + k]
            if not 0x80 <= ch <= 0xBF:
                good = False
                length = k
                break
            cp = (cp << 6) | (ch & 0x3F)
        if good and cp >= floor and not 0xD800 <= cp <= 0xDFFF and cp <= 0x10FFFF:
            out.append(chr(cp))
        else:
            out.append(_REPLACEMENT)
        i += length
    return "".join(out)


def envelope(recap: bytes, windows: bool | None = None) -> bytes:
    r"""``{"additionalContext": RECAP}`` byte for byte as the hook printed it
    with ``jq -Rs '{additionalContext:.}'`` and then ``printf '%s\n'``.

    ``json.dumps`` with a 2-space indent and ``ensure_ascii=False`` matches
    jq's layout and escapes every character below U+0020 the way jq does
    (``\n`` ``\t`` ``\r`` ``\b`` ``\f``, else lowercase ``\u00XX``). jq also
    escapes DEL, which ``json.dumps`` leaves raw, so that is done here (DEL
    can only occur inside the string). Invalid UTF-8 is replaced as jq
    replaces it (``_jq_decode``).

    WINDOWS (default: whether this platform is Windows) reproduces what the
    hook printed there with a native Windows jq build (observed with jq 1.8.2
    from WinGet, under Git Bash, on Windows 11): jq reads its input in text
    mode, so each CRLF in the recap arrives as LF, and writes its output in
    text mode, so the envelope's own line breaks are CRLF -- except the last:
    the hook's ``$(...)`` dropped jq's trailing CRLF and ``printf '%s\n'``
    ended the envelope with a single LF. This assumes a native jq; a user
    with an MSYS-built jq got LF throughout before, and gets CRLF now. Not
    reproduced: a text-mode read also stops at a Ctrl-Z byte (0x1A), which
    no recap carries."""
    if windows is None:
        windows = os.name == "nt"
    if windows:
        recap = recap.replace(b"\r\n", b"\n")
    text = _jq_decode(recap)
    out = json.dumps({"additionalContext": text}, indent=2, ensure_ascii=False)
    out = out.replace("\x7f", "\\u007f")
    if windows:
        out = out.replace("\n", "\r\n")
    return (out + "\n").encode("utf-8")


def _write_all(fd: int, data: bytes) -> None:
    """Make the file behind FD hold exactly DATA: unbuffered writes from
    offset 0, looped until every byte is written, then truncated to fit."""
    os.lseek(fd, 0, os.SEEK_SET)
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]
    os.ftruncate(fd, len(data))


def _wrap_in_place(path: str) -> None:
    """Rewrite PATH, in place, to ``envelope`` of its own contents.

    In place, not write-then-rename: the hook still holds PATH open as its
    stdout while this runs, and Windows refuses to replace a file another
    process has open (observed: WinError 5 on Windows 11).

    Unbuffered (``os.write``, no Python file buffer): every byte has reached
    the file, or failed, by the time ``_write_all`` returns, so a failure
    surfaces inside the ``try`` -- including one at truncate time -- and is
    never deferred to a later flush that the restore would trip over. On any
    failure the original bytes are written back, over space the recap
    already occupied, before the error propagates; the hook's plain-text
    fallback then prints the recap. Only if that restore itself fails does
    its error replace the first one, and the file may then hold neither."""
    fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
    try:
        chunks = []
        while True:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            chunks.append(chunk)
        original = b"".join(chunks)
        data = envelope(original)
        try:
            _write_all(fd, data)
        except BaseException:
            _write_all(fd, original)
            raise
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    recap_file = args[0] if args else ""
    if not _host.copilot_session():
        return 0
    if recap_file:
        _wrap_in_place(recap_file)
    else:
        remember_dir = os.environ.get("REMEMBER_DIR", "")
        if remember_dir:
            log("hook", UNBUFFERED_LOG, os.path.join(remember_dir, "logs"))
    sys.stdout.write("copilot")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
