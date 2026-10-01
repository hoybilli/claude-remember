# Installing under VS Code Agents (GitHub Copilot harness)

Observed against VS Code 1.139.1 on Windows 11 with Git for Windows (Git Bash) and Windows PowerShell 5.1, on 2026-09-30, by dumping the environment and stdin from inside firing plugin hooks -- not only reasoned from documentation. macOS and Linux VS Code, and the standalone Copilot CLI (same harness), are **reasoned, not observed**. Every claim below is labelled **observed** or **reasoned**; a claim about this repo's code was read from the code.

This repo has no issue number for this port yet. Tests and fixtures that cover it carry the placeholder token `vscode` in their names instead (`tests/test_*_vscode.py`, `tests/fixtures/vscode-*`).

## Install

Either route:

```
copilot plugin install Digital-Process-Tools/claude-remember
```

(the plugin lands under `~/.copilot/installed-plugins/_direct/...`), or the VS Code setting

```
"chat.pluginLocations": {"<absolute path to a checkout>": true}
```

followed by **Developer: Reload Window**. The settings route is the one **observed** here; the `copilot plugin install` route is **reasoned**.

- VS Code copies the plugin into its own mirror directory under `%APPDATA%\Code\agentPlugins\` and runs hooks **from the mirror**, not from the source folder (**observed**). A path that is already registered is not re-mirrored when its files change (**observed**), so an in-place edit of a checkout is not picked up; re-register under a new path (or reinstall) to refresh.
- No hooks manifest other than `hooks/hooks.json` is needed. VS Code reads the Claude-format manifest (**observed**). If a Copilot-native manifest (`.github/plugin/plugin.json` naming its own hooks file) is also present, VS Code loads only that one and sends a different, camelCase payload (**observed**); this plugin deliberately ships no such manifest.
- On Windows you need Git for Windows (Git Bash) and `jq`, same as [windows.md](windows.md).

## How hooks are launched on Windows

- **Observed:** VS Code runs a hook entry's `command` string through Windows PowerShell. There a bare `bash` can resolve to `C:\Windows\System32\bash.exe` (WSL) when System32 precedes Git on `PATH`: no Windows environment, no `/c/` paths, and the plugin's script is never reached. A `windows` override key is ignored.
- **Observed:** a `powershell` key on the entry is honoured and replaces `command` (one invocation per entry). `hooks/hooks.json` therefore carries, next to each unchanged `command`, a `powershell` key that calls `scripts/run-hook.ps1` with the hook script's file name. The launcher locates Git Bash explicitly, forwards stdin (the payload) and stdout as UTF-8, and returns bash's exit status.
- **Launcher lookup order** (read from `scripts/run-hook.ps1`), first existing file wins:
  1. `$env:REMEMBER_BASH`, if set;
  2. `Git\bin\bash.exe` under `%ProgramFiles%`;
  3. the same under `%ProgramFiles(x86)%`;
  4. the same under `%LOCALAPPDATA%\Programs`;
  5. the first `bash.exe` on `PATH` whose path does not contain `System32`.

  If none is found it writes `claude-remember: Git Bash not found; install Git for Windows or set REMEMBER_BASH to bash.exe` to stderr and exits 0, so the hook does nothing rather than failing the session. The plugin root is taken from `CLAUDE_PLUGIN_ROOT`, then `COPILOT_PLUGIN_ROOT`, then the launcher's own parent directory.
- **Execution policy:** the `& script.ps1` form is subject to PowerShell's execution policy. Observed on a machine with `LocalMachine=Unrestricted`; behaviour under `Restricted` or `AllSigned` is **not observed**.
- Claude Code ignores the `powershell` key and keeps using `command`; `claude plugin validate` accepts the manifest (**observed**). macOS/Linux VS Code is expected to use `command` (**reasoned**).

## What VS Code sends, and what this plugin does with it

- **Payload on stdin** (observed) is snake_case: `hook_event_name`, `session_id`, `cwd`, `timestamp`; plus `source` and `initial_prompt` (SessionStart), `prompt` (UserPromptSubmit), `tool_name` / `tool_input` / `tool_result` (PostToolUse), `reason` (SessionEnd). There is no `transcript_path`. A captured payload is `tests/fixtures/vscode-hook-stdin-vscode.json`.
- **`session_id`** is a bare uuid on the plugin-hook path (observed 2026-09-30). A prefixed form, `agent-host-copilotcli:/<uuid>`, was seen in the extension-host hook log on 2026-09-29. `scripts/lib-session-id.sh` (`remember_normalize_session_id`) strips that prefix before the hooks' character-allowlist validators, so both forms are handled.
- **Environment** (observed): `CLAUDE_PLUGIN_ROOT`, `COPILOT_PLUGIN_ROOT` and `PLUGIN_ROOT` (all the mirror path, backslash-separated), `CLAUDE_PROJECT_DIR` and `COPILOT_PROJECT_DIR` (the project), `COPILOT_HOME`, `COPILOT_CLI=1`, `AI_AGENT=github_copilot_vscode_agent`. Host detection (`COPILOT.signature_vars` in `pipeline/host.py`, and `remember_session_id_host_hint` in `scripts/lib-session-id.sh`) keys on `COPILOT_CLI` / `COPILOT_PLUGIN_ROOT`, not `COPILOT_HOME`; Claude Code's own signature wins when both are present. The captured environment is `tests/fixtures/vscode-env-vscode.txt`.
- **Transcript:** `~/.copilot/session-state/<uuid>/events.jsonl` (or under `$COPILOT_HOME`), one JSON object per line with a dotted `type` and a `data` object (observed; sample in `tests/fixtures/vscode-events.jsonl`). It is found by file existence, not by host detection: `find_session` in `pipeline/extract.py` and `post-tool-hook.sh` both look up the stdin uuid via `copilot_transcript_for` (`pipeline/host.py`) / `remember_copilot_transcript_for` (shell mirror). `sniff_envelope` reports such a file as `"copilot"` and `copilot_exchange` turns each line into a `(role, text)` exchange.
- **SessionStart stdout:** on this host the memory recap is emitted as `{"additionalContext": "<recap>"}` (the emit branch at the end of `scripts/session-start-hook.sh`, taken when `REMEMBER_HOST_HINT=copilot` and `jq` is available; otherwise the plain recap is printed). **Observed:** of three shapes emitted in one hook entry, only the top-level `additionalContext` reached the model; `hookSpecificOutput.additionalContext` and plain text did not. Caveat: the shapes were emitted by consecutive commands, so "unsupported" and "a later command's output wins" could not be separated.
- **Summaries** still run through `claude -p`. There is no Copilot-native summarizer; `_choose_summarizer_provider` in `pipeline/haiku.py` logs a warning that `REMEMBER_SUMMARIZER=auto` is falling back to `claude` for a Copilot transcript.
- **`.remember/`** is the project's usual directory, shared with Claude Code sessions in the same project.
- `scripts/doctor.sh` prints an `OK   copilot session-state dir present` line when `~/.copilot/session-state` (or `$COPILOT_HOME/session-state`) exists.

## Verification

**Observed** on 2026-09-30 (Windows 11, Git for Windows bash 5.2.37, Windows PowerShell 5.1): three agent sessions on the Copilot harness, run against an existing project that already had a `.remember/` directory written by Claude Code. The plugin was a `git archive` export (LF line endings) of commit 39d1610, registered through `chat.pluginLocations` and followed by a window reload; VS Code ran the hooks from its mirror under `%APPDATA%\Code\agentPlugins\`. The earlier probes were recorded against VS Code 1.139.1; `code --version` reported 1.140.0 when these sessions ran, and the running window's version was not separately confirmed.

| Event | Fired | Notes |
| --- | --- | --- |
| SessionStart | yes | Fired once per session. The harness recorded the output as `{"additionalContext": ...}` of 5,780 characters, and the model's first reply listed the day file, `now.md`, `recent.md`, `archive.md`, the last handoff and the history note, so the recap reached the model. Hook log: `session-start took 2s`. |
| UserPromptSubmit | yes | Fired on every turn and exited successfully. Its output does not reach the model on this host (see Known gaps). |
| PostToolUse | yes | Fired once per tool call (twice in session 3). Log line: `post-tool: copilot transcript .../.copilot/session-state/<uuid>/events.jsonl`. |
| SessionEnd | yes | Fired after every turn, not once per session, always with `reason=complete` (see Known gaps). Each firing ran the forced save: `extract`, then the summarizer (`provider: claude`), then a position update. |

End to end (**observed**):

- **Recap injected.** In session 1 the model could name the memory sections from the SessionStart context alone.
- **Save written to the shared timeline.** In session 3, one turn that created a small file in the project: `extract` found 4 exchanges (1 human), the summarizer returned an entry (`[write] appended (provider: claude): ## 21:21 | master`), the position advanced (`[write] position -> 34`), and the regular compression step moved the entry from `now.md` into the day file (`now.md -> today-2026-09-30.md`, `752->360b`). The day file holds a one-line entry naming the file that session created. This is the first observation of a VS Code Agents session being summarised into the same `.remember/` timeline Claude Code uses.
- **`scripts/doctor.sh`** run against the project afterwards: `VERDICT: capture is working -- last save 2026-09-30 21:13:09`.
- **Two sessions were SKIPped by the summarizer.** Session 1 (a question about the memory sections, then a command printing `hello`) and session 2 (one read-only turn that read the README and summarised it) each extracted correctly (session 2: 3 exchanges, with the tool-only assistant turn rendered as a `[TOOL: ...]` line) and the position advanced, but the summarizer returned `SKIP` and nothing was written to `now.md`. The save prompt tells the model to return `SKIP` for a span with no substantive work, and it treated these that way (**observed** that it did; the reading that this is the prompt's intended judgement and not a capture failure is **reasoned**, from the prompt text and from session 3 writing normally).

Not observed: macOS, Linux, the standalone Copilot CLI, and any session shape beyond these three.

## Known gaps

- **SessionEnd fires after every turn on this host** (**observed**, `reason=complete`), not once per session. Each firing runs the forced save, which bypasses the cooldown and minimum-message gates, so the summarizer is called once per turn; the observed cost was roughly half a cent to seven tenths of a cent per call. Saves are incremental (**observed**: the position advances between firings and nothing was duplicated), but the per-turn call is a cost and latency difference from Claude Code. Not changed in this version.
- The harness reports the merged SessionStart result as failed (`success=false`) when any other installed plugin's SessionStart hook fails (**observed**: another plugin's hook failed under PowerShell in every session); this plugin's context was still injected.
- UserPromptSubmit output does not reach the model on this host (**observed**: neither a `hookSpecificOutput` nor a top-level `additionalContext` from the plugin's UserPromptSubmit hook was injected). Whatever `scripts/user-prompt-hook.sh` prints is therefore not seen by the model here.
- Promos (`systemMessage`) are not emitted on this host; the emit branch skips them on purpose, as `systemMessage` was not probed.
- Under `powershell -Command`, Windows PowerShell 5.1 reports any non-zero script exit as 1 (**observed**); the hook scripts exit 0 on every path, so this only matters when diagnosing a failure.
- PowerShell execution policies stricter than the one observed: unverified.
- macOS/Linux VS Code and the standalone Copilot CLI: unverified.
