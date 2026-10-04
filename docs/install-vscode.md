# Installing under VS Code Agents, the Copilot CLI and the Copilot desktop app

Evidence and launcher internals: [vscode-verification.md](vscode-verification.md).

## What works

Three hosts run the same Copilot harness and load this plugin from its Claude-format manifest: VS Code Agents, the Copilot CLI (1.0.92-3) and the Copilot desktop app. All three were **observed** on Windows 11 with Git Bash and Windows PowerShell 5.1. macOS and Linux hosts are unverified (the test suite ran on Linux, no host did).

In VS Code and the Copilot CLI the memory recap is injected at SessionStart. In the desktop app the hooks fired and saves ran; injection was not recorded. The transcript is read from `~/.copilot/session-state/<uuid>/events.jsonl`; saves go into the project's `.remember/`, shared with Claude Code in the same project.

On Windows each hook starts a small PowerShell script, the launcher (`scripts/run-hook.ps1`), which finds Git Bash and runs the hook with it: one extra child PowerShell per hook, started with `-ExecutionPolicy Bypass` for that process only. See [vscode-verification.md](vscode-verification.md#how-hooks-are-launched-on-windows).

## Install

- **`copilot plugin install Digital-Process-Tools/claude-remember`** (**reasoned**, not observed).
- **VS Code:** set `"chat.pluginLocations": {"<absolute path to a checkout>": true}`, then run **Developer: Reload Window** (**observed**). VS Code runs hooks from a mirror copy and does not re-mirror a path that is already registered, so an in-place edit is not picked up; re-register under a new path (or reinstall) to refresh.
- **Copilot CLI:** `copilot --plugin-dir <checkout>`, or a local marketplace (`copilot plugin marketplace add <path>`, then `copilot plugin install`) (**observed**). The desktop app was given the plugin through the same marketplace route (**observed**) and never lists it under Installed.

## Requirements

- Git for Windows: the hooks are bash. See [windows.md](windows.md).
- `jq`. Without it nothing is injected in VS Code or the Copilot CLI; the log (`.remember/logs/memory-<date>.log`) says so.
- The `claude` CLI (Claude Code's) on `PATH`. Summaries run through `claude -p`; there is no Copilot-native summarizer.

## How saving works here

VS Code Agents sends `SessionEnd` with `reason=complete` after every turn and nothing when the session closes, so there the per-turn save is the only save. Whether the CLI does is unverified. The save runs in the background. Two behaviours, set by `cooldowns.turn_end_debounce_seconds` ([configuration.md](configuration.md)).

**A. Immediate (default, `0`).** Each turn is saved as it ends: one summarizer call per turn with new content (observed cost $0.0043-$0.0070 per call, Haiku through `claude -p`), and a loss window of only the few seconds the save takes.

**B. Debounced (`N` > 0).** Each turn's save waits `N` seconds and stands down if a later turn of the same session ends meanwhile, so a burst of turns is saved once, `N` seconds after its last turn, as one summary. The cost is a loss window of `N` seconds: if the machine sleeps or VS Code is killed inside it, that burst can go unsaved unless a later turn in the same session saves it. Put this in `~/.remember/config.json` (all projects) or `<project>/.remember/config.json` (one project); 30 is an example:

```json
{
  "cooldowns": {
    "turn_end_debounce_seconds": 30
  }
}
```

The log records `session-end: turn-end save deferred Ns (cooldowns.turn_end_debounce_seconds)` when a save is scheduled and `session-end: turn-end save superseded by a later turn` when one stands down. `N` is capped at 3600. To turn it off, set the key to `0` or delete the file (v0.37.0+).

The debounce applies only under the `copilot` host hint (VS Code Agents; the CLI too, if it sends `complete`, unverified) and only for `reason=complete`; other hosts are unaffected.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Nothing injected at SessionStart | No `jq`; or the host was started from inside a Claude Code shell, so the recap goes out as plain text (observed for the CLI, reasoned for VS Code) | Install `jq`; start VS Code or `copilot` from a shell without Claude Code's `CLAUDE_CODE_*` variables |
| A hook does nothing; stderr says `Git Bash not found` | No Git Bash found | Install Git for Windows, or set `REMEMBER_BASH` to `bash.exe` |
| `scripts/doctor.sh` prints `FAIL Session dir MISSING` | Expected on a Copilot-only project | Read the `OK   last save came from a VS Code Agents / Copilot session` line and the verdict (`capture is working`, or the summarizer verdict). Needs `jq` |
| In VS Code the harness shows SessionStart `success=false` | Another plugin's SessionStart hook failed | This plugin's context is still injected. Fix the other plugin |
| In the Copilot CLI the recap is missing | Another plugin's hook failed in the same batch, and the CLI drops the whole batch's output | Fix the other plugin |
| The desktop app saved into `~/.copilot/chats/<date>/<slug>/.remember` | No Project was attached to the session | Attach a Project |

## Limitations

- UserPromptSubmit output is not injected: never in VS Code; the CLI would, but this plugin's hook does not yet send it in the form the CLI accepts.
- The CLI drops the whole batch's output when a sibling plugin's hook fails.
- Under Constrained Language Mode (AppLocker or WDAC) the hooks run but the host waits for each save (observed by simulation).
- An execution policy enforced by Group Policy blocks the launcher (not observed).
- Promos are not shown on these hosts (reasoned from the code).
- On a project also used from Claude Code, `doctor.sh` can say `capture is working` after a Copilot save even if Claude Code's own capture is broken; the `FAIL` line is the hint (reasoned).
- macOS and Linux hosts are unverified.

## Follow-ups

Not done; see [vscode-verification.md](vscode-verification.md#coverage-gaps-and-follow-ups).

- Send UserPromptSubmit output in the form the CLI accepts.
- Make doctor tell a Copilot-only project from a mixed one.
- Recognise Gemini CLI as its own host; a Gemini session started from a shell that exports `COPILOT_CLI` would be treated as Copilot (reasoned).
- Record the VS Code version for the 2026-10-03 runs.

Tests and fixtures for this port carry the placeholder token `vscode` in their names (`tests/test_*_vscode.py`, `tests/fixtures/vscode-*`).
