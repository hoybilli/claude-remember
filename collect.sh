#!/usr/bin/env bash
# Throwaway probe kit: summarise a probe log.
#
#   collect.sh <probe-log.jsonl> [--raw]
#
# One row per (os, source, event, handler_key): count, payload casing,
# hook_event_name present, session-id field name, which Q6 env vars were set,
# bash path, cwd, plugin root. Then which sources fired at all, and the
# SessionStart stdout each source emitted. --raw also prints each payload's
# top-level keys. Python 3 stdlib only.
set -u
[ $# -ge 1 ] || { sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 2; }
PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import json' >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "collect.sh: python3 needed" >&2; exit 1; }
exec "$PY" -I - "$@" <<'PYEOF'
import json, re, sys
from collections import OrderedDict

args = sys.argv[1:]
path = args[0]
raw_mode = "--raw" in args[1:]
ENV = ["COPILOT_CLI", "COPILOT_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT",
       "COPILOT_HOME", "CLAUDE_PROJECT_DIR"]
SHORT = {"COPILOT_CLI": "CLI", "COPILOT_PLUGIN_ROOT": "CPR", "CLAUDE_PLUGIN_ROOT": "CLPR",
         "PLUGIN_ROOT": "PR", "COPILOT_HOME": "CH", "CLAUDE_PROJECT_DIR": "CLPD"}

def casing(keys):
    snake = any("_" in k for k in keys)
    camel = any(re.search(r"[a-z][A-Z]", k) for k in keys)
    if snake and camel:
        return "mixed"
    if snake:
        return "snake"
    if camel:
        return "camel"
    return "flat" if keys else "-"

def tail(s, n=38):
    s = s or ""
    return s if len(s) <= n else "..." + s[-(n - 3):]

rows = OrderedDict()
bad = 0
recs = []
with open(path, encoding="utf-8", errors="replace") as fh:
    for ln in fh:
        ln = ln.strip()
        if not ln:
            continue
        try:
            recs.append(json.loads(ln))
        except Exception:
            bad += 1

for r in recs:
    k = (r.get("os", ""), r.get("source", ""), r.get("event", ""), r.get("handler_key", ""))
    row = rows.setdefault(k, {"n": 0, "casing": set(), "hen": set(), "sid": set(),
                              "env": set(), "bash": set(), "cwd": set(), "root": set(),
                              "keys": set(), "out": set(), "stage": set()})
    row["n"] += 1
    keys = r.get("stdin_keys") or []
    if isinstance(keys, str):
        keys = keys.split()
    row["casing"].add(casing(keys))
    row["hen"].add("yes" if "hook_event_name" in keys else ("hookEventName" if "hookEventName" in keys else "no"))
    sid = [x for x in ("session_id", "sessionId") if x in keys]
    row["sid"].add(sid[0] if sid else "-")
    env = r.get("env") or {}
    row["env"].add(",".join(SHORT[e] for e in ENV if e in env) or "-")
    row["bash"].add(r.get("bash_running") or r.get("which_bash") or "")
    row["cwd"].add(r.get("cwd", ""))
    row["root"].add(r.get("plugin_root", ""))
    row["keys"].add(",".join(keys))
    if r.get("stdout_emitted"):
        row["out"].add(r["stdout_emitted"])
    row["stage"].add(r.get("stage", ""))

def j(s):
    return "|".join(sorted(x for x in s if x is not None)) or "-"

hdr = ["os", "source", "event", "key", "n", "casing", "hook_event_name", "sid field", "env set", "bash", "cwd", "plugin root"]
table = [hdr]
for (os_, src, ev, key), row in rows.items():
    table.append([tail(os_, 22), src, ev, key or "-", str(row["n"]), j(row["casing"]), j(row["hen"]),
                  j(row["sid"]), j(row["env"]), tail(j(row["bash"]), 30), tail(j(row["cwd"])), tail(j(row["root"]))])
w = [max(len(r[i]) for r in table) for i in range(len(hdr))]
for i, r in enumerate(table):
    print("  ".join(c.ljust(w[n]) for n, c in enumerate(r)).rstrip())
    if i == 0:
        print("  ".join("-" * x for x in w))
print()
print("env key: CLI=COPILOT_CLI CPR=COPILOT_PLUGIN_ROOT CLPR=CLAUDE_PLUGIN_ROOT PR=PLUGIN_ROOT CH=COPILOT_HOME CLPD=CLAUDE_PROJECT_DIR")
fired = OrderedDict()
for (os_, src, ev, key), row in rows.items():
    fired.setdefault(src, set()).add(key or "-")
allsrc = ["claudefmt", "copilot-root", "copilot-gh"]
print("sources fired: " + "; ".join(
    "%s=%s" % (s, ("yes via " + "+".join(sorted(fired[s]))) if s in fired else "no")
    for s in allsrc + [s for s in fired if s not in allsrc]))
for (os_, src, ev, key), row in rows.items():
    if row["out"]:
        print("SessionStart stdout [%s %s %s]: %s" % (os_, src, key, j(row["out"])))
stages = set()
for row in rows.values():
    stages |= row["stage"]
print("stage(s): " + j(stages))
print("records: %d  unparsable lines: %d" % (len(recs), bad))
if raw_mode:
    print()
    for (os_, src, ev, key), row in rows.items():
        print("keys [%s %s %s]: %s" % (src, ev, key, j(row["keys"])))
PYEOF
