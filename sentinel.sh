#!/usr/bin/env bash
# Throwaway probe kit: READ-ONLY fingerprint of the NEVER-TOUCH install.
#
#   sentinel.sh [--save <dir>] [<NT path>]
#
# Default NT: $HOME/.copilot/installed-plugins/_direct/Digital-Process-Tools--claude-remember--remember
# Prints: HEAD, `git worktree list`, the .remember file count, and a sha256 of
# the sorted ".remember" file list with sizes and mtimes. With --save, also
# writes that full listing to <dir>/sentinel-<UTC time>.txt so two runs can be
# compared with `diff`. Nothing under NT is written.
#
# Reading the result: HEAD and the worktree lines must be identical before and
# after. The .remember count/hash may move ONLY because a live Claude Code
# session on this project appended to its own memory (now.md, logs/...); diff
# the saved listings and check every changed path is such a file. The probe
# itself never writes under NT. If HEAD or the worktree list changed, or NT is
# missing: STOP and tell the human partner. Do not repair anything.
set -u
save=""
if [ "${1:-}" = "--save" ]; then save="${2:-}"; shift 2; fi
NT="${1:-$HOME/.copilot/installed-plugins/_direct/Digital-Process-Tools--claude-remember--remember}"

if [ ! -d "$NT" ]; then
    echo "sentinel: NT path absent: $NT"
    echo "sentinel: (expected absent on a machine without that install; otherwise STOP)"
    exit 0
fi

sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum | cut -d' ' -f1; else shasum -a 256 | cut -d' ' -f1; fi; }
statline() { # size mtime path, GNU or BSD stat
    if stat -c '%s %Y %n' "$1" >/dev/null 2>&1; then stat -c '%s %Y %n' "$1"; else stat -f '%z %m %N' "$1"; fi
}

echo "sentinel: NT=$NT"
echo "HEAD: $(git -C "$NT" rev-parse HEAD 2>&1)"
echo "worktrees:"
git -C "$NT" worktree list 2>&1 | sed 's/^/  /'
if [ -d "$NT/.remember" ]; then
    listing="$(cd "$NT" && find .remember -type f 2>/dev/null | LC_ALL=C sort | while IFS= read -r f; do statline "$f"; done)"
    count="$(printf '%s\n' "$listing" | grep -c . )"
    echo ".remember files: $count"
    echo ".remember sha256(list+size+mtime): $(printf '%s\n' "$listing" | sha)"
    if [ -n "$save" ]; then
        mkdir -p "$save"
        out="$save/sentinel-$(date -u +%Y%m%dT%H%M%SZ).txt"
        {
            echo "HEAD: $(git -C "$NT" rev-parse HEAD 2>&1)"
            git -C "$NT" worktree list 2>&1
            printf '%s\n' "$listing"
        } > "$out"
        echo "saved: $out"
    fi
else
    echo ".remember: MISSING  <-- STOP and tell the human partner"
fi
