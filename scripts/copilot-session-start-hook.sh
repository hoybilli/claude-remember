#!/bin/bash
# ============================================================================
# copilot-session-start-hook.sh -- Copilot SessionStart adapter (issue: vscode)
# ============================================================================
#
# Bound by hooks/hooks.copilot.json (VS Code Agents, the Copilot CLI and the
# Copilot desktop app; Claude Code never loads that file). Runs the core
# session-start-hook.sh unchanged and hands the host its recap in the one
# shape the Copilot hosts inject: a top-level {"additionalContext": "..."}
# (observed, docs/vscode-verification.md). Plain text is not injected there.
#
#   1. The agent-host prefix is stripped from the payload's session_id
#      (lib-copilot-adapter.sh); the core hook validates what is left.
#   2. The core runs with REMEMBER_SUPPRESS_PROMO=1 (its stdout is rewritten
#      here, the agy-session-start-hook.sh precedent, #596) and its stdout
#      captured to a temp file under $REMEMBER_DIR/tmp (else $TMPDIR).
#   3. python -m pipeline.copilot_recap FILE rewrites the file in place to
#      the envelope (pipeline/copilot_recap.py:envelope), then it is printed.
#   4. No python, or the helper fails: the plain recap is printed and
#      `copilot session-start: envelope failed, plain recap` is logged.
#   5. Nothing captured (the core printed nothing): nothing is printed.
#
# EXIT CODES
#   The core hook's. 0 without delegating when the adapter library is
#   missing.
# ============================================================================

_CSS_DIR="${BASH_SOURCE[0]%/*}"
[ "$_CSS_DIR" = "${BASH_SOURCE[0]}" ] && _CSS_DIR="$(pwd)"
[ -f "$_CSS_DIR/lib-copilot-adapter.sh" ] || exit 0
source "$_CSS_DIR/lib-copilot-adapter.sh" || exit 0
remember_copilot_adapter_init "${BASH_SOURCE[0]}" || exit 0

remember_copilot_read_stdin_into _CSS_STDIN
remember_copilot_strip_session_id_into _CSS_PAYLOAD "$_CSS_STDIN"
export REMEMBER_SUPPRESS_PROMO=1
_CSS_CORE="$_COPILOT_ADAPTER_DIR/session-start-hook.sh"

_CSS_OUT=""
if [ "$_COPILOT_ADAPTER_PATHS" = 1 ] && [ -d "$REMEMBER_DIR/tmp" ]; then
    _CSS_OUT="$(mktemp "$REMEMBER_DIR/tmp/copilot-session-start.XXXXXX" 2>/dev/null)" || _CSS_OUT=""
fi
if [ -z "$_CSS_OUT" ]; then
    _CSS_OUT="$(mktemp "${TMPDIR:-/tmp}/remember-copilot-session-start.XXXXXX" 2>/dev/null)" || _CSS_OUT=""
fi
if [ -z "$_CSS_OUT" ]; then
    log "hook" "copilot session-start: no temp file, plain recap"
    remember_copilot_delegate "$_CSS_CORE" "$_CSS_PAYLOAD"
    exit $?
fi

remember_copilot_delegate "$_CSS_CORE" "$_CSS_PAYLOAD" > "$_CSS_OUT"
_CSS_RC=$?
if [ -s "$_CSS_OUT" ]; then
    _CSS_WRAPPED=0
    if declare -F _remember_python >/dev/null 2>&1 && _remember_python 2>/dev/null; then
        if (cd "$_COPILOT_ADAPTER_ROOT" && _remember_run_python -m pipeline.copilot_recap "$_CSS_OUT") >/dev/null 2>&1; then
            _CSS_WRAPPED=1
        fi
    fi
    if [ "$_CSS_WRAPPED" != 1 ]; then
        log "hook" "copilot session-start: envelope failed, plain recap"
    fi
    cat "$_CSS_OUT"
fi
rm -f "$_CSS_OUT"
exit "$_CSS_RC"
