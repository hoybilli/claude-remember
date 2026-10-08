#!/usr/bin/env python3
"""Throwaway probe kit: emits the probe's hooks files (stdlib only).

  gen.py claude                                   -> Claude-format hooks.json
  gen.py copilot <tag> <pascal|camel> <chain|env|rel> [--no-command]
                                                  -> Copilot-native hooks file

Every handler passes a third argument naming the handler key that carried it
(command / bash / powershell), so the log shows which key the host ran (Q7).
"""
import json
import sys

PASCAL = ["SessionStart", "UserPromptSubmit", "PostToolUse", "SessionEnd"]
CAMEL = ["sessionStart", "userPromptSubmitted", "postToolUse", "sessionEnd"]
BS = chr(92)
LAUNCH = "scripts" + BS + "run-probe.ps1"
PS = "powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File"


def claude():
    hooks = {}
    for ev in PASCAL:
        entry = {
            "type": "command",
            "command": 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/recorder.sh" claudefmt %s command' % ev,
            "powershell": '%s "$env:CLAUDE_PLUGIN_ROOT%s%s" claudefmt %s powershell; exit $LASTEXITCODE' % (PS, BS, LAUNCH, ev),
            "timeout": 30,
        }
        hooks[ev] = [{"hooks": [entry]}]
    return {"hooks": hooks}


def bash_root(mode):
    if mode == "chain":
        return '"${COPILOT_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${PLUGIN_ROOT:-.}}}/scripts/recorder.sh"'
    if mode == "env":
        return '"${CLAUDE_PLUGIN_ROOT}/scripts/recorder.sh"'
    if mode == "rel":
        return "./scripts/recorder.sh"
    raise SystemExit("bad root mode: %s" % mode)


def ps_cmd(mode, tag, ev):
    if mode == "chain":
        pre = ("$r=$env:COPILOT_PLUGIN_ROOT; if (-not $r) { $r=$env:CLAUDE_PLUGIN_ROOT }; "
               "if (-not $r) { $r=$env:PLUGIN_ROOT }; if (-not $r) { $r='.' }; ")
        path = '"$r' + BS + LAUNCH + '"'
    elif mode == "env":
        pre, path = "", '"$env:CLAUDE_PLUGIN_ROOT' + BS + LAUNCH + '"'
    elif mode == "rel":
        pre, path = "", '".' + BS + LAUNCH + '"'
    else:
        raise SystemExit("bad root mode: %s" % mode)
    return "%s%s %s %s %s powershell; exit $LASTEXITCODE" % (pre, PS, path, tag, ev)


def copilot(tag, casing, mode, with_command):
    events = PASCAL if casing == "pascal" else CAMEL
    hooks = {}
    for ev in events:
        h = {
            "type": "command",
            "bash": "bash %s %s %s bash" % (bash_root(mode), tag, ev),
            "powershell": ps_cmd(mode, tag, ev),
        }
        if with_command:
            h["command"] = "bash %s %s %s command" % (bash_root(mode), tag, ev)
        h["timeoutSec"] = 30
        hooks[ev] = [h]
    return {"version": 1, "hooks": hooks}


def main(argv):
    if argv[:1] == ["claude"]:
        out = claude()
    elif argv[:1] == ["copilot"] and len(argv) >= 4:
        tag, casing, mode = argv[1], argv[2], argv[3]
        if casing not in ("pascal", "camel"):
            raise SystemExit("casing must be pascal|camel")
        out = copilot(tag, casing, mode, "--no-command" not in argv[4:])
    else:
        raise SystemExit(__doc__)
    sys.stdout.write(json.dumps(out, indent=2) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
