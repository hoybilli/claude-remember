#!/bin/bash
# ============================================================================
# copilot-post-tool-hook.sh -- Copilot PostToolUse adapter (issue: vscode)
# ============================================================================
#
# Bound by hooks/hooks.copilot.json (Copilot hosts only). The Copilot hosts
# send no transcript_path; the session's own transcript is
# <COPILOT_HOME or ~/.copilot>/session-state/<uuid>/events.jsonl
# (lib-session-id.sh, remember_copilot_transcript_into). When it exists, this
# adds "transcript_path":"<that path, JSON-escaped>" as the FIRST key of the
# payload -- the core hook reads the first occurrence of a key -- and
# delegates to the core post-tool-hook.sh unchanged, which then uses the
# file as the transcript, trusts the payload's session_id and exports
# REMEMBER_TRANSCRIPT_PATH for the save. No file, or a path holding a double
# quote: the payload goes through with only the session-id prefix stripped,
# and the core takes its own fallback.
#
# Every Copilot transcript is named events.jsonl, so the core's per-transcript
# line-count file (#913, $REMEMBER_DIR/tmp/transcript-scan.events) is shared
# by all Copilot sessions of a project; remember_copilot_scan_owner_check
# (lib-copilot-adapter.sh) empties it when the session changes, so each
# session's count starts from its own file. That couples this wrapper to the
# core's file name (see the function's comment).
#
# EXIT CODES
#   The core hook's. 0 without delegating when the adapter library is
#   missing.
# ============================================================================

_CPT_DIR="${BASH_SOURCE[0]%/*}"
[ "$_CPT_DIR" = "${BASH_SOURCE[0]}" ] && _CPT_DIR="$(pwd)"
[ -f "$_CPT_DIR/lib-copilot-adapter.sh" ] || exit 0
source "$_CPT_DIR/lib-copilot-adapter.sh" || exit 0
remember_copilot_adapter_init "${BASH_SOURCE[0]}" || exit 0

remember_copilot_read_stdin_into _CPT_STDIN
remember_copilot_strip_session_id_into _CPT_PAYLOAD "$_CPT_STDIN"
remember_copilot_session_id_into _CPT_ID "$_CPT_PAYLOAD" || _CPT_ID=""
if [ -n "$_CPT_ID" ] && remember_copilot_transcript_into "$_CPT_ID"; then
    if remember_copilot_json_escape_path_into _CPT_ESCAPED "$REMEMBER_COPILOT_TRANSCRIPT"; then
        remember_copilot_prepend_field_into _CPT_PAYLOAD "$_CPT_PAYLOAD" transcript_path "$_CPT_ESCAPED"
        remember_copilot_scan_owner_check "$_CPT_ID"
    fi
fi
remember_copilot_delegate "$_COPILOT_ADAPTER_DIR/post-tool-hook.sh" "$_CPT_PAYLOAD"
exit $?
