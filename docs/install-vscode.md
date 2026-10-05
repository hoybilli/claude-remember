# Installing under VS Code Agents, the Copilot CLI and the Copilot desktop app

Evidence: [vscode-verification.md](vscode-verification.md).

## What works

VS Code Agents, the Copilot CLI and the Copilot desktop app run one Copilot harness and load this plugin's Claude-format manifest. All three were **observed** on Windows 11 (Git Bash, Windows PowerShell 5.1) and on macOS 27 (Apple silicon, stock bash 3.2). Linux hosts are unverified (only the test suite ran there).

| Observed on | VS Code Agents | Copilot CLI | Desktop app |
| --- | --- | --- | --- |
| Hooks fired | Windows, macOS | Windows, macOS | Windows, macOS |
| Recap injected | Windows, macOS | Windows, macOS | macOS |
| Transcript read; saves ran | Windows, macOS | macOS | Windows, macOS |

The transcript is read from `~/.copilot/session-state/<uuid>/events.jsonl`; saves go into the project's `.remember/`, shared with Claude Code.

On macOS the hooks run with plain bash. On Windows each goes through a PowerShell launcher, `scripts/run-hook.ps1`, that finds Git Bash: one extra process per hook, with `-ExecutionPolicy Bypass` for that process ([details](vscode-verification.md#how-hooks-are-launched-on-windows)).

## Install

- **VS Code** (observed on Windows and macOS): in your user `settings.json`, set `"chat.pluginLocations": {"<absolute path to the plugin>": true}`, then run **Developer: Reload Window**. The plugin syncs on the first **New Chat**, not on the reload (observed on macOS). Hooks run from a mirror copy that is not refreshed: register changed files under a new path. On macOS, register a copy without `.git/` (a `git archive` export or a release download) or a checkout with `core.fsmonitor` off.
- **Copilot CLI** (observed on Windows and macOS): `copilot --plugin-dir <plugin directory>`, or a local marketplace (`copilot plugin marketplace add <directory>`, then `copilot plugin install`).
- **Desktop app** (observed on Windows and macOS): the same local marketplace. The plugin must sit inside the marketplace directory, named by a relative `source` such as `"./remember"`; an absolute path is rejected with `Plugin path escapes marketplace directory` (observed on macOS). On Windows it never appeared under Installed.
- `copilot plugin install Digital-Process-Tools/claude-remember` should also work (reasoned, not tried).

## Requirements

- Bash: Git for Windows on Windows ([windows.md](windows.md)); stock bash on macOS.
- Python 3.9+ (`python3` on `PATH`; Git for Windows does not include it).
- `jq`: it ships with recent macOS (`/usr/bin/jq`); install it on Windows. Without it nothing is injected in VS Code or the CLI; `.remember/logs/memory-<date>.log` says so.
- Claude Code's `claude` CLI on `PATH` (summaries run through `claude -p`).

## How saving works here

- **VS Code Agents** (observed on Windows and macOS) and **the desktop app** (observed on macOS) send `SessionEnd` (`reason=complete`) after every turn, and a background save runs each time. VS Code sends nothing on close (observed on Windows), so that is its only save.
- **The Copilot CLI** sends `SessionEnd` once, on `/exit` (`reason=user_exit`), and saves then (observed on macOS). Closing it without `/exit` (a killed terminal) would lose turns no PostToolUse save caught (reasoned).

For that per-turn save (`copilot` host hint, `reason=complete`; never the CLI's `user_exit` or other hosts), `cooldowns.turn_end_debounce_seconds` ([configuration.md](configuration.md)) chooses between two behaviours.

**A. Immediate (default, `0`).** Each turn is saved at once: one summarizer call per turn with new content (observed $0.0043-$0.0070 per call on Windows and macOS), and a loss window of seconds.

**B. Debounced (`N` > 0).** A turn's save waits `N` seconds; if another turn ends first, the wait restarts. A burst of turns costs one summarizer call, `N` seconds after the last turn. The risk: a sleep or a killed host inside those `N` seconds loses the burst, unless the session has a later turn. Put `{"cooldowns": {"turn_end_debounce_seconds": 30}}` in `~/.remember/config.json` (all projects) or `<project>/.remember/config.json` (one project).

The log says `turn-end save deferred Ns` when a save is scheduled and `turn-end save superseded by a later turn` when one stands down. `N` is capped at 3600; ten or more digits, like anything but a plain non-negative integer, is read as `0`. To turn it off, set the key back to `0` or remove it. (Before v0.37.0 a removed per-project `config.json` kept serving its old value; this port ships the fix.)

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Nothing injected at SessionStart | No `jq`; or the host was started from a terminal inside Claude Code, so the plugin prints plain text as for Claude Code (observed for the CLI on Windows, reasoned for VS Code) | Install `jq`; start the host from a terminal outside Claude Code |
| VS Code on macOS shows nothing from the plugin; `agenthost.log` (VS Code's log folder) says `Failed to sync plugin … fsmonitor--daemon.ipc` | A git checkout with `core.fsmonitor` on: the mirror copy cannot open that socket in `.git/` and the sync aborts (observed on macOS) | Register a copy without `.git/`, or run `git config core.fsmonitor false` and stop its daemon; register under a new path |
| `Git Bash not found` on stderr (Windows) | Git Bash missing | Install Git for Windows, or set `REMEMBER_BASH` to `bash.exe` |
| `scripts/doctor.sh` prints `FAIL Session dir MISSING` | Expected on a Copilot-only project | Read the `OK   last save came from a VS Code Agents / Copilot session` line and the verdict (needs `jq`) |
| VS Code shows SessionStart `success=false`, or the CLI shows no recap | Another plugin's hook failed: VS Code still injects this plugin's context; the CLI drops the batch's output (seen on Windows) | Fix the other plugin |
| The desktop app saved into `~/.copilot/chats/<date>/<slug>/.remember` | No Project was attached (seen on Windows) | Attach a Project |

## Limitations

- The per-prompt time stamp (`prompt_stamp`, Claude Code's `[14:30 CEST -- user]` line) is not injected: VS Code ignores it, and the CLI would accept it (both observed on Windows) in a shape this plugin's hook does not yet send.
- Under Constrained Language Mode (AppLocker or WDAC, Windows) the host waits for each save (shown in a test that sets the language mode in process, not under a real policy).
- An execution policy enforced by Group Policy blocks the Windows launcher (not observed).
- Promos are not shown on these hosts (reasoned from the code; pinned by a test that ran on macOS).
- On a project also used from Claude Code, a Copilot save can make `doctor.sh` say `capture is working` while Claude Code's capture is broken; only the `FAIL` line hints at it (reasoned).

## Not done yet

Open: the CLI's prompt stamp, doctor on a mixed project and a Gemini CLI signature ([the record](vscode-verification.md#coverage-gaps-and-follow-ups)).
