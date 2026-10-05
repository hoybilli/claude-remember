# VS Code Agents / Copilot hosts: verification record and launcher internals

The evidence behind [install-vscode.md](install-vscode.md). Each claim names the hosts it was recorded for and the platform it was recorded on.

## Scope and labels

Three hosts run the same Copilot harness and load this plugin from its Claude-format manifest: VS Code Agents, the Copilot CLI and the GitHub Copilot desktop app.

- **Windows:** all three were run on Windows 11 with Git for Windows bash 5.2.37 and Windows PowerShell 5.1, on 2026-09-30 (VS Code only) and 2026-10-03: Copilot CLI 1.0.92-3 (a prerelease) and the desktop app at runtime `copilotVersion 0.0.0` (the app's own version was not determined).
- **macOS:** all three were run on 2026-10-04 on macOS 27.0.1, arm64 (Apple silicon), with the stock `/bin/bash` 3.2.57: VS Code 1.140.0, Copilot CLI 1.0.91 (the latest stable release) and the desktop app 1.1.26 (runtime `copilotVersion 0.0.0`).
- **Linux:** no host was run; the test suite was. The suite was also run on macOS.

Labels:

- **Observed**: seen in a live host run or a test run on the stated platform.
- **Observed by simulation**: seen in a test that imitates the condition (a language mode or execution policy set in process), not under the real policy.
- **Reasoned**: derived from the code or documentation, not seen.
- **Read from the code**: a reasoned claim about this repo's own scripts, checked against the source, not against a run.
- **Not observed**: not tried; any claim rests on reasoning or documentation.

VS Code version on Windows: the 2026-09-30 probes were recorded against 1.139.1, and `code --version` reported 1.140.0 on 2026-09-30 after the window reloads. The version was not re-recorded for the 2026-10-03 runs, and the running window's version was never confirmed. On macOS it was 1.140.0.

This repo has no issue number for this port yet; tests and fixtures carry the placeholder token `vscode` in their names (`tests/test_*_vscode.py`, `tests/fixtures/vscode-*`).

## What is established, per host

### On Windows 11

| | VS Code Agents | Copilot CLI | Desktop app |
| --- | --- | --- | --- |
| Dates | 2026-09-30, 2026-10-03 | 2026-10-03 | 2026-10-03 |
| Hooks fired through the launcher | observed | observed | observed |
| Recap injected at SessionStart | observed | observed | not recorded |
| Transcript resolved; saves ran | observed | not recorded | observed |
| `SessionEnd` per turn with `reason=complete`, nothing on close | observed | not recorded (SessionEnd fired; its reason was not recorded) | not recorded (SessionEnd fired) |
| UserPromptSubmit output injected | no (observed) | CLI injects a top-level `additionalContext` (observed, probe); this plugin's hook emits none (read from the code) | not recorded |
| Plain-text recap injected | no (observed) | no (observed, probe) | not recorded |

### On macOS 27

All cells **observed** on 2026-10-04; details in [the macOS entry](#2026-10-04-macos-vs-code-agents-copilot-cli-desktop-app).

| | VS Code Agents 1.140.0 | Copilot CLI 1.0.91 | Desktop app 1.1.26 |
| --- | --- | --- | --- |
| Hooks fired (each entry's `command`, plain bash; no launcher) | yes | yes | yes |
| Recap injected at SessionStart | yes (the model named the memory files) | yes (top-level `additionalContext` recorded) | yes (the model named the memory files) |
| Transcript resolved; saves ran | yes | yes | yes |
| `SessionEnd` | after every turn, `reason=complete` | once, on `/exit`, `reason=user_exit` | after every turn, `reason=complete` |
| Hooks run from | VS Code's mirror | the `--plugin-dir` directory | the marketplace directory |
| SessionStart / PostToolUse / SessionEnd duration | 0.18-0.24 / 0.15-0.40 / 0.01 s | 0.14-0.21 / 0.08-0.19 / 0.01 s | 0.18 / 0.08 / 0.01 s |

So the desktop app's recap injection and its per-turn `SessionEnd`, the CLI's `SessionEnd` reason and the CLI's transcript lookup, all unrecorded on Windows, are observed on macOS. The Windows cells are not changed by that: a macOS observation is not a Windows one.

## What the hosts send

- **Payload on stdin** (observed on Windows in VS Code and the CLI) is snake_case: `hook_event_name`, `session_id`, `cwd`, `timestamp`; plus `source` and `initial_prompt` (SessionStart), `prompt` (UserPromptSubmit), `tool_name` / `tool_input` / `tool_result` (PostToolUse), `reason` (SessionEnd). No `transcript_path`. Captured: `tests/fixtures/vscode-hook-stdin-vscode.json`. On macOS the CLI's harness recorded camelCase keys (`sessionId`, `toolArgs`, `toolResult`, ...) in its own records, while the hooks received the snake_case form on stdin, as on Windows (observed; the key lists are in the macOS entry).
- **`session_id`** is a bare uuid on the plugin-hook path (observed on Windows, 2026-09-30 in VS Code and 2026-10-03 in the CLI). A prefixed form, `agent-host-copilotcli:/<uuid>`, was seen in the extension-host hook log on 2026-09-29. `remember_session_id_resolve` (`scripts/lib-session-id.sh`) strips that prefix before the hooks' character-allowlist validators, so both forms work. An id that is unsafe after stripping (`agent-host-x:/../../x`) is emptied by the validators, and the hook runs as if no id arrived (pinned for SessionStart, PostToolUse and SessionEnd by the `*_vscode` hook tests).
- **Environment** (observed on Windows): in VS Code, `CLAUDE_PLUGIN_ROOT`, `COPILOT_PLUGIN_ROOT` and `PLUGIN_ROOT` (all the mirror path, backslash-separated), `CLAUDE_PROJECT_DIR` and `COPILOT_PROJECT_DIR` (the project), `COPILOT_HOME`, `COPILOT_CLI=1`, `AI_AGENT=github_copilot_vscode_agent`. The CLI's hooks carry `COPILOT_CLI=1`, `COPILOT_PLUGIN_ROOT`, `CLAUDE_PLUGIN_ROOT` and `CLAUDE_PROJECT_DIR` too. Captured: `tests/fixtures/vscode-env-vscode.txt`.
- **Host detection** (`COPILOT.signature_vars` in `pipeline/host.py`; `remember_session_id_resolve`) keys on `COPILOT_CLI` / `COPILOT_PLUGIN_ROOT`, not `COPILOT_HOME`. The shell hint has two arms (read from the code): a session id with the `agent-host-*:/` prefix sets the `copilot` hint regardless of other signatures; only the environment arm yields, to the signature of any host registered before Copilot in `pipeline/host.REGISTRY` (Claude Code, Codex, Antigravity), matching the Python check.
- **Transcript:** `~/.copilot/session-state/<uuid>/events.jsonl` (or under `$COPILOT_HOME`), one JSON object per line with a dotted `type` and a `data` object (observed on Windows in VS Code; resolved on Windows in VS Code and the desktop app, and on macOS in all three hosts; sample `tests/fixtures/vscode-events.jsonl`). It is found by file existence, not host detection: `find_session` in `pipeline/extract.py` and `post-tool-hook.sh` look up the stdin uuid via `copilot_transcript_for` (`pipeline/host.py`) and its shell mirror `remember_copilot_transcript_into` (`scripts/lib-session-id.sh`; `doctor.sh` uses the printing form `remember_copilot_transcript_for`). `sniff_envelope` reports such a file as `"copilot"` and `copilot_exchange` turns each line into a `(role, text)` exchange.
- **SessionStart stdout:** the recap is emitted as `{"additionalContext": "<recap>"}` (end of `scripts/session-start-hook.sh`, taken when `REMEMBER_HOST_HINT=copilot` and `jq` is available; otherwise the plain recap is printed, which VS Code does not inject). Each plain-text fallback on the copilot hint is logged (read from the code; pinned by `tests/test_session_start_copilot_stdout_vscode.py`, observed on Windows, Linux and macOS): without `jq`, `session-start: copilot host without jq, recap printed as plain text, which this host does not inject`; when the envelope's own `jq` call fails, `session-start: copilot envelope could not be built, recap printed as plain text, ...`; when the recap was never buffered (a `REMEMBER_TRACE` run, or `tmp/` not writable), `session-start: copilot host, recap not buffered (trace on or tmp/ not writable), printed as plain text, ...`. **Observed** in VS Code on Windows: of three shapes emitted in one hook entry, only the top-level `additionalContext` reached the model; `hookSpecificOutput.additionalContext` and plain text did not. Caveat: the shapes came from consecutive commands, so "unsupported" and "a later command's output wins" could not be separated. On macOS the top-level `additionalContext` was recorded in all three hosts (observed).
- **UserPromptSubmit stdout:** in VS Code on Windows, output does not reach the model (**observed**: neither a `hookSpecificOutput` nor a top-level `additionalContext` from the plugin's hook was injected). The CLI differs (see its section), but `scripts/user-prompt-hook.sh` emits no top-level `additionalContext` on the copilot hint (read from the code), so nothing it prints reaches the model in the CLI either, today. On macOS, in VS Code, this plugin's hook printed nothing, and VS Code itself attached its own 1,157-character `additionalContext` hint to UserPromptSubmit (not this plugin's); the desktop app attached no such hint (both observed).
- **Another plugin's hook failing.** The harness reports the merged SessionStart result as failed (`success=false`) when any other installed plugin's SessionStart hook fails (**observed** in VS Code on Windows: another plugin's hook failed under PowerShell in every session); this plugin's context was still injected. The CLI drops the whole batch's output, this plugin's recap included (**observed** on Windows); `success=false` was not recorded for the CLI.
- **Summaries** run through the `claude` CLI (Claude Code's; `claude -p`); there is no Copilot-native summarizer. The save log records `provider: claude` (**observed** on Windows). `_choose_summarizer_provider` in `pipeline/haiku.py` warns when a Copilot transcript falls back to `claude`, but only when a transcript path is supplied (`REMEMBER_TRANSCRIPT_PATH`), which VS Code does not do (**observed** on Windows: the live save log shows `provider: claude` and no warning).
- **`.remember/`** is the project's usual directory, shared with Claude Code sessions in the same project. In the desktop app, a session with a Project attached uses that project's `.remember/`; one without runs in the app's chat workspace, `~/.copilot/chats/<date>/<slug>/`, and the plugin bootstraps a fresh `.remember/` there, so that memory is not the project's (both **observed** on Windows).
- **Install layout.** VS Code copies the plugin into a mirror and runs hooks from it: under `%APPDATA%\Code\agentPlugins\` on Windows; on macOS at `~/Library/Application Support/Code/agentPlugins/file-<absolute path with / turned into ->/<11-hex id>/` (both **observed**). A path already registered is not re-mirrored when its files change (**observed** on Windows; the macOS run reports the same, see its work-around). The CLI runs hooks straight from the `--plugin-dir` or local-marketplace directory, no mirror (**observed** on Windows; on macOS from `--plugin-dir`). The desktop app was given the plugin through the same local-marketplace route on both platforms and runs hooks straight from the marketplace directory (**observed** on macOS); on Windows it never listed the plugin under Installed (**observed**). VS Code reads the Claude-format `hooks/hooks.json` (**observed** on Windows and macOS); if a Copilot-native manifest (`.github/plugin/plugin.json` naming its own hooks file) is also present, it loads only that one and sends a different, camelCase payload (**observed** on Windows), so this plugin ships none. The `copilot plugin install` route from GitHub is **reasoned**, including where it lands (`~/.copilot/installed-plugins/_direct/...`).

## How hooks are launched on Windows

On macOS none of this applies: all three hosts ran each entry's `command` (`bash "${CLAUDE_PLUGIN_ROOT}/scripts/<hook>.sh"`) with the stock `/bin/bash` 3.2.57, and the `powershell` key and the launcher were not involved (**observed**, 2026-10-04). Linux is expected to behave the same (**reasoned**).

- **Observed:** VS Code runs a hook's `command` string through Windows PowerShell, where a bare `bash` can resolve to `C:\Windows\System32\bash.exe` (WSL) when System32 precedes Git on `PATH`: no Windows environment, no `/c/` paths, and the plugin's script is never reached. A `windows` override key is ignored.
- **Observed:** a `powershell` key on the entry is honoured and replaces `command` (one invocation per entry). `hooks/hooks.json` carries, next to each unchanged `command`, a `powershell` key of exactly this form:

  ```
  powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$env:CLAUDE_PLUGIN_ROOT\scripts\run-hook.ps1" <name>.sh; exit $LASTEXITCODE
  ```

  It starts a child Windows PowerShell running `scripts/run-hook.ps1` (the launcher) with the hook script's file name. The launcher locates Git Bash explicitly, runs the hook with it, and returns bash's exit status; `exit $LASTEXITCODE` passes it on.
- **Exit status.** With no bash found the launcher writes `claude-remember: Git Bash not found; install Git for Windows or set REMEMBER_BASH to bash.exe` to stderr and exits 0, so the hook does nothing rather than failing the session. Any other launcher error (for example a `REMEMBER_BASH` that is a file but not a program) is written as `claude-remember: launcher error: ...` to stderr and also exits 0. Only bash's exit status is forwarded, and the hook scripts exit 0 on every path.
- **Lookup order** (read from `scripts/run-hook.ps1`), first existing file wins:
  1. `$env:REMEMBER_BASH`, if set (a path that does not exist, or names a directory, falls through);
  2. `Git\bin\bash.exe` under `%ProgramW6432%` (the 64-bit Program Files, which a 32-bit PowerShell reports as `%ProgramFiles%` = `Program Files (x86)`);
  3. the same under `%ProgramFiles%`;
  4. the same under `%ProgramFiles(x86)%`;
  5. the same under `%LOCALAPPDATA%\Programs` (skipped when `LOCALAPPDATA` is unset);
  6. the first `bash.exe` on `PATH` whose path contains neither `System32` nor `WindowsApps` (WSL's launcher and its Microsoft Store alias).

  The plugin root is taken from `CLAUDE_PLUGIN_ROOT`, then `COPILOT_PLUGIN_ROOT`, then the launcher's parent directory, but those two fallbacks apply only when the launcher is invoked directly. The manifest names it through `$env:CLAUDE_PLUGIN_ROOT` alone, so through the manifest it is reached only when that variable is set (read from `hooks/hooks.json`).
- **Stdin.** The launcher does not touch stdin or stdout; bash inherits both handles. The payload therefore arrives byte for byte, EOF is the host's, and a host that leaves stdin open is bounded by the hook's own `read -t 1`. **Observed** on Windows in `tests/test_hooks_json.py`, for the launcher alone and the full manifest value: the bytes bash reads equal the bytes written (non-ASCII and a mid-payload CR included), a payload over 1 MB arrives complete, and with stdin held open for 30 s a hook that reads with `read -t 1` and exits returns within the test's 20 s bound.
- **`-NonInteractive`** makes a launcher started without its script name fail (exit 1, a missing-parameter error on stderr) instead of prompting and taking the answer from the payload on stdin, as it does without the flag (both **observed** on Windows).
- **`-NoLogo`** does **not** keep the banner off stdout when `-File` names a file that does not exist (an unset or stale `CLAUDE_PLUGIN_ROOT`): Windows PowerShell 5.1 prints its banner to stdout and fails before any script runs (**observed** on Windows). All three hosts set `CLAUDE_PLUGIN_ROOT` on Windows (**observed**), so this does not arise through the manifest on them.
- **Constrained Language Mode** (AppLocker or WDAC script enforcement): the launcher checks its language mode before any step needing full language. Under Constrained Language Mode it skips the handle step below (refused there), runs the hook as usual, forwards its exit status, and writes `claude-remember: launcher: Constrained Language Mode; could not detach background work, the host will wait for it` to stderr; the error path also exits 0 there. Launcher messages go to `[Console]::Error`; under Constrained Language Mode, which refuses that call, `Write-Error` is used instead. `Write-Warning` is avoided because Windows PowerShell 5.1 writes the warning stream to stdout, in front of a SessionStart hook's JSON (**observed** on Windows). **Observed by simulation** in `tests/test_hooks_json.py`, Windows only: the session's language mode is set to `ConstrainedLanguage` in process and the launcher called in it. Setting `__PSLockdownPolicy=4` in a child's environment did not engage the mode on this machine (the child reported `FullLanguage`, **observed**), so a real AppLocker / WDAC policy was not exercised.
- **Background work must not hold the hook's output open.** The hooks return at once and leave their work (the save, a due consolidation) to a detached background child; the host treats the hook as finished only when its stdout reaches end-of-file (shown for a Python caller in the test harness; **observed** for VS Code on Windows, see the [duration table](#2026-10-03-vs-code-agents-the-launcher-entry-and-its-detach-fix)).
- **Why the host waited.** Windows PowerShell 5.1 holds an extra inheritable duplicate of its own stdout handle (present from the start of the script, not created by the launcher), and every process it starts -- bash, and through bash every background child -- inherits every inheritable handle. So the detached child kept a copy of the host's stdout pipe and the host waited for it: `powershell.exe` exited in 0.4 s while the caller's stdout stayed open for the child's full 20 s, and changing how the launcher starts bash (a captured pipeline, `System.Diagnostics.Process` with redirected streams, `cmd /c` with file redirections) did not help, because each still inherits the duplicate (all **observed** in a test harness on Windows 11, Git Bash 5.2.37).
- **The fix.** Before starting bash the launcher clears the inherit flag on every handle except its three standard ones, which bash receives as its own stdio and does not pass on to a child whose stdio it redirected (**observed** on Windows: Git Bash started directly with piped stdio returns at once with the same background child).
- **The `Add-Type` step.** The handle step compiles a few lines of C# with `Add-Type` (about 0.15-0.25 s added per hook here). The helper is compiled in memory: with `TEMP` pointing at a directory that does not exist it still compiled and ran, and left no files in `%TEMP%` (**observed** on Windows); `Add-Type` still starts `csc.exe` (**reasoned**). The step is best effort: where it fails the launcher writes `claude-remember: launcher: could not detach background work (...); the host will wait for it` to stderr, the hook still runs, and the host waits for the background work as before. `tests/test_hooks_json.py` pins a 15 s background child against a 10 s return bound (a broken launcher is held the full 15 s), with a foreground-sleep positive control.
- **Execution policy:** the entry passes `-ExecutionPolicy Bypass` to the child PowerShell, so a `Restricted` (the Windows client default) or `RemoteSigned` policy on the shell VS Code starts does not block it. **Observed by simulation** with `-ExecutionPolicy Restricted` on the outer shell: each manifest value reached its script with the payload intact, while the earlier `& "...\run-hook.ps1"` form failed with `PSSecurityException`. A real `CurrentUser` / `LocalMachine` setting and `RemoteSigned` were not simulated; that the child's Bypass outranks them follows from PowerShell's documented scope precedence (**reasoned**). A policy enforced by Group Policy (`MachinePolicy` / `UserPolicy`) takes precedence over `-ExecutionPolicy` and cannot be overridden this way (**not observed**). The live run's machine has `LocalMachine=Unrestricted`.
- **`claude plugin validate`** accepts the manifest with the `powershell` keys (**observed**; re-run from the repo root on the current form: "Validation passed with warnings", the one warning being that a `CLAUDE.md` at the plugin root is not loaded as context, unrelated to the hooks). That Claude Code ignores the key at run time and keeps using `command` is **reasoned** (the validator accepts it and the `command` strings are byte-for-byte unchanged), not observed in a Claude Code session.

## Verification log

### 2026-09-30: VS Code Agents, first live runs

**Observed** on Windows 11: three agent sessions against an existing project that already had a `.remember/` written by Claude Code. The plugin was a `git archive` export (LF line endings) of commit 39d1610, registered through `chat.pluginLocations` and a window reload. Its `powershell` entries used the earlier `& "$env:CLAUDE_PLUGIN_ROOT\scripts\run-hook.ps1" <name>.sh` form; the `-File` form was not used in these sessions. The probes of that day dumped the environment and stdin from inside firing plugin hooks, so what they report was seen, not only reasoned from documentation.

| Event | Fired | Notes |
| --- | --- | --- |
| SessionStart | yes | Once per session, in each of the three. The harness recorded `{"additionalContext": ...}` of 5,780 characters, and in session 1 the model's first reply listed the day file, `now.md`, `recent.md`, `archive.md`, the last handoff and the history note, so the recap reached the model. Hook log: `session-start took 2s`. |
| UserPromptSubmit | yes | Every turn; exited successfully. |
| PostToolUse | yes | Once per tool call in all three sessions (1, 1 and 2 calls). Log line: `post-tool: copilot transcript .../.copilot/session-state/<uuid>/events.jsonl`. |
| SessionEnd | yes | After every turn, not once per session, with `reason=complete` in every firing observed (four, across three sessions). Each ran the forced save: `extract`, the summarizer (`provider: claude`), a position update. |

End to end (**observed**):

- **`scripts/doctor.sh`**, run after session 1 (its timestamp precedes session 3's write): `VERDICT: capture is working -- last save 2026-09-30 21:13:09`.
- **Two sessions were SKIPped by the summarizer.** Session 1 (a question about the memory sections, then a command printing `hello`) and session 2 (one read-only turn that read and summarised the README) were each extracted and the position advanced (observed in the log for both; session 2 shows `position -> 25`), but the summarizer returned `SKIP` and nothing reached `now.md`. Session 2's extract was inspected by re-running the extractor: three exchanges (the prompt, the tool-only assistant turn as a `[TOOL: ...]` line, the answer) with the right roles. Session 1's extract was not inspected. The save prompt says to return `SKIP` for a span with no substantive work, and it did (**observed**); that this is the prompt's intended judgement and not a capture failure is **reasoned**, from the prompt text and from session 3 writing normally.
- **Save written to the shared timeline.** Session 3, one turn that created a small file: `extract` found 4 exchanges (1 human), the summarizer returned an entry (`[write] appended (provider: claude): ## 21:21 | <branch>`), the position advanced (`[write] position -> 34`), and compression moved the entry from `now.md` into the day file (`now.md -> today-2026-09-30.md`, `752->360b`), which holds a one-line entry naming the file. This is the first observation of a VS Code Agents session being summarised into the same `.remember/` timeline Claude Code uses.

Not observed on 2026-09-30: macOS, Linux, the standalone Copilot CLI, and any session shape beyond these three.

### 2026-10-03: VS Code Agents, the launcher entry and its detach fix

**Observed** on Windows 11, against the same project, with the plugin again a `git archive` export registered through `chat.pluginLocations`. Runs before the launcher fix used an export of d54685c; runs after it used 3311c67 (which contains the fix, 07c0e7d), and a final round used b40bf48. All three carry the `powershell -NoProfile -ExecutionPolicy Bypass -File ... run-hook.ps1` entries. `-NoLogo -NonInteractive`, the launcher's stdin inheritance, its console-encoding setup (reduced to the stdout encoding only, UTF-8, for its own stderr lines) and its Constrained Language Mode path were added after these runs (and after the CLI and desktop app runs below) and have not been run live in any Windows host; they are covered by the Windows tests named under [How hooks are launched on Windows](#how-hooks-are-launched-on-windows).

| Event | Fired | Notes (exports of d54685c, 3311c67 and b40bf48) |
| --- | --- | --- |
| SessionStart | yes | Through the launcher from VS Code's mirror, like every hook below. Injected the recap as `{"additionalContext": ...}` (5,009 characters on d54685c, 4,802 on 3311c67, 2,564 on b40bf48, where upstream's #842 handoff redelivery cap held back a handoff already delivered many times); in the d54685c and 3311c67 runs the model listed the memory sections. |
| UserPromptSubmit | yes | Through the launcher. 2026-10-03: its output still does not reach the model. |
| PostToolUse | yes | Resolved the Copilot transcript. |
| SessionEnd | yes | After every turn, as on 2026-09-30. |

**Hook durations, before and after the detach fix** (07c0e7d), from the harness's `hook.start` / `hook.end` records in `events.jsonl`. Before the fix each hook took as long as the work it had put in the background, so the model's turn waited for the whole save and the UI showed the next prompt as queued ("steering") until then. After it, each hook took only its own run time, and the second prompt of a burst was no longer queued behind the first turn's save. All **observed** on Windows:

| Hook | Background work | Before (d54685c) | After (3311c67, then b40bf48) |
| --- | --- | --- | --- |
| SessionStart, daily consolidation due | consolidation (a summarizer call, ~35 s) | 40.0 s (one session) | not recorded |
| SessionStart | -- (nothing due) | 2.6-2.7 s | 2.7 s, 2.9 s (3311c67) and 3.0 s (b40bf48), one session each |
| SessionEnd, default | forced save: extract + summarizer (~5 s) | 7.4 s and 8.5 s (one session, two turns) | 0.7 s (3311c67, session A, two turns); 0.9-1.0 s (b40bf48, two turns) |
| SessionEnd, after a session that wrote a file | forced save + compression (~45 s) | 55.1 s (one session) | not recorded |
| SessionEnd, `turn_end_debounce_seconds=30` | 30 s sleep + forced save | 38-39 s (three sessions) | 0.7 s (3311c67, session B, two turns) |
| PostToolUse | background save | 1.1-1.7 s (four firings) | 1.5 s (3311c67) and 2.5 s (b40bf48) |

The cause is the inherited stdout handle. The fix was first shown in the test harness -- the real `scripts/session-end-hook.sh`, with a stub save sleeping 10 s, returned through the launcher in 0.7 s (`-File`) and 0.9 s (the full manifest form) instead of 11.8 s, and the save still completed about 11 s later -- and the VS Code durations dropped exactly as that predicted.

### 2026-10-03: Copilot CLI

**Observed** on Windows 11 with Copilot CLI 1.0.92-3, the plugin loaded with `--plugin-dir` and, separately, from a local directory marketplace:

- The hooks ran through `scripts/run-hook.ps1` straight from that directory. The payload was snake_case with a bare uuid `session_id`. SessionStart took 2.6-2.7 s and SessionEnd 0.8 s. The recap was injected: the model listed the memory files.
- A probe hook emitting several shapes showed that the CLI injects a top-level `additionalContext` for SessionStart **and** for UserPromptSubmit (VS Code does not for the latter); `hookSpecificOutput.additionalContext` and plain text were not injected.
- A `copilot` started from inside a Claude Code session inherits its `CLAUDE_CODE_*` variables, so the hint resolves to Claude Code and the recap goes out as plain text, which the CLI does not inject. With those variables removed the recap was injected. For VS Code the same case is **reasoned**, not observed.

### 2026-10-03: Copilot desktop app

**Observed** on Windows 11, the plugin given through a local directory marketplace: every hook fired through the launcher (SessionStart 2.2 s, SessionEnd 0.7 s, PostToolUse 1.4 s), the Copilot transcript was resolved and the saves ran. Where a session's `.remember/` lands is under "What the hosts send".

### 2026-10-04: macOS (VS Code Agents, Copilot CLI, desktop app)

**Observed** unless marked. Platform: macOS 27.0.1, arm64 (Apple silicon); the stock `/bin/bash` 3.2.57 (no Homebrew bash); `jq` 1.7.1 at `/usr/bin/jq`, which ships with macOS; Python 3.9.6 (Apple, `/usr/bin/python3`) and 3.14.7 (Homebrew). Hosts: VS Code 1.140.0; Copilot CLI 1.0.91 (Homebrew cask, the latest stable release; the 1.0.92-3 used on Windows is a prerelease); GitHub Copilot desktop app 1.1.26 (`CFBundleShortVersionString`), runtime `copilotVersion 0.0.0`. The hooks ran each manifest entry's `command` (`bash "${CLAUDE_PLUGIN_ROOT}/scripts/<hook>.sh"`); the `powershell` key and `run-hook.ps1` were not involved. This was the first live run of that path in any Copilot host.

**VS Code Agents, registered from a git checkout: the sync failed.** Registering the git clone itself through `chat.pluginLocations` failed. VS Code's `agenthost.log` logged, 14 times:

```
[AgentPluginManager] Failed to sync plugin file://<checkout>: Error: Unknown system error -102: Unknown system error -102, open '<checkout>/.git/fsmonitor--daemon.ipc'
```

VS Code's mirror copies the whole registered folder, `.git/` included. With `core.fsmonitor=true` (set in that clone's local `.git/config`; global and system unset; what set it was not observed), `.git/fsmonitor--daemon.ipc` is a Unix-domain socket, `open()` on it fails (errno 102), and the sync aborts, leaving a partial mirror. No plugin hook ran; the harness's own hook records still appeared, with no plugin output. On Windows the fsmonitor IPC is a named pipe, not a file in `.git/`, so the same failure is not expected there (**reasoned**); the Windows VS Code runs used `git archive` exports without `.git/` anyway. Work-arounds: register a copy without `.git/` (a `git archive` export, the method used for every VS Code run on both platforms) or turn `core.fsmonitor` off in that checkout (`git config core.fsmonitor false`, then stop its daemon), and re-register under a new path, since VS Code does not re-mirror a registered path.

**VS Code Agents, registered from a `git archive` export: full pass**, three sessions.

- **Sync.** Nothing was synced on **Developer: Reload Window**; the sync ran when the first New Chat opened. The mirror, at `~/Library/Application Support/Code/agentPlugins/file-<absolute path with / turned into ->/<11-hex id>/`, was identical to the export (`diff -rq`). Hooks ran from the mirror (`PIPELINE_DIR` was the mirror).
- **SessionStart** 0.18-0.24 s, a top-level `additionalContext` of 546 characters each time; in session 1 the model named the memory files (as reported by the user). The SessionStart hook records come after the first UserPromptSubmit's; the recap was still injected.
- **PostToolUse** 0.15-0.40 s: `post-tool: copilot transcript ~/.copilot/session-state/<uuid>/events.jsonl`.
- **SessionEnd** 0.01 s, `reason=complete` after **every** turn (four turns); a forced save ran each time (three returned `SKIP` from the summarizer, one wrote an entry), at $0.0046-$0.0063 per summarizer call.
- **UserPromptSubmit:** this plugin's hook printed nothing; VS Code itself attaches its own 1,157-character `additionalContext` hint to UserPromptSubmit (not this plugin's).
- `hook-errors.log` empty; no stray processes.

**Copilot CLI 1.0.91.** `copilot --plugin-dir <dir>`, started from a plain Terminal (not inside Claude Code); two sessions, full pass.

- SessionStart 0.14-0.21 s with a top-level `additionalContext` (388 and 629 characters); PostToolUse 0.08-0.19 s, transcript resolved; hooks ran straight from the `--plugin-dir` directory (no mirror).
- **`SessionEnd` fires once, on `/exit`, with `reason=user_exit`, not after each turn**: a two-turn session sent a single SessionEnd (0.01 s). The forced save ran then and wrote an entry. So `cooldowns.turn_end_debounce_seconds`, which acts only on `reason=complete`, never applies on the CLI. (**Reasoned**, not tested: a CLI session ended without `/exit`, such as a killed terminal, would send no SessionEnd, and its turns would be saved only by PostToolUse delta saves, if any.)
- Payload keys in the harness's own records: sessionStart `[cwd, initialPrompt, sessionId, source, timestamp]`, userPromptSubmitted `[cwd, prompt, sessionId, timestamp]`, postToolUse `[cwd, sessionId, timestamp, toolArgs, toolName, toolResult]`, sessionEnd `[cwd, reason, sessionId, timestamp]`. The hooks received the snake_case form on stdin, as on Windows.

**Copilot desktop app 1.1.26.** Installed through a temporary local directory marketplace.

- **An absolute plugin `source` in `marketplace.json` is rejected** by `copilot plugin install` with `Plugin path escapes marketplace directory: <path>`, and the failed install leaves a disabled "live plugin" entry behind. A relative `"source": "./remember"`, with the plugin copied inside the marketplace directory, installs ("loaded live … nothing was copied"). `~/.copilot/settings.json` did not exist before.
- One new chat with a Project (the test folder) attached, two turns: SessionStart 0.18 s with a top-level `additionalContext` (679 characters), and the model **named the memory files**; PostToolUse 0.08 s, transcript resolved; **`SessionEnd` with `reason=complete` after every turn** (0.01 s; like VS Code, unlike the CLI); a save per turn (one `SKIP`, one entry); hooks ran straight from the marketplace directory (no mirror). No VS Code-style hint on UserPromptSubmit. `hook-errors.log` empty; no stray processes.
- **Removing it.** `copilot plugin uninstall remember@remember-local` only disables a live (local-marketplace) plugin: "Plugin … disabled. It is still on disk at <marketplace dir> — nothing was removed." `copilot plugin marketplace remove remember-local` then removed the marketplace. The install had created `~/.copilot/settings.json`; after uninstall and remove it still held `{"extraKnownMarketplaces": {}, "enabledPlugins": {"remember@remember-local": false}}` (inert; deleted by hand, since the file had not existed before). The same stale `false` key was seen on Windows.

**`scripts/doctor.sh`**, on a CLI-only project with `CLAUDE_PROJECT_DIR` set: `FAIL Session dir MISSING` (Claude Code's transcript dir, expected), `OK   copilot session-state dir present`, `OK   last save came from a VS Code Agents / Copilot session (...); Claude Code's transcript dir is not expected (issue: vscode)`, `VERDICT: capture is working`. `jq` was reported as `/usr/bin/jq (jq-1.7.1-apple)`.

### Linux: the test suite

No host ran on Linux; the suite did (**observed** on WSL Ubuntu 26.04, Python 3.14, a `git archive` export of b40bf48). Three failures were this port's and are fixed (the `jq`-word lint tripped by a log line; two SessionStart plain-text controls that switched promos off through a variable nothing reads). The final run gave 4 failed / 3,331 passed / 87 skipped; the four are `test_lock_primitive` cases that also fail on `main` (9e78169). Every `*_vscode` module and both detach modules ran rather than skipping.

### macOS: the test suite

On the Mac above, at 62f3813 (**observed**): Python 3.9.6 gave 1 failed / 3,299 passed / 101 skipped (12m55s), Python 3.14.7 2 failed / 3,298 passed / 101 skipped (12m34s), coverage 93% on both. Both failures were in this port's tests, not the plugin, and are fixed and re-run green on the Mac on both Pythons:

- `test_copilot_exchange_vscode.py::test_deeply_nested_string_arguments_under_the_size_cap_do_not_raise` (3.14.7 only): `json.loads("[" * 60000)` raises `JSONDecodeError` on 3.14.7 but `RecursionError` on 3.9.6 and on Windows 3.14.4, and the test assumed the latter. 3bad563 asserts the version-independent contract and pins each branch with a forced exception (15 passed on both Pythons).
- `test_session_start_copilot_stdout_vscode.py::test_copilot_without_jq_logs_why_nothing_is_injected` (both): the harness hid `jq` by dropping its `PATH` directory; on macOS `jq` is `/usr/bin/jq` while `cat` is `/bin/cat`, so `/usr/bin`, and `tr` with it, was dropped and `log.sh` failed. c981dde hides `jq` through a mirror of its directory without it (14 passed, 0 skipped on both Pythons).

The 29 skips in the `*_vscode` and `test_hooks_json` modules are all Windows-only (PowerShell / Git Bash required). The SessionStart promo-on pair, skipped on Windows, ran and passed. No test hung, and no stray `sleep 3600` was left after either run.

## Saves per turn: evidence

VS Code sends `SessionEnd` with `reason=complete` after every turn and nothing when the session is closed (**observed** on Windows in the three sessions of 2026-09-30 and again on 2026-10-03). On macOS on 2026-10-04 the per-turn firing with `reason=complete` was observed again in VS Code and also in the desktop app; whether either sends anything on close was not recorded there. On Windows the desktop app's SessionEnd fired, but per-turn firing and the reason were not recorded. A turn its own `SessionEnd` does not save is saved by nothing else: the next session has a new id, and start-up recovery reads only Claude Code's transcript directory (read from the code). The forced save bypasses the cooldown and minimum-message gates, so by default the summarizer runs once per turn. Saves are incremental (**observed** on Windows: the position advances between firings and nothing was duplicated).

The Copilot CLI is different: on macOS it sent one `SessionEnd`, on `/exit`, with `reason=user_exit`, and the save ran then (**observed**, CLI 1.0.91). Its reason was not recorded on Windows. A CLI session ended without `/exit` would get no `SessionEnd` save (**reasoned**; see the gaps).

- **A. Immediate (default).** Observed cost $0.0043-$0.0070 per summarizer call on Windows and $0.0046-$0.0063 on macOS (Haiku through `claude -p`). One immediate save per turn was **observed** in VS Code on Windows on 2026-09-30 and 2026-10-03, and in VS Code and the desktop app on macOS on 2026-10-04. Once the launcher fix was in, the turn did not wait for the save on Windows (durations in the table above).
- **B. Debounced.** **Observed live** in VS Code on Windows on 2026-10-03 with `N`=30: a two-turn burst (the second prompt sent 8.2 s after the first reply, by the harness timestamps, and not held) logged `turn-end save deferred 30s` twice, then `turn-end save superseded by a later turn` for the first turn, and one save ran 30 s after the second turn, covering both (4 exchanges, 2 human). Not run live on macOS. Also pinned by `tests/test_session_end_turn_debounce_vscode.py`, which drives the real hook (observed passing under Git Bash on Windows and under bash on Linux in WSL; on macOS the suite passed, and its only skips in `*_vscode` modules were Windows-only).
- **Internals** (read from the code). During the window `tmp/save-session.pid` names the sleeping save, so PostToolUse delta saves are suppressed until it finishes; that is why `N` is capped at 3600. A value from 3601 to nine digits is used as 3600, with a `session-end: cooldowns.turn_end_debounce_seconds=<N> clamped to 3600` log line on each firing (the clamp is pinned by the test module above); a value of ten or more digits is read as `0`, like any other malformed value, with no log line. A value that is not a plain non-negative integer is read as `0`. The debounce applies only under the `copilot` hint and only for `reason=complete`, so it does not apply to the CLI's `user_exit` (observed on macOS).
- **Config cache (#843).** Before v0.37.0, deleting a per-project `config.json` left the merged-config cache serving the old value (**observed** on v0.36.0: the key stayed at 30 after the file was removed); upstream fixed it in v0.37.0 (#843, commit 7a8f415), which this port includes (**observed**: 30, then 0 after deleting the layer).

## Doctor on Copilot projects

`scripts/doctor.sh` prints `OK   copilot session-state dir present` when `~/.copilot/session-state` (or `$COPILOT_HOME/session-state`) exists.

A project used only through Copilot hosts never gets Claude Code's transcript directory (`~/.claude/projects/<slug>`), so the Paths section still prints `FAIL Session dir MISSING`. When the session id recorded with the last successful save names an existing `<COPILOT_HOME or ~/.copilot>/session-state/<uuid>/events.jsonl`, doctor prints `OK   last save came from a VS Code Agents / Copilot session (<path>); Claude Code's transcript dir is not expected (issue: vscode)` and the verdict reads `capture is working` (or the summarizer verdict) instead of the #144 slug-mismatch verdict. Without such a transcript the #144 verdict is unchanged. The id is read with `jq`, so without `jq` the check does not run and the old verdict stands.

Pinned by `tests/test_doctor_copilot_only_project_vscode.py` (**observed** on Windows Git Bash and Linux WSL; on macOS the suite passed, and its only skips in `*_vscode` modules were Windows-only), which also reproduces the pre-fix #144 verdict. Seen live on macOS on a CLI-only project (2026-10-04 entry above). Not seen live on Windows: the project used for those runs also has a Claude Code history.

## Coverage gaps and follow-ups

Gaps (the limitations a user needs are in [install-vscode.md](install-vscode.md#limitations)):

- **`userConfig` recovery token.** The path upstream v0.39.0 moved to `plugin.json` `userConfig` (#860, the `oauth_token` option) is unverified under the Copilot hosts: whether a host offers or passes that option to hooks was not checked on either platform (**not observed**).
- **Byte budget (#842).** Upstream's total SessionStart budget (`thresholds.session_start_max_bytes`, default 9,000 bytes) is measured on the plain-text recap's handoff and memory sections, before the copilot envelope wraps the recap; JSON escaping can make the injected string somewhat longer (**reasoned** from the code; the recaps in the Windows d54685c and 3311c67 runs were under it).
- **Promos.** Promos (`systemMessage`) are not emitted under the copilot hint (all three hosts); the emit branch skips them on purpose, as `systemMessage` was not probed (**reasoned** from the code). The promo-on pair in `tests/test_session_start_copilot_stdout_vscode.py` pins the skip; it is skipped on Windows and ran and passed on macOS (**observed**). No live run shows it.
- **Per-hook startup cost.** On Windows each hook starts one extra Windows PowerShell process on top of the shell VS Code starts. In the test harness on one machine a trivial hook through the launcher alone took about 0.5 s (0.36 s before the handle step was added), and the full manifest form about 0.2 s more (**observed** there, not measured in VS Code).
- **CLI session closed without `/exit`.** The CLI sends `SessionEnd` only on `/exit` (observed on macOS), so a session ended any other way, such as a killed terminal, would send none, and its turns since the last PostToolUse delta save would go unsaved (**reasoned**, not tested).
- **CLI `SessionEnd` reason on Windows.** Not recorded for CLI 1.0.92-3; the `user_exit` observation is from macOS, CLI 1.0.91.
- **Doctor on a mixed project.** When the last save came from a Copilot session, doctor reads `capture is working` even if the project's Claude Code slug is genuinely mismatched (#144): the Copilot transcript satisfies the same guard a present Claude Code session dir would, and only the earlier `FAIL Session dir MISSING` line hints otherwise (**reasoned** from the code, not observed).
- **Gemini hint.** Gemini CLI has no signature in `pipeline/host.REGISTRY`, so a Gemini session launched from a shell that exports `COPILOT_CLI` would get the copilot hint, and its recap the copilot envelope (**reasoned**, not observed).

Proposed follow-ups (none done in this port):

- **Version.** Record the running VS Code window's version in the next Windows run.
- **UserPromptSubmit.** `scripts/user-prompt-hook.sh` does not compute the copilot hint and switches on `CLAUDE_PROJECT_DIR`, which VS Code and the CLI set (observed on Windows), so it takes the plain-stdout branch. A fix needs the hook to resolve the hint, branch ahead of that switch, and emit a top-level `additionalContext` (the CLI did not inject the `hookSpecificOutput` shape its other branch emits), keeping the exit-0, never-block rule and weighing the cost on its fast path (#227).
- **Doctor.** The masking is in the verdict ladder: the Copilot transcript stands in for the Claude Code session dir, which is what stops a Copilot-only project getting the false #144 verdict. A sound fix needs evidence that the project is also used from Claude Code, such as a prior Claude Code save; doctor records none today.
- **Gemini.** Add the signature to `pipeline/host.REGISTRY` ahead of `COPILOT` and to the hand-kept list in `scripts/lib-session-id.sh`, which chooses the shell hint, in the same commit. `pipeline/host.py` takes signatures from a live hook-environment dump, and none exists for Gemini yet, so capture one first.
- **VS Code plugin sync fails on a `.git/` fsmonitor socket (macOS).** A candidate for an upstream VS Code report: the mirror copies the whole registered folder, `.git/` included, and one Unix-domain socket there (`.git/fsmonitor--daemon.ipc`) aborts the whole sync, leaving a partial mirror and no plugin hooks (observed on macOS, 2026-10-04 entry).
