#!/bin/bash
# ============================================================================
# lib-session-id.sh -- one place to normalise a host's session id (issue: vscode)
# ============================================================================
#
# VS Code Agents (the GitHub Copilot harness inside VS Code) puts
# `agent-host-copilotcli:/<uuid>` on stdin as `session_id`. Every hook then
# runs the id through the same character allowlist
# (`''|.|..|-*|*[!A-Za-z0-9._-]*`), which rejects the colon and the slash --
# correctly: both are path hazards. The uuid after the prefix is the real id
# (it names ~/.copilot/session-state/<uuid>/), so the prefix is stripped HERE,
# before the validator, and the validator is left exactly as it was.
#
# Only the `agent-host-<tag>:/<rest>` shape is rewritten. Anything else is
# printed unchanged and left for the validator to judge.
#
# Both helpers are pure (print-only, no side effects): callers run them inside
# $(...), where an export would be lost in the subshell. Each caller sets and
# exports REMEMBER_HOST_HINT itself from remember_session_id_host_hint. The
# hint is a logging/dispatch hint only -- never a path input.
#
# USAGE
#   source "$_HOOK_DIR/lib-session-id.sh"
#   id=$(remember_normalize_session_id "$raw")
#   REMEMBER_HOST_HINT=$(remember_session_id_host_hint "$raw"); export REMEMBER_HOST_HINT
#
# Bash 3.2 safe: parameter expansion only, no regex, no arrays.
# ============================================================================

# remember_normalize_session_id RAW -> prints the id to use
remember_normalize_session_id() {
    case "$1" in
        agent-host-*:/*) printf '%s' "${1#*:/}" ;;
        *) printf '%s' "$1" ;;
    esac
}

# remember_session_id_host_hint RAW -> prints "copilot" or "" (no side effects)
remember_session_id_host_hint() {
    case "$1" in agent-host-*:/*) printf 'copilot' ;; *) printf '' ;; esac
}

# remember_copilot_transcript_for UUID -> prints the Copilot events file if it
# exists, else nothing. Mirrors pipeline/host.copilot_transcript_for().
remember_copilot_transcript_for() {
    case "$1" in
        ''|.|..|*/*|*\*|*:*) return 1 ;;
    esac
    _rc_base="${COPILOT_HOME:-${HOME:-}/.copilot}"
    _rc_path="${_rc_base%/}/session-state/$1/events.jsonl"
    [ -f "$_rc_path" ] || return 1
    printf '%s' "$_rc_path"
}
