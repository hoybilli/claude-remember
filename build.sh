#!/usr/bin/env bash
# Throwaway probe kit: (re)generates remember-probe/ beside this script.
# Tokens are fixed once, in tokens.env; delete that file to draw new ones.
# Writes only inside this kit directory.
set -eu
K="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$K/remember-probe"
PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import json' >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "build.sh: python3 needed" >&2; exit 1; }

if [ ! -f "$K/tokens.env" ]; then
    d4() { printf '%04d' $(( (RANDOM * 32768 + RANDOM) % 10000 )); }
    {
        echo "TOKEN_CLAUDEFMT=TOKEN_CLAUDEFMT_$(d4)"
        echo "TOKEN_COPILOT_ROOT=TOKEN_COPILOT_ROOT_$(d4)"
        echo "TOKEN_COPILOT_GH=TOKEN_COPILOT_GH_$(d4)"
    } > "$K/tokens.env"
fi

mkdir -p "$P/.claude-plugin" "$P/.github/plugin" "$P/hooks" "$P/scripts"

DESC='throwaway hook probe; delete after use'
manifest() { # $1 = hooks path or empty
    if [ -n "$1" ]; then
        printf '{\n  "name": "remember-probe",\n  "version": "0.0.1",\n  "description": "%s",\n  "author": {"name": "local probe"},\n  "hooks": "%s"\n}\n' "$DESC" "$1"
    else
        printf '{\n  "name": "remember-probe",\n  "version": "0.0.1",\n  "description": "%s",\n  "author": {"name": "local probe"}\n}\n' "$DESC"
    fi
}
# Claude manifest: no "hooks" key, exactly like the real plugin's
# .claude-plugin/plugin.json (hooks/hooks.json is loaded by convention; see
# REPORT.md for why the key is left out).
manifest ""                                 > "$P/.claude-plugin/plugin.json"
manifest "./hooks/hooks.copilot-root.json"  > "$P/plugin.json"
manifest "./hooks/hooks.copilot-gh.json"    > "$P/.github/plugin/plugin.json"

"$PY" "$K/gen.py" claude                                     > "$P/hooks/hooks.json"
"$PY" "$K/gen.py" copilot copilot-root pascal chain          > "$P/hooks/hooks.copilot-root.json"
"$PY" "$K/gen.py" copilot copilot-gh   pascal chain          > "$P/hooks/hooks.copilot-gh.json"
"$PY" "$K/gen.py" copilot copilot-root camel  chain          > "$P/hooks/hooks.copilot-root.camel.json"
"$PY" "$K/gen.py" copilot copilot-gh   camel  chain          > "$P/hooks/hooks.copilot-gh.camel.json"

{
    echo "# remember-probe config (sourced by scripts/recorder.sh). Throwaway."
    cat "$K/tokens.env"
    echo "CLAUDEFMT_OUT=envelope"
    echo "PROBE_STAGE=unstaged"
    echo "PROBE_LOG="
} > "$P/probe.conf"

cat > "$P/README.md" <<'EOF'
# remember-probe

Throwaway hook probe for the claude-remember Copilot-port feasibility spike.
Delete after use. It records each hook call into ../probe-log.jsonl and, at
SessionStart, prints one token envelope. It does nothing else.
EOF

chmod +x "$P/scripts/recorder.sh" 2>/dev/null || true
echo "built $P"
cat "$K/tokens.env"
