#!/bin/bash
# ============================================================================
# lib-session-id.sh -- one place to normalise a host's session id (issue: vscode)
# ============================================================================
#
# VS Code Agents (the GitHub Copilot harness inside VS Code) has been seen to
# put `agent-host-copilotcli:/<uuid>` in `session_id` (observed in the
# extension-host hook log, 2026-09-29), and a bare `<uuid>` in the plugin-hook
# payload (observed 2026-09-30, VS Code 1.139.1 / Windows 11). Both are
# handled: the prefix is stripped, and the host hint also reads the
# environment, because the bare form carries no prefix to key on. The hooks
# that source this file (post-tool-hook.sh, session-end-hook.sh) then run the
# id through the same character allowlist
# (`''|.|..|-*|*[!A-Za-z0-9._-]*`), which rejects the colon and the slash --
# correctly: both are path hazards. The uuid after the prefix is the real id
# (it names ~/.copilot/session-state/<uuid>/), so the prefix is stripped HERE,
# before the validator, and the validator is left exactly as it was.
#
# Only the `agent-host-<tag>:/<rest>` shape is rewritten. Anything else is
# passed through unchanged and left for the validator to judge.
#
# remember_session_id_resolve sets REMEMBER_SESSION_ID_NORMALIZED and
# REMEMBER_SESSION_ID_HINT in the caller's own shell instead of printing them:
# post-tool-hook.sh runs on every tool call, and each $(...) is a fork that
# docs/windows.md (#511) measured as slow on Git Bash. Each of those hooks
# sets and exports REMEMBER_HOST_HINT itself from the result. The hint is a
# logging/dispatch hint only -- never a path input.
#
# session-start-hook.sh does NOT source this file (it kept the compiled hook
# over v0.40.0's size budget). It inlines only the prefix half -- the same
# strip, and REMEMBER_HOST_HINT=copilot when the prefix was there -- and the
# environment half is applied by pipeline.host.copilot_session(), the same
# rule in Python, in the one helper call that hook makes
# (pipeline/copilot_recap.py). tests/test_session_start_host_rule_vscode.py
# compares the two against remember_session_id_resolve below.
#
# USAGE
#   source "$_HOOK_DIR/lib-session-id.sh"
#   remember_session_id_resolve "$raw"
#   id=$REMEMBER_SESSION_ID_NORMALIZED
#   REMEMBER_HOST_HINT=$REMEMBER_SESSION_ID_HINT; export REMEMBER_HOST_HINT
#
# Bash 3.2 safe: parameter expansion, `[ ]` and `[[ == ]]` glob tests only
# (no `case`: v0.40.0's release-tree checker refuses it), no regex, no
# arrays, no printf -v.
# ============================================================================

# remember_session_id_resolve RAW -> sets REMEMBER_SESSION_ID_NORMALIZED (the
# id to use) and REMEMBER_SESSION_ID_HINT ("copilot" or ""); both are
# overwritten on every call. Always returns 0. No command substitution.
#
# THE HOST RULE -- described here once; pipeline.host.copilot_session(), the
# session-start hook and pipeline/copilot_recap.py point back to it.
# The hint is "copilot" when RAW has the agent-host prefix, or when the
# environment carries Copilot's signature and no signature of a host that
# pipeline/host.REGISTRY lists before COPILOT -- the same first-match order
# pipeline.host.detect_host uses. Names, from pipeline/host.py:
#   CLAUDE_CODE.signature_vars  CLAUDE_CODE_ENTRYPOINT, CLAUDE_CODE_SESSION_ID
#   CODEX.signature_vars        CODEX_SESSION_ID, CODEX_THREAD_ID
#   ANTIGRAVITY.signature_vars  ANTIGRAVITY_CONVERSATION_ID
#   COPILOT.signature_vars      COPILOT_CLI, COPILOT_PLUGIN_ROOT
# so a Codex or Antigravity session whose environment also carries COPILOT_CLI
# keeps its own plain-text recap. COPILOT_HOME is deliberately not consulted:
# a configuration path a user may set anywhere is not a signature (#463).
# A signature counts when it is non-empty, whitespace included (`[ -n ]`) --
# unlike detect_host, which strips. The Python side applies the same rule
# from REGISTRY, and first accepts REMEMBER_HOST_HINT=copilot, which every
# hook exports: the prefix half alone (session-start) or this resolver's whole
# answer (post-tool, session-end).
remember_session_id_resolve() {
    REMEMBER_SESSION_ID_HINT=""
    if [[ "$1" == agent-host-*:/* ]]; then
        REMEMBER_SESSION_ID_NORMALIZED="${1#*:/}"
        REMEMBER_SESSION_ID_HINT=copilot
        return 0
    fi
    REMEMBER_SESSION_ID_NORMALIZED="$1"
    if [ -z "${CLAUDE_CODE_ENTRYPOINT:-}" ] && [ -z "${CLAUDE_CODE_SESSION_ID:-}" ] \
        && [ -z "${CODEX_SESSION_ID:-}" ] && [ -z "${CODEX_THREAD_ID:-}" ] \
        && [ -z "${ANTIGRAVITY_CONVERSATION_ID:-}" ] \
        && { [ -n "${COPILOT_CLI:-}" ] || [ -n "${COPILOT_PLUGIN_ROOT:-}" ]; }; then
        REMEMBER_SESSION_ID_HINT=copilot
    fi
    return 0
}

# remember_copilot_transcript_into UUID -> sets REMEMBER_COPILOT_TRANSCRIPT to
# the Copilot events file and returns 0 if it exists; else sets it to "" and
# returns 1. No command substitution. Mirrors
# pipeline/host.copilot_transcript_for().
#
# The id guard refuses '', '.', '..' and anything holding '/', '\' or ':'
# (it was a `case`, which v0.40.0's release-tree checker refuses).
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
# empty COPILOT_HOME falls back, as the `${COPILOT_HOME:-...}` default it
# replaces did (tests/test_copilot_home_fallback_vscode.py) -- with one
# trailing '/' dropped. doctor.sh prints this directory and resolves the last
# save's transcript under it, so doctor and the hooks agree on where it is.
remember_copilot_state_dir_into() {
    if [ -n "${COPILOT_HOME:-}" ]; then
        REMEMBER_COPILOT_STATE_DIR=$COPILOT_HOME
    else
        REMEMBER_COPILOT_STATE_DIR="${HOME:-}/.copilot"
    fi
    REMEMBER_COPILOT_STATE_DIR="${REMEMBER_COPILOT_STATE_DIR%/}/session-state"
    return 0
}
