#!/bin/bash
# ============================================================================
# copilot-session-end-hook.sh -- Copilot SessionEnd adapter (issue: vscode)
# ============================================================================
#
# Bound by hooks/hooks.copilot.json (Copilot hosts only). VS Code Agents and
# the desktop app send SessionEnd with reason=complete after EVERY turn
# (observed), so the core session-end-hook.sh runs one forced save -- one
# summarizer call -- per turn. cooldowns.turn_end_debounce_seconds (default
# 0: delegate at once, every turn) trades that for one save N seconds after
# the last turn of a burst:
#
#   - applies iff reason=complete, the payload's session_id (prefix
#     stripped) is uuid-shaped (hex digits and dashes, no leading dash: it
#     names the token file), and the key reads as N > 0. Not a plain
#     non-negative integer, or ten digits or more: 0. Above 3600: 3600,
#     logged.
#   - writes a fresh token to $REMEMBER_DIR/tmp/turn-end.<id> and detaches a
#     copy of itself (--sleeper N STAMP, payload in
#     REMEMBER_SESSION_END_PAYLOAD as the core itself passes it) and exits.
#   - the sleeper waits N seconds, in steps of at most 5, and stands down as
#     soon as the file no longer holds its token (a later turn wrote one);
#     else it removes the file and delegates the payload to the core hook.
#
# During the wait the core's tmp/save-session.pid is not held, so PostToolUse
# delta saves are not paused (accepted, docs/superpowers/specs/
# 2026-10-08-copilot-adapter-layer-design.md 4.4).
#
# EXIT CODES
#   The core hook's when it delegates at once; 0 when it defers, and 0
#   without delegating when the adapter library is missing.
# ============================================================================

_CSE_DIR="${BASH_SOURCE[0]%/*}"
[ "$_CSE_DIR" = "${BASH_SOURCE[0]}" ] && _CSE_DIR="$(pwd)"
[ -f "$_CSE_DIR/lib-copilot-adapter.sh" ] || exit 0
source "$_CSE_DIR/lib-copilot-adapter.sh" || exit 0
remember_copilot_adapter_init "${BASH_SOURCE[0]}" || exit 0

_CSE_CORE="$_COPILOT_ADAPTER_DIR/session-end-hook.sh"

if [ "${1:-}" = "--sleeper" ]; then
    _CSE_PAYLOAD="${REMEMBER_SESSION_END_PAYLOAD:-}"
    unset REMEMBER_SESSION_END_PAYLOAD
    _CSE_LEFT="${2:-0}"
    _CSE_STAMP="${3:-}"
    if [ -z "$_CSE_LEFT" ] || [[ "$_CSE_LEFT" == *[!0-9]* ]] || [ "${#_CSE_LEFT}" -ge 10 ]; then
        _CSE_LEFT=0
    fi
    _CSE_LEFT=$((10#$_CSE_LEFT))
    remember_copilot_session_id_into _CSE_ID "$_CSE_PAYLOAD" || exit 0
    _CSE_FILE="$REMEMBER_DIR/tmp/turn-end.$_CSE_ID"
    while [ "$_CSE_LEFT" -gt 0 ]; do
        _CSE_STEP=5
        [ "$_CSE_LEFT" -lt 5 ] && _CSE_STEP="$_CSE_LEFT"
        sleep "$_CSE_STEP"
        _CSE_LEFT=$((10#$_CSE_LEFT - 10#$_CSE_STEP))
        _CSE_SEEN=""
        { read -r _CSE_SEEN < "$_CSE_FILE"; } 2>/dev/null
        if [ "$_CSE_SEEN" != "$_CSE_STAMP" ]; then
            log "hook" "copilot session-end: turn-end save superseded by a later turn"
            exit 0
        fi
    done
    _CSE_SEEN=""
    { read -r _CSE_SEEN < "$_CSE_FILE"; } 2>/dev/null
    if [ "$_CSE_SEEN" != "$_CSE_STAMP" ]; then
        log "hook" "copilot session-end: turn-end save superseded by a later turn"
        exit 0
    fi
    rm -f "$_CSE_FILE"
    remember_copilot_delegate "$_CSE_CORE" "$_CSE_PAYLOAD"
    exit $?
fi

remember_copilot_read_stdin_into _CSE_STDIN
remember_copilot_strip_session_id_into _CSE_PAYLOAD "$_CSE_STDIN"
remember_copilot_session_id_into _CSE_ID "$_CSE_PAYLOAD" || _CSE_ID=""
remember_copilot_field_into _CSE_REASON reason "$_CSE_PAYLOAD" || _CSE_REASON=""

_CSE_N=0
if [ "$_CSE_REASON" = complete ] && [ -n "$_CSE_ID" ] \
    && [[ "$_CSE_ID" != *[!0-9A-Fa-f-]* ]] && [ "${_CSE_ID#-}" = "$_CSE_ID" ] \
    && [ "$_COPILOT_ADAPTER_PATHS" = 1 ] && declare -F config_into >/dev/null 2>&1; then
    config_into _CSE_N ".cooldowns.turn_end_debounce_seconds" 0
    if [ -z "$_CSE_N" ] || [[ "$_CSE_N" == *[!0-9]* ]] || [ "${#_CSE_N}" -ge 10 ]; then
        _CSE_N=0
    fi
    _CSE_N=$((10#$_CSE_N))
    if [ "$_CSE_N" -gt 3600 ]; then
        log "hook" "copilot session-end: turn_end_debounce_seconds clamped to 3600"
        _CSE_N=3600
    fi
fi

if [ "$_CSE_N" -gt 0 ]; then
    _CSE_FILE="$REMEMBER_DIR/tmp/turn-end.$_CSE_ID"
    _CSE_STAMP="$$-$RANDOM-$(date +%s)"
    if { printf '%s\n' "$_CSE_STAMP" > "$_CSE_FILE"; } 2>/dev/null; then
        log "hook" "copilot session-end: turn-end save deferred ${_CSE_N}s (cooldowns.turn_end_debounce_seconds)"
        REMEMBER_SESSION_END_PAYLOAD="$_CSE_PAYLOAD" \
            nohup bash "${BASH_SOURCE[0]}" --sleeper "$_CSE_N" "$_CSE_STAMP" </dev/null >/dev/null 2>&1 &
        disown 2>/dev/null || true
        exit 0
    fi
    log "hook" "copilot session-end: could not write token, turn-end save not deferred"
fi

remember_copilot_delegate "$_CSE_CORE" "$_CSE_PAYLOAD"
exit $?
