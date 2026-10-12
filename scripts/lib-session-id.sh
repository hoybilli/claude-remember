#!/bin/bash
# ============================================================================
# lib-session-id.sh -- Copilot session ids and session-state paths (issue: vscode)
# ============================================================================
#
# VS Code Agents (the GitHub Copilot harness inside VS Code) has been seen to
# put `agent-host-copilotcli:/<uuid>` in `session_id` (observed in the
# extension-host hook log, 2026-09-29), and a bare `<uuid>` in the plugin-hook
# payload (observed 2026-09-30, VS Code 1.139.1 / Windows 11). The uuid after
# the prefix is the real id: it names ~/.copilot/session-state/<uuid>/.
#
# Sourced by scripts/lib-copilot-adapter.sh (the Copilot hook wrappers) and
# scripts/doctor.sh. The four core hooks do not source it: the wrappers strip
# the prefix from the payload before a core hook's own validator
# (`''|.|..|-*|*[!A-Za-z0-9._-]*`) sees the id, and that validator is left
# exactly as upstream wrote it.
#
# Bash 3.2 safe: parameter expansion, `[ ]` and `[[ == ]]` glob tests only
# (no `case`: the release-tree checker refuses it), no regex, no arrays, no
# command substitution (docs/windows.md, #511).
# ============================================================================

# remember_session_id_resolve RAW -> sets REMEMBER_SESSION_ID_NORMALIZED to
# RAW with one leading `agent-host-<tag>:/` removed, and
# REMEMBER_SESSION_ID_HINT to "copilot" iff that prefix was there, else "".
# Both are overwritten on every call. Always returns 0. Anything without the
# prefix passes through unchanged, left for the core hook's validator.
remember_session_id_resolve() {
    REMEMBER_SESSION_ID_HINT=""
    REMEMBER_SESSION_ID_NORMALIZED="$1"
    if [[ "$1" == agent-host-*:/* ]]; then
        REMEMBER_SESSION_ID_NORMALIZED="${1#*:/}"
        REMEMBER_SESSION_ID_HINT=copilot
    fi
    return 0
}

# remember_copilot_transcript_into UUID -> sets REMEMBER_COPILOT_TRANSCRIPT to
# the Copilot events file and returns 0 if it exists; else sets it to "" and
# returns 1. No command substitution. Mirrors
# pipeline/host.copilot_transcript_for().
#
# The id guard refuses '', '.', '..' and anything holding '/', '\' or ':'.
# `-z "${1#.}"` is true for '' and '.', `-z "${1#..}"` for '' and '..'
# (upstream's own spelling, doctor.sh). All `[[ ]]`: a keyword, so the guard
# holds even where `[` is shadowed (tests/test_post_tool_copilot_transcript_
# vscode.py shadows it to prove the guard alone refuses).
#
# The directory is remember_copilot_state_dir_into's, below.
remember_copilot_transcript_into() {
    REMEMBER_COPILOT_TRANSCRIPT=""
    if [[ -z "${1#.}" ]] || [[ -z "${1#..}" ]] || [[ "$1" == */* ]] \
        || [[ "$1" == *\\* ]] || [[ "$1" == *:* ]]; then
        return 1
    fi
    remember_copilot_state_dir_into
    local _rc_path="$REMEMBER_COPILOT_STATE_DIR/$1/events.jsonl"
    [ -f "$_rc_path" ] || return 1
    REMEMBER_COPILOT_TRANSCRIPT="$_rc_path"
    return 0
}

# remember_copilot_state_dir_into -> sets REMEMBER_COPILOT_STATE_DIR to
# <base>/session-state. Always returns 0. No command substitution.
#
# The base is COPILOT_HOME when it is non-empty, else $HOME/.copilot -- an
# empty COPILOT_HOME falls back (tests/test_copilot_home_fallback_vscode.py)
# -- with one trailing '/' dropped. doctor.sh prints this directory and
# resolves the last save's transcript under it, so doctor and the hooks agree
# on where it is.
remember_copilot_state_dir_into() {
    if [ -n "${COPILOT_HOME:-}" ]; then
        REMEMBER_COPILOT_STATE_DIR=$COPILOT_HOME
    else
        REMEMBER_COPILOT_STATE_DIR="${HOME:-}/.copilot"
    fi
    REMEMBER_COPILOT_STATE_DIR="${REMEMBER_COPILOT_STATE_DIR%/}/session-state"
    return 0
}
