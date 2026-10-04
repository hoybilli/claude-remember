# Installing under VS Code Agents, the Copilot CLI and the Copilot desktop app

The evidence behind this page, and the launcher's internals, are in [vscode-verification.md](vscode-verification.md).

## What works

Three hosts run the same Copilot harness and load this plugin from its Claude-format manifest: VS Code Agents, the Copilot CLI (1.0.92-3) and the GitHub Copilot desktop app. All three were **observed** on Windows 11 with Git for Windows (Git Bash) and Windows PowerShell 5.1. macOS and Linux hosts are unverified: the test suite ran on Linux, no host did.

On these hosts the memory recap is injected at SessionStart, the transcript is read from `~/.copilot/session-state/<uuid>/events.jsonl`, and saves go into the project's `.remember/`, shared with Claude Code sessions in the same project.

## Install

- **`copilot plugin install Digital-Process-Tools/claude-remember`** (**reasoned**, not observed).
- **VS Code:** set `"chat.pluginLocations": {"<absolute path to a checkout>": true}`, then run **Developer: Reload Window** (**observed**). VS Code runs hooks from a mirror copy and does not re-mirror a path that is already registered, so an in-place edit is not picked up; re-register under a new path to refresh.
- **Copilot CLI:** `copilot --plugin-dir <checkout>`, or a local marketplace (`copilot plugin marketplace add <path>`, then `copilot plugin install`) (**observed**). The desktop app takes the same marketplace route (**observed**) and never lists the plugin under Installed.

## Requirements

- Git for Windows: the hooks are bash. See [windows.md](windows.md).
- `jq`. Without it nothing is injected on these hosts, and the log line names it.
- The `claude` CLI on `PATH`. Summaries run through `claude -p`; there is no Copilot-native summarizer.

## How saving works here

These hosts send `SessionEnd` with `reason=complete` after every turn and nothing when the session closes, so the per-turn save is the only save. The save runs in the background; the turn does not wait for it. Set `cooldowns.turn_end_debounce_seconds` to choose between two behaviours (see [configuration.md](configuration.md)).

**A. Immediate (default, `0`).** Each turn is saved as soon as it ends: one summarizer call per turn with new content (observed cost $0.0043-$0.0070 per call, Haiku through `claude -p`), and a loss window of only the few seconds the save takes.

**B. Debounced (`N` > 0).** Each turn's save waits `N` seconds and stands down if a later turn of the same session ends meanwhile, so a burst of turns is saved once, `N` seconds after its last turn, as one summary. The cost is a loss window of `N` seconds: if the machine sleeps or VS Code is killed inside it, that burst can go unsaved, and only a later turn in the same session would save it. Put this in `~/.remember/config.json` (all projects) or `<project>/.remember/config.json` (one project); 30 is an example:

```json
{
  "cooldowns": {
    "turn_end_debounce_seconds": 30
  }
}
```

The daily log records `session-end: turn-end save deferred Ns (cooldowns.turn_end_debounce_seconds)` when a save is scheduled and `session-end: turn-end save superseded by a later turn` when one stands down. `N` is capped at 3600. To turn it off, set the key to `0` or delete the file (v0.37.0+).

Other hosts are unaffected: the debounce applies only under the `copilot` host hint (VS Code Agents; the Copilot CLI too, if it sends `complete`, which is unverified) and only for `reason=complete`.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Nothing injected at SessionStart | No `jq`; or copilot was started from inside a Claude Code shell, which inherits `CLAUDE_CODE_*` and sends the recap as plain text (observed for the CLI) | Install `jq`; start copilot from a clean shell |
| A hook does nothing; stderr says `Git Bash not found` | No Git Bash found | Install Git for Windows, or set `REMEMBER_BASH` to `bash.exe` |
| `scripts/doctor.sh` prints `FAIL Session dir MISSING` | Expected on a Copilot-only project | Read the verdict line: `capture is working` plus `last save came from a VS Code Agents / Copilot session`. Needs `jq` |
| The harness shows SessionStart `success=false` | Another plugin's hook failed | In VS Code this plugin's context is still injected; in the CLI the whole batch's output is dropped. Fix the other plugin |
| The desktop app saved into `~/.copilot/chats/<date>/<slug>/.remember` | No Project was attached to the session | Attach a Project |

## Limitations

- UserPromptSubmit output is not injected: never in VS Code; the CLI would, but the hook emits no envelope yet.
- The Copilot CLI drops the whole hook batch when a sibling plugin's hook fails.
- Under Constrained Language Mode (AppLocker or WDAC) the hooks run, but the host waits for each save (observed by simulation).
- An execution policy enforced by Group Policy blocks the launcher (not observed).
- Promos are not shown on these hosts (reasoned from the code).
- macOS and Linux hosts are unverified.

## Follow-ups

Not done in this port; details in [vscode-verification.md](vscode-verification.md#coverage-gaps-and-follow-ups).

- A UserPromptSubmit envelope for the copilot host (the CLI injects it).
- Gemini CLI has no registry signature, so a shell exporting `COPILOT_CLI` would mis-hint it.
- Doctor on a mixed project can mask a real #144 mismatch when the last save was a Copilot one.
- The VS Code version for the 2026-10-03 runs was not recorded.

Tests and fixtures for this port carry the placeholder token `vscode` in their names (`tests/test_*_vscode.py`, `tests/fixtures/vscode-*`).
