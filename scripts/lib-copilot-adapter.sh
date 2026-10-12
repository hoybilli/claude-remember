#!/bin/bash
# ============================================================================
# lib-copilot-adapter.sh -- shared code of the Copilot hook wrappers (issue: vscode)
# ============================================================================
#
# The three scripts/copilot-*-hook.sh wrappers are bound by
# hooks/hooks.copilot.json, which only the Copilot hosts load (root
# plugin.json). Each rewrites the Copilot payload and delegates to one of the
# four core hooks unchanged -- the pattern scripts/agy-*-hook.sh set for
# Antigravity. The core hooks never source this file.
#
# What a wrapper does with it:
#   source "$dir/lib-copilot-adapter.sh" || exit 0
#   remember_copilot_adapter_init "${BASH_SOURCE[0]}" || exit 0
#   remember_copilot_read_stdin_into payload
#   remember_copilot_strip_session_id_into payload "$payload"
#   remember_copilot_delegate "$_COPILOT_ADAPTER_DIR/<core>-hook.sh" "$payload"
#
# Every function writes its answer into the variable named by its first
# argument (printf -v) instead of printing it: no command substitution on the
# per-call path (docs/windows.md, #511). Only remember_copilot_read_stdin_into
# forks, once, for `cat`.
#
# Bash 3.2 safe: parameter expansion, `[ ]` and `[[ == ]]` glob tests, printf
# -v; no `case` (the release-tree checker refuses it), no regex, no arrays.
# The double quote and the backslash are held in variables (octal 042 and
# 134), the spelling upstream's own extractor uses (#898 round 8). A pattern
# substitution with a quoted replacement is never itself double-quoted
# (x=${v//"$a"/"$b"}, as log.sh does): before bash 4.3 the quotes of the
# replacement would survive into the result. Where the replacement holds
# backslashes (the JSON path escape) there is no replacement at all: a loop
# of prefix and suffix removals, which no bash version reads differently.
# ============================================================================

# remember_copilot_adapter_init SCRIPT -> 0, or 1 when the wrapper must exit
# without delegating (its own directory or lib-session-id.sh is unusable).
# SCRIPT is the wrapper's own BASH_SOURCE[0]. Sets _COPILOT_ADAPTER_DIR (the
# scripts directory, absolute) and _COPILOT_ADAPTER_ROOT (its parent, the
# plugin root); exports CLAUDE_PLUGIN_ROOT and PLUGIN_ROOT as that root, so a
# backslash PLUGIN_ROOT from the host cannot win in resolve-paths.sh, and
# REMEMBER_HOST_HINT=copilot (only the Copilot hosts load these wrappers).
# Then sources detect-tools.sh (lazy python), resolve-paths.sh,
# bootstrap-dirs.sh and log.sh as the core hooks do. A resolve-paths.sh
# failure is not fatal: _COPILOT_ADAPTER_PATHS stays 0, REMEMBER_DIR and
# config_into are then not the store's, and the wrapper still delegates so
# the core hook decides (it can resolve the project from the payload's cwd
# where this cannot). log is always defined afterwards.
remember_copilot_adapter_init() {
    local _rca_src="$1" _rca_dir
    _rca_dir="${_rca_src%/*}"
    [ "$_rca_dir" = "$_rca_src" ] && _rca_dir="$(pwd)"
    _rca_dir="$(cd "$_rca_dir" 2>/dev/null && pwd)" || return 1
    [ -f "$_rca_dir/lib-session-id.sh" ] || return 1
    _COPILOT_ADAPTER_DIR="$_rca_dir"
    _COPILOT_ADAPTER_ROOT="${_rca_dir%/*}"
    CLAUDE_PLUGIN_ROOT="$_COPILOT_ADAPTER_ROOT"
    PLUGIN_ROOT="$_COPILOT_ADAPTER_ROOT"
    REMEMBER_HOST_HINT=copilot
    export CLAUDE_PLUGIN_ROOT PLUGIN_ROOT REMEMBER_HOST_HINT
    source "$_rca_dir/lib-session-id.sh" || return 1
    _COPILOT_ADAPTER_PATHS=0
    _REMEMBER_LAZY_PYTHON=1
    source "$_rca_dir/detect-tools.sh"
    if REMEMBER_PATHS_SOFT_FAIL=1 source "$_rca_dir/resolve-paths.sh" 2>/dev/null; then
        source "$_rca_dir/bootstrap-dirs.sh"
        source "$_rca_dir/log.sh" 2>/dev/null
        _COPILOT_ADAPTER_PATHS=1
    fi
    declare -F log >/dev/null 2>&1 || log() { :; }
    return 0
}

# remember_copilot_read_stdin_into VAR -> VAR = stdin as `$(cat)` yields it:
# every byte except trailing newlines, which command substitution drops (and
# NUL bytes, which a bash variable cannot hold). "" when stdin is a terminal
# (never read: it would wait for a person). Returns 0.
remember_copilot_read_stdin_into() {
    local _rcr_val=""
    if [ ! -t 0 ]; then
        _rcr_val="$(cat)"
    fi
    printf -v "$1" '%s' "$_rcr_val"
    return 0
}

# remember_copilot_field_into VAR FIELD PAYLOAD -> VAR = the string value of
# the FIRST "FIELD" key in PAYLOAD, with each JSON `\\` read as `\`; returns 1
# (VAR empty) when the key is absent, not followed by only a colon and
# whitespace, or its value is empty. The same deliberately narrow first-match
# reading the core hooks' own _stdin_json_string does (no escaped quotes), so
# a wrapper and its core hook always agree on which value they read.
remember_copilot_field_into() {
    local _rcf_var="$1" _rcf_dq _rcf_bs _rcf_quoted _rcf_rest _rcf_sep _rcf_val
    printf -v "$_rcf_var" '%s' ""
    printf -v _rcf_dq '\042'
    printf -v _rcf_bs '\134'
    _rcf_quoted="$_rcf_dq$2$_rcf_dq"
    [[ "$3" == *"$_rcf_quoted"* ]] || return 1
    _rcf_rest="${3#*"$_rcf_quoted"}"
    [[ "$_rcf_rest" == *"$_rcf_dq"* ]] || return 1
    _rcf_sep="${_rcf_rest%%"$_rcf_dq"*}"
    if [[ "$_rcf_sep" == *[!:[:space:]]* ]]; then
        return 1
    fi
    _rcf_val="${_rcf_rest#*"$_rcf_dq"}"
    _rcf_val="${_rcf_val%%"$_rcf_dq"*}"
    _rcf_val=${_rcf_val//"$_rcf_bs$_rcf_bs"/"$_rcf_bs"}
    [ -n "$_rcf_val" ] || return 1
    printf -v "$_rcf_var" '%s' "$_rcf_val"
    return 0
}

# remember_copilot_session_id_into VAR PAYLOAD -> VAR = the first
# "session_id" string value (see remember_copilot_field_into); 1 if none.
remember_copilot_session_id_into() {
    remember_copilot_field_into "$1" session_id "$2"
}

# remember_copilot_strip_session_id_into VAR PAYLOAD -> VAR = PAYLOAD with
# the agent-host prefix (remember_session_id_resolve, lib-session-id.sh)
# removed from the first "session_id" string value, every other byte
# unchanged. A payload without that key, or whose first value carries no
# prefix, comes back as it was. Validation is left to the core hook.
# Returns 0.
remember_copilot_strip_session_id_into() {
    local _rcs_var="$1" _rcs_in="$2" _rcs_dq _rcs_quoted _rcs_head _rcs_rest _rcs_sep
    local _rcs_after _rcs_val _rcs_tail
    printf -v "$_rcs_var" '%s' "$_rcs_in"
    printf -v _rcs_dq '\042'
    _rcs_quoted="${_rcs_dq}session_id${_rcs_dq}"
    [[ "$_rcs_in" == *"$_rcs_quoted"* ]] || return 0
    _rcs_head="${_rcs_in%%"$_rcs_quoted"*}"
    _rcs_rest="${_rcs_in#*"$_rcs_quoted"}"
    [[ "$_rcs_rest" == *"$_rcs_dq"* ]] || return 0
    _rcs_sep="${_rcs_rest%%"$_rcs_dq"*}"
    if [[ "$_rcs_sep" == *[!:[:space:]]* ]]; then
        return 0
    fi
    _rcs_after="${_rcs_rest#*"$_rcs_dq"}"
    [[ "$_rcs_after" == *"$_rcs_dq"* ]] || return 0
    _rcs_val="${_rcs_after%%"$_rcs_dq"*}"
    _rcs_tail="${_rcs_after#*"$_rcs_dq"}"
    remember_session_id_resolve "$_rcs_val"
    [ -n "$REMEMBER_SESSION_ID_HINT" ] || return 0
    printf -v "$_rcs_var" '%s%s%s%s%s%s%s' "$_rcs_head" "$_rcs_quoted" "$_rcs_sep" \
        "$_rcs_dq" "$REMEMBER_SESSION_ID_NORMALIZED" "$_rcs_dq" "$_rcs_tail"
    return 0
}

# remember_copilot_prepend_field_into VAR PAYLOAD FIELD VALUE -> VAR = PAYLOAD
# with "FIELD":"VALUE" inserted as the first member of its object (right after
# the first `{`; a comma follows unless the object was empty). VALUE must
# already be JSON-escaped (remember_copilot_json_escape_path_into). First,
# because the core hooks read the first occurrence of a field: a tool_input
# that carries the same field further on cannot shadow it. Returns 1, VAR =
# PAYLOAD unchanged, when PAYLOAD does not start with `{` (after whitespace).
remember_copilot_prepend_field_into() {
    local _rcp_var="$1" _rcp_in="$2" _rcp_dq _rcp_lead _rcp_body _rcp_trim _rcp_comma=","
    printf -v "$_rcp_var" '%s' "$_rcp_in"
    [[ "$_rcp_in" == *"{"* ]] || return 1
    _rcp_lead="${_rcp_in%%"{"*}"
    if [[ "$_rcp_lead" == *[![:space:]]* ]]; then
        return 1
    fi
    _rcp_body="${_rcp_in#*"{"}"
    _rcp_trim="${_rcp_body#"${_rcp_body%%[![:space:]]*}"}"
    [[ "$_rcp_trim" == "}"* ]] && _rcp_comma=""
    printf -v _rcp_dq '\042'
    printf -v "$_rcp_var" '%s{%s%s%s:%s%s%s%s%s' "$_rcp_lead" "$_rcp_dq" "$3" "$_rcp_dq" \
        "$_rcp_dq" "$4" "$_rcp_dq" "$_rcp_comma" "$_rcp_body"
    return 0
}

# remember_copilot_json_escape_path_into VAR PATH -> VAR = PATH with every
# backslash doubled (a Windows path inside a JSON string), returns 0; or
# VAR = "" and returns 1 when PATH holds a double quote, which the core
# hooks' first-match reader cannot carry.
remember_copilot_json_escape_path_into() {
    local _rce_dq _rce_bs _rce_rest="$2" _rce_val=""
    printf -v "$1" '%s' ""
    printf -v _rce_dq '\042'
    printf -v _rce_bs '\134'
    [[ "$2" == *"$_rce_dq"* ]] && return 1
    while [[ "$_rce_rest" == *"$_rce_bs"* ]]; do
        _rce_val="$_rce_val${_rce_rest%%"$_rce_bs"*}$_rce_bs$_rce_bs"
        _rce_rest="${_rce_rest#*"$_rce_bs"}"
    done
    printf -v "$1" '%s' "$_rce_val$_rce_rest"
    return 0
}

# remember_copilot_scan_owner_check UUID -> 0, always. Upstream #913 keeps
# the core post-tool hook's incremental line count in
# $REMEMBER_DIR/tmp/transcript-scan.<transcript basename minus .jsonl>. Every
# Copilot transcript is named events.jsonl, so all Copilot sessions of one
# project share transcript-scan.events, and the core only checks that the byte
# at the stored offset is a newline -- another session's file can pass that,
# and the count is then wrong for the rest of that session. This records the
# session that last scanned in $REMEMBER_DIR/tmp/copilot-scan-owner; when
# UUID is another one it empties transcript-scan.events, which the core reads
# as "no count" and rebuilds with one full scan. It couples the adapter to
# that core file name: if upstream re-keys the file, this empties a file
# nothing reads, and the tests in test_post_tool_copilot_transcript_vscode.py
# that read transcript-scan.events fail and say so. Runs only when the store
# resolved (_COPILOT_ADAPTER_PATHS=1). No fork: `read` and redirections are
# builtins. Each `2>/dev/null` comes BEFORE its redirection, so a file that
# cannot be opened costs a rescan and leaves no line in hook-errors.log
# (bootstrap-dirs.sh sends the hooks' stderr there). Two Copilot chats of one
# project scanning at the same moment can still interleave (narrow; only an
# upstream re-key removes it).
remember_copilot_scan_owner_check() {
    [ "${_COPILOT_ADAPTER_PATHS:-0}" = 1 ] || return 0
    local _rco_tmp="$REMEMBER_DIR/tmp" _rco_prev=""
    [ -d "$_rco_tmp" ] || return 0
    if [ -f "$_rco_tmp/copilot-scan-owner" ]; then
        read -r _rco_prev 2>/dev/null < "$_rco_tmp/copilot-scan-owner"
    fi
    [ "$_rco_prev" = "$1" ] && return 0
    if [ -f "$_rco_tmp/transcript-scan.events" ]; then
        : 2>/dev/null > "$_rco_tmp/transcript-scan.events"
    fi
    printf '%s\n' "$1" 2>/dev/null > "$_rco_tmp/copilot-scan-owner"
    return 0
}

# remember_copilot_delegate CORE PAYLOAD -> runs `bash CORE` with exactly
# PAYLOAD's bytes on its stdin (closed after, so the core's `read -t 1` sees
# EOF at once) and returns CORE's exit status. CORE's stdout and stderr are
# the caller's.
remember_copilot_delegate() {
    printf '%s' "$2" | bash "$1"
}
