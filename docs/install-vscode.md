# Installing under VS Code Agents, the Copilot CLI and the Copilot desktop app

Evidence: [vscode-verification.md](vscode-verification.md).

## What works

VS Code Agents, the Copilot CLI and the Copilot desktop app run one Copilot harness and load this plugin's Claude-format manifest. All three were **observed** on Windows 11 (Git Bash, Windows PowerShell 5.1) and macOS 27 (Apple silicon, stock bash 3.2): hooks fire, the recap is injected at SessionStart, the Copilot transcript is read and saves run. Two of these were not recorded on Windows, though they were observed on macOS: the desktop app's recap and the CLI's transcript read. Linux hosts are unverified (only the test suite ran there).

The transcript is read from `~/.copilot/session-state/<uuid>/events.jsonl`; saves go into the project's `.remember/`, shared with Claude Code.

On macOS the hooks run with plain bash. On Windows each goes through a PowerShell launcher, `scripts/run-hook.ps1`, that finds Git Bash: one extra process per hook, with `-ExecutionPolicy Bypass` for that process ([details](vscode-verification.md#how-hooks-are-launched-on-windows)).

## Install

- **VS Code:** in your user `settings.json`, set `"chat.pluginLocations": {"<absolute path to the plugin>": true}` (on Windows, double each backslash: `C:\\tools\\remember`), then run **Developer: Reload Window** and open a **New Chat**; the plugin syncs on that first chat, not on the reload (observed on macOS). Register a copy without `.git/`, such as a `git archive` export, as every observed run did; on macOS a git checkout can fail to sync (see Troubleshooting). VS Code does not re-copy a registered folder, so to update, register the new version under a new path.
- **Copilot CLI:** `copilot --plugin-dir <plugin directory>`, or the local marketplace below.
- **Desktop app:** the local marketplace below, installed with the `copilot` CLI. On Windows the hooks ran and saves were made, but the plugin never appeared under Installed.
- `copilot plugin install Digital-Process-Tools/claude-remember` should also work (reasoned, not tried).

**Local marketplace** (observed on macOS, desktop app install). Copy the plugin into `<marketplace dir>/remember/` and list it in `<marketplace dir>/.claude-plugin/marketplace.json`:

```json
{"name": "remember-local", "owner": {"name": "local"}, "plugins": [{"name": "remember", "source": "./remember"}]}
```

Then run `copilot plugin marketplace add <marketplace dir>` (it prints `Marketplace "remember-local" added successfully.`; observed on macOS) and `copilot plugin install remember@remember-local`. The `source` must be relative: `copilot plugin install` rejects an absolute path with `Plugin path escapes marketplace directory` (observed on macOS).

## Removing it

- **CLI and desktop app** (observed on macOS): `copilot plugin uninstall remember@remember-local` only disables a local-marketplace plugin; then run `copilot plugin marketplace remove remember-local`. `~/.copilot/settings.json` may keep an inert `"remember@remember-local": false` (that leftover was observed on Windows and macOS); delete that key by hand if you like.
- **VS Code:** remove the path from `chat.pluginLocations` (not tried).

## Requirements

- Bash: Git for Windows on Windows ([windows.md](windows.md)); stock bash on macOS.
- Python 3.9+ (`python3` on `PATH`; Git for Windows does not include it).
- `jq`: preinstalled on macOS 27 (`/usr/bin/jq`; check with `jq --version`); on Windows, install it ([windows.md](windows.md) lists Scoop and Chocolatey). Without it nothing is injected in VS Code or the CLI; `.remember/logs/memory-<date>.log` says so.
- Claude Code's `claude` CLI on `PATH`, signed in: every save is a `claude -p` call billed to your Claude account, not to Copilot (billing: reasoned).

## How saving works here

- **VS Code Agents** (observed on Windows and macOS) and **the desktop app** (observed on macOS) send `SessionEnd` (`reason=complete`) after every turn, and a background save runs each time. VS Code sends nothing on close (observed on Windows), so this is the only save when a turn ends; PostToolUse saves run mid-turn once enough new transcript accumulates.
- **The Copilot CLI** sends `SessionEnd` once, on `/exit` (`reason=user_exit`), and saves then (observed on macOS). Closing it without `/exit` (a killed terminal; other exits not tried) would lose turns no PostToolUse save caught (reasoned).

In VS Code Agents and the desktop app, `cooldowns.turn_end_debounce_seconds` ([configuration.md](configuration.md)) chooses between two behaviours; it has no effect in the CLI, which saves once on `/exit`, or outside Copilot.

**A. Immediate (default, `0`).** Each turn is saved at once: one summarizer call per turn with new content (observed $0.0043-$0.0070 per call in VS Code on Windows and macOS), and a loss window of seconds.

**B. Debounced (`N` > 0).** A turn's save waits `N` seconds; if another turn ends first, the wait restarts (observed live in VS Code on Windows). A burst of turns costs one summarizer call, `N` seconds after the last turn. The risk (reasoned, not tested): if the waiting save is ended inside those `N` seconds (a shutdown, or a host that takes its hooks' background processes down with it), the burst can go unsaved, unless the session has a later turn. Put `{"cooldowns": {"turn_end_debounce_seconds": 30}}` in `~/.remember/config.json` (all projects) or `<project>/.remember/config.json` (one project).

The log says `turn-end save deferred Ns` when a save is scheduled and `turn-end save superseded by a later turn` when one stands down. `N` is whole seconds: above 3600 it is used as 3600; ten or more digits, or anything that is not a whole number, counts as `0`. To turn it off, set the key back to `0` or remove it.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Nothing injected at SessionStart | No `jq`; or the host was started from a terminal inside Claude Code, so the plugin prints plain text as for Claude Code (observed for the CLI on Windows, reasoned for VS Code) | Install `jq`; start the host from a terminal outside Claude Code |
| VS Code on macOS shows nothing from the plugin; `agenthost.log` (VS Code's log folder) says `Failed to sync plugin … fsmonitor--daemon.ipc` | A git checkout with `core.fsmonitor` on: the mirror copy cannot open that socket in `.git/` and the sync aborts (observed on macOS) | Register a copy without `.git/` (observed), or turn `core.fsmonitor` off and stop its daemon (not tried); register under a new path |
| `Git Bash not found` on stderr (Windows) | Git Bash missing | Install Git for Windows, or set `REMEMBER_BASH` to `bash.exe` |
| `bash <plugin>/scripts/doctor.sh`, run in the project, prints `FAIL Session dir MISSING` | Expected: Claude Code's transcript folder never exists on a Copilot-only project | None needed if it also prints `OK   last save came from a VS Code Agents / Copilot session` and the verdict `capture is working` or a summarizer verdict (needs `jq`) |
| VS Code shows SessionStart `success=false`, or the CLI shows no recap | Another plugin's hook failed: VS Code still injects this plugin's context; the CLI drops the batch's output (observed on Windows) | Fix the other plugin |
| The desktop app saved into `~/.copilot/chats/<date>/<slug>/.remember` | No Project was attached (observed on Windows) | Attach a Project |

## Limitations

Open questions and follow-ups are in [the record](vscode-verification.md#coverage-gaps-and-follow-ups).

- The per-prompt time stamp (`prompt_stamp`, Claude Code's `[14:30 CEST -- user]` line) is not injected on these hosts.
- Under Constrained Language Mode (AppLocker or WDAC, Windows) the host waits for each save (shown in a test that sets the language mode in process, not under a real policy).
- An execution policy enforced by Group Policy blocks the Windows launcher (not observed).
- On a project also used from Claude Code, a Copilot save can make `doctor.sh` say `capture is working` while Claude Code's capture is broken; only the `FAIL` line hints at it (reasoned).
