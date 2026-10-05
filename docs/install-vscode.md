# Installing under VS Code Agents, the Copilot CLI and the Copilot desktop app

## What works

**Observed on Windows 11 and macOS 27 in all three hosts:** the hooks fire, the memory recap is injected when a chat starts, and turns are saved into the project's `.remember/`, which is shared with Claude Code. Not recorded on Windows, though observed on macOS: the desktop app's recap, and the CLI's transcript read and saves. Linux: not tried (only the test suite ran there).

Versions run: VS Code 1.140.0 (macOS; 1.139.1 to 1.140.0 on Windows), Copilot CLI 1.0.92-3 (Windows) and 1.0.91 (macOS), desktop app 1.1.26 (macOS).

## Requirements

- Bash: Git for Windows on Windows ([windows.md](windows.md)); stock bash on macOS.
- Python 3.9+ (`python3` on `PATH`; Git for Windows does not include it).
- `jq`: preinstalled on macOS 27 (`/usr/bin/jq`; check with `jq --version`); on Windows, install it ([windows.md](windows.md) lists Scoop and Chocolatey). Without it nothing is injected in VS Code or the CLI; `.remember/logs/memory-<date>.log` says so.
- Claude Code's `claude` CLI on `PATH`, signed in: saves with new content, and a daily consolidation, are `claude -p` calls billed to your Claude account, not to Copilot (billing: reasoned).
- The `copilot` CLI, for desktop app installs.
- On Windows each hook starts through a small PowerShell launcher (`-ExecutionPolicy Bypass` for that one process); the live Windows runs used an earlier form of it, and the shipped form has run only in tests ([details](vscode-verification.md#how-hooks-are-launched-on-windows)).

## Install

First get a copy without `.git/`, as every successful VS Code run did; on macOS a git checkout can fail to sync. In a terminal (Git Bash on Windows; there write the path with forward slashes, `C:/tools/remember`):

```
git clone https://github.com/Digital-Process-Tools/claude-remember.git
mkdir -p <plugin dir>
git -c core.autocrlf=false -C claude-remember archive HEAD | tar -x -C <plugin dir>
```

**VS Code**

1. Run **Preferences: Open User Settings (JSON)** and add `"chat.pluginLocations": {"<plugin dir>": true}` (absolute path). In settings JSON on Windows, double each backslash: `C:\\tools\\remember`.
2. Run **Developer: Reload Window**, then open a **New Chat**. The plugin syncs on that first chat, not on the reload (observed on macOS).
3. To update, export the new version to a new folder and register that path instead: VS Code does not re-copy a registered folder.

**Copilot CLI:** `copilot --plugin-dir <plugin dir>`, or the local marketplace below.

**Desktop app:** install through the local marketplace below with the `copilot` CLI, then start a new chat **with a Project attached**. Without one, memory goes under `~/.copilot/chats/<date>/<slug>/`, not your project (observed on Windows). On Windows the plugin never appeared under Installed, although its hooks ran and saved; use [Check it works](#check-it-works) instead.

**Local marketplace** (used for the desktop app on both platforms and for the CLI on Windows; the file and messages below are from macOS):

```
mkdir -p <marketplace dir>/.claude-plugin <marketplace dir>/remember
git -c core.autocrlf=false -C claude-remember archive HEAD | tar -x -C <marketplace dir>/remember
```

Put this in `<marketplace dir>/.claude-plugin/marketplace.json`:

```json
{"name": "remember-local", "owner": {"name": "local"}, "plugins": [{"name": "remember", "source": "./remember"}]}
```

Then run `copilot plugin marketplace add <marketplace dir>` (it prints `Marketplace "remember-local" added successfully.`) and `copilot plugin install remember@remember-local`. The `source` must be relative: an absolute path is rejected with `Plugin path escapes marketplace directory`.

**From GitHub:** `copilot plugin install Digital-Process-Tools/claude-remember` (landing path seen on Windows with an earlier version; not tried with this port).

## Check it works

After a first turn's save has finished (a few seconds; `N` more with debounce; in the CLI, after `/exit`), run `bash <plugin dir>/scripts/doctor.sh` (marketplace installs: `<marketplace dir>/remember`) from the project folder (Git Bash on Windows; needs `jq`). Expect `OK   last save came from a VS Code Agents / Copilot session` and `VERDICT: capture is working` (observed on macOS). On a project never opened in Claude Code, `FAIL Session dir MISSING` is expected. Any other `VERDICT: problem …` line names a real problem.

## How saving works here

- **VS Code Agents and the desktop app save after every turn**, in the background (observed: VS Code on Windows and macOS, the desktop app on macOS). Closing VS Code triggers no further save (observed on Windows).
- **The Copilot CLI saves once, when you type `/exit`** (observed on macOS). End sessions with `/exit`. Closing it any other way, such as killing the terminal, would probably send no `SessionEnd` and lose turns no mid-session save caught (reasoned, not tried).

In VS Code Agents, and in the desktop app (reasoned: it sends `complete` per turn on macOS), `cooldowns.turn_end_debounce_seconds` ([configuration.md](configuration.md)) chooses between two behaviours. It had no effect in the CLI run on macOS, which saved once on `/exit` (the Windows CLI's reason was not recorded).

**A. Immediate (default, `0`).** Each turn is saved at once: one summarizer call per turn with new content (observed $0.0043-$0.0070 per call in VS Code on Windows and macOS), and a loss window of seconds.

**B. Debounced (`N` > 0).** A turn's save waits `N` seconds; if another turn ends first, the wait restarts (observed live in VS Code on Windows). A burst of turns costs one summarizer call, `N` seconds after the last turn. To turn it on, put `{"cooldowns": {"turn_end_debounce_seconds": 30}}` in `~/.remember/config.json` (all projects) or `<project>/.remember/config.json` (one project). The risk (reasoned, not tested): if the waiting save is ended inside those `N` seconds (a shutdown, or a host that takes its hooks' background processes down with it), the burst can go unsaved, unless the session has a later turn.

Keep A unless a summarizer call per turn costs more than you want. Use a whole number from 0 to 3600; anything else is capped at 3600 or ignored ([configuration.md](configuration.md)). The project's `.remember/logs/memory-<date>.log` says `turn-end save deferred Ns` when a save is scheduled and `turn-end save superseded by a later turn` when one stands down. To turn it off, set the key back to `0` or remove it.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| The assistant shows no memory of earlier sessions | No `jq`; or the host was started from a terminal inside Claude Code (observed for the CLI on Windows, reasoned for VS Code) | Install `jq`; start the host from a terminal outside Claude Code |
| VS Code on macOS shows nothing from the plugin; its log (run **Developer: Open Logs Folder**; the file is `agenthost.log`) says `Failed to sync plugin … fsmonitor--daemon.ipc` | A git checkout with `core.fsmonitor` on: VS Code cannot copy its `.git/` socket and the sync aborts (observed on macOS) | Register a copy without `.git/` (observed), or run `git config core.fsmonitor false` and `git fsmonitor--daemon stop` in the checkout (not tried); register under a new path |
| `Git Bash not found` on stderr (Windows) | Git Bash missing | Install Git for Windows. If `bash.exe` is elsewhere, set the user environment variable `REMEMBER_BASH` to its full path (for example `D:\Git\bin\bash.exe`) and restart the host (reasoned) |
| The CLI shows no recap | Another plugin's hook failed in the same batch; the CLI drops the batch's output (observed on Windows) | Fix the other plugin |

## Removing it

- **CLI and desktop app** (observed on macOS): `copilot plugin uninstall remember@remember-local` only disables a local-marketplace plugin; then run `copilot plugin marketplace remove remember-local`. The files in `<marketplace dir>` stay; delete them by hand. `~/.copilot/settings.json` may keep an inert `"remember@remember-local": false` (observed on Windows and macOS); you can delete that key. With `--plugin-dir`, stop passing the flag.
- **VS Code:** remove the path from `chat.pluginLocations` and reload the window (not tried).
- Each project's `.remember/` stays, shared with Claude Code.

## Limitations

Open questions and follow-ups are in [the record](vscode-verification.md#coverage-gaps-and-follow-ups).

- The per-prompt time stamp (`prompt_stamp`, Claude Code's `[14:30 CEST -- user]` line) is not injected on these hosts (observed in VS Code and the CLI; reasoned for the desktop app).
- On Windows machines locked down with AppLocker or WDAC (Constrained Language Mode), the launcher cannot detach the save, so each turn waits for it (reasoned; the launcher's CLM path ran only in a test that set the mode in process).
- An execution policy set by Group Policy overrides the launcher's `-ExecutionPolicy Bypass`; if it is `Restricted` or `AllSigned`, the unsigned launcher is blocked (reasoned, not observed). Check with `Get-ExecutionPolicy -List` in PowerShell; a `MachinePolicy` or `UserPolicy` other than `Undefined` applies.
- On a project also used from Claude Code, a Copilot save can make `doctor.sh` say `capture is working` while Claude Code's capture is broken; only the `FAIL` line hints at it (reasoned).
