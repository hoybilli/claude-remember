#!/usr/bin/env bash
# Throwaway probe kit: offline self-test (no host involved).
#
#   selftest.sh [--keep]
#
# Stages variant "all" (and a camel copy) into a fresh temp dir, then runs
# every handler string of every hooks file exactly as written -- `command` /
# `bash` strings through `bash -c`, `powershell` strings through Windows
# PowerShell -EncodedCommand (Windows only) -- with sample payloads on stdin
# (snake_case for PascalCase files, camelCase for camelCase files) and the
# three plugin-root variables set the way the hosts set them. Also runs
# scripts/run-probe.ps1 directly with -File. Checks: SessionStart stdout is
# exactly the expected envelope, every other event prints nothing, one log
# line per call, every log line parses. Runs collect.sh on the log, validates
# every JSON file with `python -m json.tool`, then deletes the temp dir.
set -u
K="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
keep=""; [ "${1:-}" = "--keep" ] && keep=1
PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import json' >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "selftest: python3 needed" >&2; exit 1; }
HAVE_PS=""; command -v powershell >/dev/null 2>&1 && HAVE_PS=1

tmp="$(mktemp -d 2>/dev/null || mktemp -d -t probe)"
fail=0; calls=0
ok() { echo "  ok   $*"; }
bad() { echo "  FAIL $*"; fail=$((fail+1)); }

bash "$K/stage.sh" all "$tmp/s/stage-all" >/dev/null || { echo "stage failed"; exit 1; }
bash "$K/stage.sh" all "$tmp/c/stage-camel" --camel >/dev/null || { echo "stage camel failed"; exit 1; }
. "$tmp/s/stage-all/probe.conf"

winp() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

sample() { # $1 = event key as written in the hooks file
    case "$1" in
        SessionStart) printf '%s' '{"cwd":"c:\\Users\\user\\proj","hook_event_name":"SessionStart","initial_prompt":"PROBE","session_id":"00000000-0000-4000-8000-000000000000","source":"new","timestamp":"2026-09-30T22:48:55.034Z"}' ;;
        UserPromptSubmit) printf '%s' '{"cwd":"c:\\Users\\user\\proj","hook_event_name":"UserPromptSubmit","prompt":"PROBE","session_id":"00000000-0000-4000-8000-000000000000","timestamp":"2026-09-30T22:48:49.044Z"}' ;;
        PostToolUse) printf '%s' '{"cwd":"c:\\Users\\user\\proj","hook_event_name":"PostToolUse","session_id":"00000000-0000-4000-8000-000000000000","timestamp":"2026-09-30T22:49:03.202Z","tool_input":{"command":"Write-Output \"hello\""},"tool_name":"Bash","tool_result":{"result_type":"success","text_result_for_llm":"hello"}}' ;;
        SessionEnd) printf '%s' '{"cwd":"c:\\Users\\user\\proj","hook_event_name":"SessionEnd","reason":"complete","session_id":"00000000-0000-4000-8000-000000000000","timestamp":"2026-09-30T22:48:57.369Z"}' ;;
        sessionStart) printf '%s' '{"cwd":"c:\\Users\\user\\proj","initialPrompt":"PROBE","sessionId":"00000000-0000-4000-8000-000000000000","source":"new","timestamp":1790000000000}' ;;
        userPromptSubmitted) printf '%s' '{"cwd":"c:\\Users\\user\\proj","prompt":"PROBE","sessionId":"00000000-0000-4000-8000-000000000000","timestamp":1790000000000}' ;;
        postToolUse) printf '%s' '{"cwd":"c:\\Users\\user\\proj","sessionId":"00000000-0000-4000-8000-000000000000","timestamp":1790000000000,"toolArgs":{"command":"echo hello"},"toolName":"bash","toolResult":{"resultType":"success","textResultForLlm":"hello"}}' ;;
        sessionEnd) printf '%s' '{"cwd":"c:\\Users\\user\\proj","reason":"complete","sessionId":"00000000-0000-4000-8000-000000000000","timestamp":1790000000000}' ;;
    esac
}

expect_out() { # $1 source $2 event -> expected stdout (without the final LF)
    case "$2" in
        SessionStart|sessionStart)
            case "$1" in
                claudefmt) printf '%s' "{\"additionalContext\": \"$TOKEN_CLAUDEFMT\"}" ;;
                copilot-root) printf '%s' "{\"additionalContext\":\"$TOKEN_COPILOT_ROOT\"}" ;;
                copilot-gh) printf '%s' "{\"additionalContext\":\"$TOKEN_COPILOT_GH\"}" ;;
            esac ;;
    esac
}

# Extract "<event>\t<key>\t<string>" for every handler of a hooks file.
handlers() {
    "$PY" -I - "$1" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
for ev, groups in d["hooks"].items():
    for g in groups:
        for h in (g.get("hooks") or [g]):
            for key in ("command", "bash", "powershell"):
                if key in h:
                    sys.stdout.write("%s\t%s\t%s\n" % (ev, key, h[key].replace("\t", " ")))
PYEOF
}

run_file() { # $1 stage dir  $2 hooks file (relative)  $3 source tag
    local stage="$1" file="$2" src="$3" ev key str got exp b64
    export CLAUDE_PLUGIN_ROOT="$(winp "$stage")" COPILOT_PLUGIN_ROOT="$(winp "$stage")" PLUGIN_ROOT="$(winp "$stage")"
    while IFS="$(printf '\t')" read -r ev key str; do
        exp="$(expect_out "$src" "$ev")"
        if [ "$key" = "powershell" ]; then
            [ -n "$HAVE_PS" ] || continue
            b64="$("$PY" -I -c 'import base64,sys; print(base64.b64encode(sys.argv[1].encode("utf-16-le")).decode())' "$str")"
            got="$(sample "$ev" | (cd "$tmp" && powershell -NoLogo -NoProfile -NonInteractive -EncodedCommand "$b64") 2>"$tmp/stderr" | tr -d '\r')"
        else
            got="$(sample "$ev" | (cd "$tmp" && bash -c "$str") 2>"$tmp/stderr")"
        fi
        calls=$((calls+1))
        if [ "$got" = "$exp" ]; then ok "$src $file $ev via $key -> ${got:-<nothing>}"; else bad "$src $file $ev via $key: got [$got] want [$exp] stderr: $(head -c 300 "$tmp/stderr")"; fi
    done <<EOF
$(handlers "$stage/$file")
EOF
}

echo "== handler strings, PascalCase stage ($tmp/s/stage-all)"
run_file "$tmp/s/stage-all" hooks/hooks.json claudefmt
run_file "$tmp/s/stage-all" hooks/hooks.copilot-root.json copilot-root
run_file "$tmp/s/stage-all" hooks/hooks.copilot-gh.json copilot-gh
echo "== handler strings, camelCase stage ($tmp/c/stage-camel)"
run_file "$tmp/c/stage-camel" hooks/hooks.copilot-root.json copilot-root
run_file "$tmp/c/stage-camel" hooks/hooks.copilot-gh.json copilot-gh

if [ -n "$HAVE_PS" ]; then
    echo "== run-probe.ps1 directly (powershell -File)"
    ps1="$(winp "$tmp/s/stage-all/scripts/run-probe.ps1")"
    for ev in SessionStart PostToolUse; do
        exp="$(expect_out copilot-gh "$ev")"
        got="$(sample "$ev" | powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$ps1" copilot-gh "$ev" ps1-direct 2>"$tmp/stderr" | tr -d '\r')"
        calls=$((calls+1))
        if [ "$got" = "$exp" ]; then ok "run-probe.ps1 $ev -> ${got:-<nothing>}"; else bad "run-probe.ps1 $ev: got [$got] want [$exp] $(head -c 300 "$tmp/stderr")"; fi
    done
fi

echo "== recorder.sh directly, no stdin (tty-less, /dev/null) and bash fallback (no python on PATH)"
got="$(bash "$tmp/s/stage-all/scripts/recorder.sh" copilot-root SessionEnd direct </dev/null)"; calls=$((calls+1))
[ -z "$got" ] && ok "empty stdin, SessionEnd -> <nothing>" || bad "empty stdin printed [$got]"
fakebin="$tmp/fakebin"; mkdir -p "$fakebin"
for t in python3 python; do printf '#!/bin/sh\nexit 1\n' > "$fakebin/$t"; chmod +x "$fakebin/$t"; done
got="$(sample sessionStart | PATH="$fakebin:$PATH" bash "$tmp/s/stage-all/scripts/recorder.sh" copilot-gh sessionStart nopython)"; calls=$((calls+1))
[ "$got" = "$(expect_out copilot-gh sessionStart)" ] && ok "bash-fallback recorder -> $got" || bad "bash-fallback got [$got]"

# The two stages log beside themselves; merge both logs for the checks.
log="$tmp/all-probe-log.jsonl"
cat "$tmp/s/probe-log.jsonl" "$tmp/c/probe-log.jsonl" > "$log" 2>/dev/null
cat "$tmp/s/probe-log.jsonl.err" "$tmp/c/probe-log.jsonl.err" > "$log.err" 2>/dev/null
echo "== log checks ($log)"
n="$(grep -c . "$log" 2>/dev/null || echo 0)"
[ "$n" = "$calls" ] && ok "log lines $n == calls $calls" || bad "log lines $n != calls $calls"
"$PY" -I - "$log" <<'PYEOF' || fail=$((fail+1))
import json, sys
bad = 0
recs = []
for i, ln in enumerate(open(sys.argv[1], encoding="utf-8"), 1):
    try:
        recs.append(json.loads(ln))
    except Exception as e:
        bad += 1
        print("  FAIL line %d does not parse: %s" % (i, e))
fb = [r for r in recs if r.get("recorder") == "bash"]
print("  ok   %d lines parse; recorder=python: %d, recorder=bash: %d" % (len(recs), len(recs) - len(fb), len(fb)))
for r in recs[:1]:
    print("  sample line keys:", ",".join(r.keys()))
sys.exit(1 if bad else 0)
PYEOF
if [ -s "$log.err" ]; then bad "$log.err not empty:"; head -20 "$log.err"; else ok "no recorder stderr"; fi

echo "== collect.sh"
bash "$K/collect.sh" "$log"

echo "== json.tool on every JSON file (kit + staged copies)"
while IFS= read -r f; do
    if "$PY" -m json.tool "$f" >/dev/null 2>"$tmp/stderr"; then ok "json $f"; else bad "json $f: $(cat "$tmp/stderr")"; fi
done <<EOF
$(find "$K/remember-probe" "$tmp/s" "$tmp/c" -name '*.json' -type f | LC_ALL=C sort)
EOF

if command -v claude >/dev/null 2>&1; then
    echo "== claude plugin validate (staged all)"
    claude plugin validate "$tmp/s/stage-all" 2>&1 | sed 's/^/  /'
fi

if [ -n "$keep" ]; then echo "kept: $tmp"; else rm -rf "$tmp"; echo "cleaned: $tmp"; fi
echo "selftest: $fail failure(s), $calls calls"
[ "$fail" -eq 0 ]
