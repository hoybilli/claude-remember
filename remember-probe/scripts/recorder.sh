#!/usr/bin/env bash
# remember-probe (THROWAWAY; delete after use): records one hook invocation.
#
#   recorder.sh <source-tag> <event> [<handler-key>]
#
# Reads all of stdin (bounded: 5 s), appends ONE JSON line to the probe log
# (PROBE_LOG from probe.conf, else <plugin root>/../probe-log.jsonl), and, for
# a SessionStart event only, prints exactly the envelope for its source tag.
# Every other event prints nothing. Always exits 0; its own errors go to
# "<log>.err".

src="${1:-unknown}"
ev="${2:-unknown}"
key="${3:-}"

self="${BASH_SOURCE[0]:-$0}"
sdir="$(cd "$(dirname "$self")" 2>/dev/null && pwd)" || sdir="$(dirname "$self")"
root="$(dirname "$sdir")"

TOKEN_CLAUDEFMT=""; TOKEN_COPILOT_ROOT=""; TOKEN_COPILOT_GH=""
PROBE_LOG=""; CLAUDEFMT_OUT="envelope"; PROBE_STAGE=""
# shellcheck disable=SC1091
[ -f "$root/probe.conf" ] && . "$root/probe.conf" 2>/dev/null

log="$PROBE_LOG"
if [ -z "$log" ] || [ ! -d "$(dirname "$log")" ]; then
    log="$(dirname "$root")/probe-log.jsonl"
fi

# --- stdin, verbatim (no command substitution, so trailing newlines stay) ---
raw=""
if [ ! -t 0 ]; then
    IFS= read -r -d '' -t 5 raw || true
fi

# --- SessionStart output: exactly one envelope, nothing else on stdout ---
out=""
case "$ev" in
    SessionStart|sessionStart)
        case "$src" in
            claudefmt)
                case "$CLAUDEFMT_OUT" in
                    plain) out="$TOKEN_CLAUDEFMT" ;;
                    hso) out='{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "'"$TOKEN_CLAUDEFMT"'"}}' ;;
                    *) out='{"additionalContext": "'"$TOKEN_CLAUDEFMT"'"}' ;;
                esac ;;
            copilot-root) out='{"additionalContext":"'"$TOKEN_COPILOT_ROOT"'"}' ;;
            copilot-gh) out='{"additionalContext":"'"$TOKEN_COPILOT_GH"'"}' ;;
        esac ;;
esac
[ -n "$out" ] && printf '%s\n' "$out"

# --- the log line (stderr of this block goes to <log>.err) ---
{
    jesc() {
        local s="$1"
        s="${s//\\/\\\\}"
        s="${s//\"/\\\"}"
        s="${s//$'\n'/\\n}"
        s="${s//$'\r'/\\r}"
        s="${s//$'\t'/\\t}"
        printf '%s' "$s"
    }

    names="COPILOT_CLI COPILOT_PLUGIN_ROOT CLAUDE_PLUGIN_ROOT PLUGIN_ROOT COPILOT_HOME CLAUDE_PROJECT_DIR COPILOT_PROJECT_DIR AI_AGENT CLAUDECODE CLAUDE_CODE_ENTRYPOINT HOME BASH_VERSION MSYSTEM WSL_DISTRO_NAME"
    set_names=""
    for n in $names; do
        # eval (fixed names only) rather than ${!n+x}, for bash 3.2 on macOS.
        if eval "[ -n \"\${$n+x}\" ]"; then
            set_names="$set_names $n"
            eval "export PROBE_E_$n=\"\${$n}\""
        fi
    done
    # PATH head: first three entries, as bash sees them.
    ph="$(printf '%s' "$PATH" | tr ':' '\n' | head -n 3 | tr '\n' ':')"
    export "PROBE_E_PATH_HEAD=${ph%:}"
    set_names="$set_names PATH_HEAD"
    match_names="$(env | sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' | grep -E 'COPILOT|CLAUDE|PLUGIN|VSCODE|AI_AGENT' | grep -v '^PROBE_' | sort -u | tr '\n' ' ')"

    argv_json="["
    sep=""
    for a in "$@"; do argv_json="$argv_json$sep\"$(jesc "$a")\""; sep=","; done
    argv_json="$argv_json]"

    os="$(uname -s 2>/dev/null)"
    cwd="$(pwd)"
    wb="$(command -v bash 2>/dev/null)"

    py=""
    for c in python3 python; do
        if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import json' >/dev/null 2>&1 </dev/null; then
            py="$c"; break
        fi
    done

    line=""
    if [ -n "$py" ]; then
        line="$(MSYS2_ENV_CONV_EXCL='PROBE_' \
            PROBE_STDIN="${raw:0:30000}" PROBE_STDIN_LEN="${#raw}" \
            PROBE_SET_NAMES="$set_names" PROBE_MATCH_NAMES="$match_names" \
            PROBE_OS="$os" PROBE_SRC="$src" PROBE_EV="$ev" PROBE_KEY="$key" \
            PROBE_ARGV_JSON="$argv_json" PROBE_CWD="$cwd" PROBE_WHICH_BASH="$wb" \
            PROBE_BASH="${BASH:-}" PROBE_SELF="$self" PROBE_ROOT="$root" \
            PROBE_STAGE="$PROBE_STAGE" PROBE_STDOUT="$out" \
            "$py" -I "$sdir/record.py" </dev/null)"
    fi
    if [ -z "$line" ]; then
        # No python (or it failed): build the line in bash; keys via jq if present.
        keys="[]"
        if command -v jq >/dev/null 2>&1; then
            keys="$(printf '%s' "$raw" | jq -c 'if type=="object" then keys_unsorted else [] end' 2>/dev/null)" || keys="[]"
            [ -n "$keys" ] || keys="[]"
        fi
        envj="{"; sep=""
        for n in $set_names; do
            eval "v=\"\${PROBE_E_$n}\""
            envj="$envj$sep\"$n\":\"$(jesc "$v")\""; sep=","
        done
        envj="$envj}"
        ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        line="{\"ts\":\"$ts\",\"os\":\"$(jesc "$os")\",\"source\":\"$(jesc "$src")\",\"event\":\"$(jesc "$ev")\",\"handler_key\":\"$(jesc "$key")\",\"argv\":$argv_json,\"stdin_len\":${#raw},\"stdin_raw\":\"$(jesc "${raw:0:4000}")\",\"stdin_keys\":$keys,\"stdin_parse\":\"jq-or-none\",\"env\":$envj,\"env_names_matching\":\"$(jesc "$match_names")\",\"cwd\":\"$(jesc "$cwd")\",\"which_bash\":\"$(jesc "$wb")\",\"bash_running\":\"$(jesc "${BASH:-}")\",\"script\":\"$(jesc "$self")\",\"plugin_root\":\"$(jesc "$root")\",\"stage\":\"$(jesc "$PROBE_STAGE")\",\"stdout_emitted\":\"$(jesc "$out")\",\"recorder\":\"bash\"}"
    fi
    printf '%s\n' "$line" >> "$log"
} 2>>"$log.err"

exit 0
