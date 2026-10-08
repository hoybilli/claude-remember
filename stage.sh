#!/usr/bin/env bash
# Throwaway probe kit: stage a copy of remember-probe/ for one variant.
#
#   stage.sh <variant> <dest> [options]
#
# variants (which manifests stay in the copy):
#   all          .claude-plugin/plugin.json + plugin.json + .github/plugin/plugin.json
#   claude+gh    .claude-plugin/plugin.json + .github/plugin/plugin.json
#   claude+root  .claude-plugin/plugin.json + plugin.json
#   gh-only      .github/plugin/plugin.json
#   root-only    plugin.json
#   claude-only  .claude-plugin/plugin.json
# options:
#   --camel               Copilot hooks files use camelCase event keys
#                         (sessionStart, userPromptSubmitted, postToolUse, sessionEnd)
#   --root chain|env|rel  how Copilot handler strings reach the plugin root
#                         chain (default): ${COPILOT_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${PLUGIN_ROOT:-.}}}
#                         env:   ${CLAUDE_PLUGIN_ROOT} / $env:CLAUDE_PLUGIN_ROOT only (the port's form)
#                         rel:   ./scripts/... relative to the hook's cwd
#   --no-command          leave the "command" fallback key out of Copilot handlers
#   --claudefmt-out envelope|plain|hso
#                         SessionStart stdout of the Claude-format path
#                         (default envelope = {"additionalContext": TOKEN}, the port's shape)
#   --claude-hooks-key    add "hooks": "./hooks/hooks.json" to .claude-plugin/plugin.json
#                         (the real plugin has no such key; default leaves it out)
#   --prune               delete hooks files no remaining manifest names
#   --marketplace         <dest> becomes a local marketplace dir: the plugin goes to
#                         <dest>/remember-probe and <dest>/.claude-plugin/marketplace.json
#                         names it (marketplace "remember-probe-local")
#   --log <path>          probe log path (default: <plugin dir>/../probe-log.jsonl)
#
# Refuses an existing <dest>, and any <dest> under ~/.copilot, ~/.claude, a
# git work tree, or the NEVER-TOUCH path. Prints the tokens for this stage.
set -eu
K="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$K/remember-probe"

die() { echo "stage.sh: $*" >&2; exit 2; }
[ $# -ge 2 ] || { sed -n '2,35p' "${BASH_SOURCE[0]}"; exit 2; }
variant="$1"; dest="$2"; shift 2
casing=pascal; rootmode=chain; nocmd=""; cfout=envelope; ckey=""; prune=""; mkt=""; log=""
while [ $# -gt 0 ]; do
    case "$1" in
        --camel) casing=camel ;;
        --root) rootmode="${2:-}"; shift ;;
        --no-command) nocmd="--no-command" ;;
        --claudefmt-out) cfout="${2:-}"; shift ;;
        --claude-hooks-key) ckey=1 ;;
        --prune) prune=1 ;;
        --marketplace) mkt=1 ;;
        --log) log="${2:-}"; shift ;;
        *) die "unknown option $1" ;;
    esac
    shift
done
case "$variant" in all|claude+gh|claude+root|gh-only|root-only|claude-only) ;; *) die "bad variant $variant" ;; esac
case "$rootmode" in chain|env|rel) ;; *) die "bad --root $rootmode" ;; esac
case "$cfout" in envelope|plain|hso) ;; *) die "bad --claudefmt-out $cfout" ;; esac

PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import json' >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || die "python3 needed"
[ -f "$SRC/probe.conf" ] || die "run build.sh first"

# --- guards ---
[ -e "$dest" ] && die "$dest exists; pick a new path or delete it first"
# Resolve against the nearest existing ancestor and check every guard BEFORE
# creating any directory.
parent="$(dirname "$dest")"
anc="$parent"; rest=""
while [ ! -d "$anc" ]; do rest="/$(basename "$anc")$rest"; anc="$(dirname "$anc")"; done
absparent="$(cd "$anc" && pwd)$rest"
absdest="$absparent/$(basename "$dest")"
homeabs="$(cd "$HOME" && pwd)"
case "$absdest" in
    "$homeabs"/.copilot|"$homeabs"/.copilot/*|"$homeabs"/.claude|"$homeabs"/.claude/*) die "refusing a path under ~/.copilot or ~/.claude" ;;
    *installed-plugins*|*Digital-Process-Tools--claude-remember--remember*) die "refusing a path near the NEVER-TOUCH install" ;;
esac
if git -C "$anc" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    die "refusing a path inside a git work tree ($anc)"
fi
mkdir -p "$absparent"

# --- copy ---
if [ -n "$mkt" ]; then
    plugdir="$absdest/remember-probe"
    mkdir -p "$absdest/.claude-plugin" "$plugdir"
    printf '%s\n' '{"name": "remember-probe-local", "owner": {"name": "local"}, "plugins": [{"name": "remember-probe", "source": "./remember-probe"}]}' \
        > "$absdest/.claude-plugin/marketplace.json"
else
    plugdir="$absdest"
    mkdir -p "$plugdir"
fi
cp -R "$SRC/." "$plugdir/"

case "$variant" in
    all) ;;
    claude+gh) rm -f "$plugdir/plugin.json" ;;
    claude+root) rm -rf "$plugdir/.github" ;;
    gh-only) rm -rf "$plugdir/.claude-plugin" "$plugdir/plugin.json" ;;
    root-only) rm -rf "$plugdir/.claude-plugin" "$plugdir/.github" ;;
    claude-only) rm -rf "$plugdir/.github" "$plugdir/plugin.json" ;;
esac

if [ -n "$ckey" ] && [ -f "$plugdir/.claude-plugin/plugin.json" ]; then
    "$PY" - "$plugdir/.claude-plugin/plugin.json" <<'PYEOF'
import json, sys
p = sys.argv[1]
d = json.load(open(p, encoding="utf-8"))
d["hooks"] = "./hooks/hooks.json"
open(p, "w", encoding="utf-8", newline="\n").write(json.dumps(d, indent=2) + "\n")
PYEOF
fi

# Copilot hooks files for the chosen casing / root mode (the camel copies stay
# beside them for reference; the manifests always name the plain file names).
"$PY" "$K/gen.py" copilot copilot-root "$casing" "$rootmode" $nocmd > "$plugdir/hooks/hooks.copilot-root.json"
"$PY" "$K/gen.py" copilot copilot-gh   "$casing" "$rootmode" $nocmd > "$plugdir/hooks/hooks.copilot-gh.json"
"$PY" "$K/gen.py" copilot copilot-root camel "$rootmode" $nocmd > "$plugdir/hooks/hooks.copilot-root.camel.json"
"$PY" "$K/gen.py" copilot copilot-gh   camel "$rootmode" $nocmd > "$plugdir/hooks/hooks.copilot-gh.camel.json"

if [ -n "$prune" ]; then
    [ -f "$plugdir/plugin.json" ] || rm -f "$plugdir/hooks/hooks.copilot-root.json" "$plugdir/hooks/hooks.copilot-root.camel.json"
    [ -f "$plugdir/.github/plugin/plugin.json" ] || rm -f "$plugdir/hooks/hooks.copilot-gh.json" "$plugdir/hooks/hooks.copilot-gh.camel.json"
    [ -f "$plugdir/.claude-plugin/plugin.json" ] || rm -f "$plugdir/hooks/hooks.json"
fi

[ -n "$log" ] || log="$(dirname "$plugdir")/probe-log.jsonl"
logm="$log"
command -v cygpath >/dev/null 2>&1 && logm="$(cygpath -m "$log")"
stagetag="$variant;casing=$casing;root=$rootmode;cmd=$([ -n "$nocmd" ] && echo no || echo yes);claudefmt_out=$cfout;claude_hooks_key=$([ -n "$ckey" ] && echo yes || echo no);prune=$([ -n "$prune" ] && echo yes || echo no);marketplace=$([ -n "$mkt" ] && echo yes || echo no)"
{
    grep '^TOKEN_' "$SRC/probe.conf"
    echo "CLAUDEFMT_OUT=$cfout"
    echo "PROBE_STAGE='$stagetag'"
    echo "PROBE_LOG='$logm'"
} > "$plugdir/probe.conf"
chmod +x "$plugdir/scripts/recorder.sh" 2>/dev/null || true

# --- summary ---
. "$plugdir/probe.conf"
win() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
fwd() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
echo "staged:    $stagetag"
echo "plugin:    $(fwd "$plugdir")"
[ "$(win "$plugdir")" != "$(fwd "$plugdir")" ] && echo "           $(win "$plugdir")"
[ -n "$mkt" ] && echo "market:    $(fwd "$absdest")   (marketplace name remember-probe-local, plugin remember-probe)"
echo "log:       $PROBE_LOG"
echo "manifests: $(cd "$plugdir" && ls -1 .claude-plugin/plugin.json plugin.json .github/plugin/plugin.json 2>/dev/null | tr '\n' ' ')"
echo "tokens:    claudefmt=$TOKEN_CLAUDEFMT  copilot-root=$TOKEN_COPILOT_ROOT  copilot-gh=$TOKEN_COPILOT_GH"
echo "           (reply token -> manifest whose SessionStart output reached the model)"
