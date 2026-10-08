"""remember-probe (throwaway): builds ONE JSON log line for recorder.sh.

Everything comes in through PROBE_* environment variables set by recorder.sh
(so the values are bash's view, not a Windows-converted one); the line goes
to stdout as UTF-8 with a single LF. Stdlib only. Never raises.
"""
import json
import os
import sys
from datetime import datetime, timezone


def scan_keys(text):
    """Top-level object keys by a tolerant scan (works on truncated JSON)."""
    keys, depth, i, n = [], 0, 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j, buf = i + 1, []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j:j + 2])
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            s = "".join(buf)
            k = j + 1
            while k < n and text[k] in " \t\r\n":
                k += 1
            if depth == 1 and k < n and text[k] == ":":
                keys.append(s)
            i = j + 1
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        i += 1
    return keys


def main():
    e = os.environ
    raw = e.get("PROBE_STDIN", "")
    try:
        obj = json.loads(raw)
        keys = list(obj.keys()) if isinstance(obj, dict) else []
        parse = "json"
    except Exception:
        keys = scan_keys(raw) if raw.strip() else []
        parse = "scan" if keys else "none"
    env = {}
    for name in e.get("PROBE_SET_NAMES", "").split():
        env[name] = e.get("PROBE_E_" + name, "")
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "os": e.get("PROBE_OS", ""),
        "source": e.get("PROBE_SRC", ""),
        "event": e.get("PROBE_EV", ""),
        "handler_key": e.get("PROBE_KEY", ""),
        "argv": json.loads(e.get("PROBE_ARGV_JSON", "[]") or "[]"),
        "stdin_len": int(e.get("PROBE_STDIN_LEN", "0") or 0),
        "stdin_raw": raw[:4000],
        "stdin_keys": keys,
        "stdin_parse": parse,
        "env": env,
        "env_names_matching": sorted(e.get("PROBE_MATCH_NAMES", "").split()),
        "cwd": e.get("PROBE_CWD", ""),
        "which_bash": e.get("PROBE_WHICH_BASH", ""),
        "bash_running": e.get("PROBE_BASH", ""),
        "script": e.get("PROBE_SELF", ""),
        "plugin_root": e.get("PROBE_ROOT", ""),
        "stage": e.get("PROBE_STAGE", ""),
        "stdout_emitted": e.get("PROBE_STDOUT", ""),
        "recorder": "python",
    }
    line = json.dumps(rec, ensure_ascii=True) + "\n"
    sys.stdout.buffer.write(line.encode("utf-8"))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # never fail the hook
        sys.stderr.write("remember-probe: record.py: %r\n" % (exc,))
