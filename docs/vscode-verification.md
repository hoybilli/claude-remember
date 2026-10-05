# VS Code Agents / Copilot hosts: verification record and launcher internals

The evidence behind [install-vscode.md](install-vscode.md). Each claim names the hosts it was recorded for and the platform it was recorded on.

## Scope and labels

Three hosts run the same Copilot harness and load this plugin from its Claude-format manifest: VS Code Agents, the Copilot CLI and the GitHub Copilot desktop app.

- **Windows:** all three were run on Windows 11 on 2026-09-30 (VS Code only) and 2026-10-03.
- **macOS:** all three were run on 2026-10-04 on macOS 27.0.1, arm64 (Apple silicon).
- **Linux:** no host was run; the test suite was, as it was on macOS and Windows.

Labels:

- **Observed**: seen in a live host run or a test run on the stated platform.
- **Observed by simulation**: seen in a test that imitates the condition (a language mode or execution policy set in process), not under the real policy.
- **Reasoned**: derived from the code or documentation, not seen.
- **Read from the code**: a reasoned claim about this repo's own scripts, checked against the source, not against a run.
- **Not observed**: not tried; any claim rests on reasoning or documentation.

### Versions

Read on 2026-10-04; the app reports runtime `copilotVersion 0.0.0`.

| Component | Windows 11 | macOS 27.0.1 |
| --- | --- | --- |
| VS Code | 1.139.1 at the 2026-09-30 probes, 1.140.0 after reloads (observed); 1.140.0 for 2026-10-03 (reasoned: installed 2026-09-30, unchanged since; a window running since before that update would have used the older build) | 1.140.0 (observed) |
| Copilot Chat | 0.68.0 for 2026-10-03 (reasoned: ships in VS Code 1.140.0) | 0.68.0 (observed after the run) |
| Copilot CLI | 1.0.92-3, prerelease (observed) | 1.0.91, stable (observed) |
| Desktop app | 1.1.26 (reasoned: installed 2026-10-03; later auto-update not excluded) | 1.1.26 (observed) |
| `claude` CLI | 2.1.284 (reasoned: version on 2026-10-04) | 2.1.284 (observed after the runs) |
| jq | 1.8.2 (installed 2026-09-28) | 1.7.1 |
| git, bash | Git for Windows 2.53.0.2, bash 5.2.37; PowerShell 5.1 | git 2.54.0, `/bin/bash` 3.2.57 |
| Python | 3.14.4 | Hooks log only `PYTHON=python3`: Homebrew's 3.14.7 in a login shell. The GUI hosts' PATH was not observed, so possibly `/usr/bin/python3` 3.9.6. Both passed |

This repo has no issue number for this port yet; tests and fixtures carry the placeholder token `vscode` in their names (`tests/test_*_vscode.py`, `tests/fixtures/vscode-*`).

Changes and the tests that pin them (read from the code and the tests):

| Change | Files | Pinned by |
| --- | --- | --- |
| Copilot host signature; `agent-host-*:/` id prefix | `pipeline/host.py`, `scripts/lib-session-id.sh` | `test_copilot_signature_vscode`, `test_session_id_normalize_vscode` |
| Copilot transcript: find, sniff, read | `pipeline/host.py`, `pipeline/extract.py`, `scripts/post-tool-hook.sh` | `test_copilot_session_dir_vscode`, `test_copilot_envelope_vscode`, `test_copilot_exchange_vscode`, `test_post_tool_copilot_transcript_vscode` |
| Recap as top-level `additionalContext` | `scripts/session-start-hook.sh`, `pipeline/copilot_recap.py`, `pipeline/host.py` (`copilot_session`) | `test_session_start_copilot_stdout_vscode`, `test_copilot_recap_vscode`, `test_session_start_host_rule_vscode` |
| Windows launcher | `hooks/hooks.json`, `scripts/run-hook.ps1` | `test_hooks_json` |
| Turn-end debounce | `scripts/session-end-hook.sh` | `test_session_end_turn_debounce_vscode` |
| Doctor on Copilot-only projects | `scripts/doctor.sh` | `test_doctor_copilot_only_project_vscode` |
| Summarizer fallback warning; no vanished-transcript receipt for VS Code's unopenable `transcript_path` | `pipeline/haiku.py` | `test_copilot_summarizer_warning_vscode`, `test_session_start_host_rule_vscode` |
| Tests isolated from a Copilot shell | `tests/conftest.py` | `test_conftest_copilot_env_scrub_vscode` |

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

All cells **observed** on 2026-10-04, except that the CLI's recap reaching the model is inferred from its recorded `additionalContext` and the Windows probe; details in [the macOS entry](#2026-10-04-macos-vs-code-agents-copilot-cli-desktop-app).

| | VS Code Agents | Copilot CLI | Desktop app |
| --- | --- | --- | --- |
| Hooks fired (plain bash, no launcher) | yes | yes | yes |
| Recap injected at SessionStart | yes (the model named the memory files, as reported by the user) | yes (top-level `additionalContext` recorded) | yes (the model named the memory files) |
| Transcript resolved; saves ran | yes | yes | yes |
| `SessionEnd` | after every turn, `reason=complete` | once, on `/exit`, `reason=user_exit` | after every turn, `reason=complete` |
| Hooks run from | VS Code's mirror | the `--plugin-dir` directory | the marketplace directory |
| SessionStart / PostToolUse / SessionEnd duration | 0.18-0.24 / 0.15-0.40 / 0.01 s | 0.14-0.21 / 0.08-0.19 / 0.01 s | 0.18 / 0.08 / 0.01 s |

## What the hosts send

- **Payload on stdin** (observed on Windows in VS Code and the CLI) is snake_case: `hook_event_name`, `session_id`, `cwd`, `timestamp`; plus `source` and `initial_prompt` (SessionStart), `prompt` (UserPromptSubmit), `tool_name` / `tool_input` / `tool_result` (PostToolUse), `reason` (SessionEnd). No `transcript_path`. Captured: `tests/fixtures/vscode-hook-stdin-vscode.json`. On macOS the CLI's harness recorded camelCase keys in its own records (sessionStart `[cwd, initialPrompt, sessionId, source, timestamp]`, userPromptSubmitted `[cwd, prompt, sessionId, timestamp]`, postToolUse `[cwd, sessionId, timestamp, toolArgs, toolName, toolResult]`, sessionEnd `[cwd, reason, sessionId, timestamp]`), while the hooks received the snake_case form on stdin, as on Windows (observed).
- **`session_id`** is a bare uuid on the plugin-hook path (observed on Windows, 2026-09-30 in VS Code and 2026-10-03 in the CLI). A prefixed form, `agent-host-copilotcli:/<uuid>`, was seen in the extension-host hook log on 2026-09-29. The prefix is stripped before each hook's character-allowlist validator, so both forms work: by `remember_session_id_resolve` (`scripts/lib-session-id.sh`) in the PostToolUse and SessionEnd hooks, and by an inline copy of the same strip in the SessionStart hook, which does not source that library (size budget; `tests/test_session_start_host_rule_vscode.py` pins the two as equal). An id that is unsafe after stripping (`agent-host-x:/../../x`) is emptied by the validators, and the hook runs as if no id arrived (pinned for SessionStart, PostToolUse and SessionEnd by the `*_vscode` hook tests).
- **Environment** (observed on Windows): in VS Code, `CLAUDE_PLUGIN_ROOT`, `COPILOT_PLUGIN_ROOT` and `PLUGIN_ROOT` (all the mirror path, backslash-separated), `CLAUDE_PROJECT_DIR` and `COPILOT_PROJECT_DIR` (the project), `COPILOT_HOME`, `COPILOT_CLI=1`, `AI_AGENT=github_copilot_vscode_agent`. The CLI's hooks carry `COPILOT_CLI=1`, `COPILOT_PLUGIN_ROOT`, `CLAUDE_PLUGIN_ROOT` and `CLAUDE_PROJECT_DIR` too. Captured: `tests/fixtures/vscode-env-vscode.txt`.
- **Host detection** (`COPILOT.signature_vars` in `pipeline/host.py`; `remember_session_id_resolve`) keys on `COPILOT_CLI` / `COPILOT_PLUGIN_ROOT`, not `COPILOT_HOME`. The shell hint has two arms (read from the code): a session id with the `agent-host-*:/` prefix sets the `copilot` hint regardless of other signatures; only the environment arm yields, to the signature of any host registered before Copilot in `pipeline/host.REGISTRY` (Claude Code, Codex, Antigravity), matching the Python check.
- **Transcript:** `~/.copilot/session-state/<uuid>/events.jsonl` (or under `$COPILOT_HOME`), one JSON object per line with a dotted `type` and a `data` object (format observed on Windows in VS Code; where it was resolved: the tables above; sample `tests/fixtures/vscode-events.jsonl`). It is found by file existence, not host detection: `find_session` in `pipeline/extract.py` and `post-tool-hook.sh` look up the stdin uuid via `copilot_transcript_for` (`pipeline/host.py`) and its shell mirror `remember_copilot_transcript_into` (`scripts/lib-session-id.sh`, which `doctor.sh` uses too, with `remember_copilot_state_dir_into` for the directory it prints). `sniff_envelope` reports such a file as `"copilot"` and `copilot_exchange` turns each line into a `(role, text)` exchange.
- **SessionStart stdout:** the recap is emitted as `{"additionalContext": "<recap>"}`. To keep the compiled hook inside v0.40.0's size budget, `scripts/session-start-hook.sh` only strips the `agent-host-*:/` prefix inline; when the prefix was there or `COPILOT_CLI` / `COPILOT_PLUGIN_ROOT` is set, it makes one Python call, `pipeline/copilot_recap.py`, which applies the exact host rule (`pipeline.host.copilot_session`, the same rule as `scripts/lib-session-id.sh`; `tests/test_session_start_host_rule_vscode.py` compares the two) and builds the envelope. The envelope therefore no longer needs `jq`. Its bytes are those the hook used to print (`$(jq -Rs ...)`, then `printf '%s\n'`): **observed** on Windows 11 with WinGet's native jq 1.8.2 under Git Bash, by running those two old lines through bash as the oracle (`tests/test_copilot_recap_vscode.py`, 217 cases; a 1,504-case fuzz). That includes the native build's CRLF line breaks inside the envelope and the single LF that ends it. It assumes a native jq on Windows: with an MSYS-built jq the old envelope was LF throughout. Linux and macOS are **reasoned** until that test runs there. Each plain-text fallback on the Copilot host is logged (pinned by `tests/test_session_start_copilot_stdout_vscode.py`): `session-start: copilot envelope failed, plain recap` when the Python call cannot run, and `session-start: copilot host, recap not buffered (trace on or tmp/ not writable), printed as plain text, which this host does not inject` for a `REMEMBER_TRACE` run or an unwritable `tmp/`. The live host runs in this file predate this change, and used the `jq` path. **Observed** in VS Code on Windows: of three shapes emitted in one hook entry, only the top-level `additionalContext` reached the model; `hookSpecificOutput.additionalContext` and plain text did not. Caveat: the shapes came from consecutive commands, so "unsupported" and "a later command's output wins" could not be separated.
- **UserPromptSubmit stdout:** in VS Code on Windows, output does not reach the model (**observed**: neither a `hookSpecificOutput` nor a top-level `additionalContext` from the plugin's hook was injected). The CLI differs (see its section), but this hook emits no top-level `additionalContext` (read from the code; see the UserPromptSubmit follow-up), so nothing it prints reaches the model there either, today. On macOS (**observed**): run by hand (with `COPILOT_CLI=1`, which this hook does not read; plain text is the branch it takes when `CLAUDE_PROJECT_DIR` is set), the hook prints a plain-text stamp (`[HH:MM TZ -- <user>]`), no JSON envelope, and exits 0. The harness writes one `userPromptSubmitted` hook.end per prompt (not per plugin), and none carried output from this plugin. In VS Code the first carries VS Code's own 1,157-character `additionalContext` hint (the `add_artifact_or_reference` nudge) and later ones no output key; in the CLI none carries output; the desktop app showed no such hint. The hook writes no line to the memory log. **Reasoned:** the hosts record UserPromptSubmit output only as JSON carrying `additionalContext`, so the stamp is dropped; whether it reaches the model otherwise was not observed.
- **Another plugin's hook failing.** The harness reports the merged SessionStart result as failed (`success=false`) when any other installed plugin's SessionStart hook fails (**observed** in VS Code on Windows: another plugin's hook failed under PowerShell in every session); this plugin's context was still injected. The CLI drops the whole batch's output, this plugin's recap included (**observed** on Windows); `success=false` was not recorded for the CLI.
- **Summaries** run through the `claude` CLI (Claude Code's; `claude -p`); there is no Copilot-native summarizer. `_choose_summarizer_provider` in `pipeline/haiku.py` warns when a Copilot transcript falls back to `claude`, but only when a transcript path is supplied (`REMEMBER_TRANSCRIPT_PATH`), which VS Code does not do (**observed** on Windows: the live save log shows `provider: claude` and no warning).
- **`.remember/`** is the project's usual directory, shared with Claude Code sessions in the same project. In the desktop app, a session with a Project attached uses that project's `.remember/`; one without runs in the app's chat workspace, `~/.copilot/chats/<date>/<slug>/`, and the plugin bootstraps a fresh `.remember/` there, so that memory is not the project's (both **observed** on Windows).
- **Install layout.**
  - VS Code copies the plugin into a mirror and runs hooks from it: under `%APPDATA%\Code\agentPlugins\` on Windows; on macOS at `~/Library/Application Support/Code/agentPlugins/file-<absolute path with / turned into ->/<11-hex id>/`, identical to the registered export (`diff -rq`), with `PIPELINE_DIR` pointing at it (both **observed**). On macOS nothing was synced on **Developer: Reload Window**; the sync ran when the first New Chat opened (**observed**). A path already registered is not re-mirrored when its files change (**observed** on Windows; on macOS stated in the run's notes, not tested).
  - The CLI runs hooks straight from the `--plugin-dir` or local-marketplace directory, no mirror (**observed** on Windows; macOS in the table).
  - The desktop app was given the plugin through the same local-marketplace route on both platforms; on Windows it never listed the plugin under Installed (**observed**).
  - VS Code reads the Claude-format `hooks/hooks.json` (**observed** on Windows and macOS); if a Copilot-native manifest (`.github/plugin/plugin.json` naming its own hooks file) is also present, it loads only that one and sends a different, camelCase payload (**observed** on Windows), so this plugin ships none.
  - The `copilot plugin install Digital-Process-Tools/claude-remember` route from GitHub: on Windows, `~/.copilot/installed-plugins/_direct/Digital-Process-Tools--claude-remember--remember/` exists from an install of the upstream repo made before this port (v0.36.0, a92a072), so the landing path is **observed** on Windows for a pre-port version; the route has not been run with this branch.

## How hooks are launched on Windows

On macOS none of this applies: all three hosts ran each entry's `command` (`bash "${CLAUDE_PLUGIN_ROOT}/scripts/<hook>.sh"`) with the stock `/bin/bash` 3.2.57, and the `powershell` key and the launcher were not involved (**observed**, 2026-10-04), the first live run of that path in any Copilot host. Linux is expected to behave the same (**reasoned**).

The live Windows runs used earlier forms of the entry (2026-09-30: `& "…\run-hook.ps1"`; 2026-10-03: `-NoProfile -ExecutionPolicy Bypass -File`). `-NoLogo`, `-NonInteractive`, the stdin hand-off, the encoding setup and the Constrained Language Mode path came later. They have run only in `tests/test_hooks_json.py` on Windows, not live in any host.

- **Observed:** VS Code runs a hook's `command` string through Windows PowerShell, where a bare `bash` can resolve to `C:\Windows\System32\bash.exe` (WSL) when System32 precedes Git on `PATH`: no Windows environment, no `/c/` paths, and the plugin's script is never reached. A `windows` override key is ignored.
- **Observed** (with the earlier forms above): a `powershell` key on the entry is honoured and replaces `command` (one invocation per entry). `hooks/hooks.json` carries, next to each unchanged `command`, a `powershell` key of exactly this form:

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

  The hook script is taken from the launcher's own directory (`$PSScriptRoot`, which is `<plugin root>\scripts`), not from an environment variable. The manifest names the launcher through `$env:CLAUDE_PLUGIN_ROOT`, so through the manifest it is reached only when that variable is set (read from `hooks/hooks.json`), and the script then comes from that same root. **Observed** on Windows: through the manifest, the script path bash receives is the same as when the launcher read `CLAUDE_PLUGIN_ROOT` itself, for a root with a trailing backslash or forward slashes too; the one textual difference seen is that `$PSScriptRoot` upper-cases a lower-case drive letter.
- **Stdin.** The launcher does not touch stdin or stdout; bash inherits both handles. The payload therefore arrives byte for byte, EOF is the host's, and a host that leaves stdin open is bounded by the hook's own `read -t 1`. **Observed** on Windows in `tests/test_hooks_json.py`, for the launcher alone and the full manifest value: the bytes bash reads equal the bytes written (non-ASCII and a mid-payload CR included), a payload over 1 MB arrives complete, and with stdin held open for 30 s a hook that reads with `read -t 1` and exits returns within the test's 20 s bound.
- **`-NonInteractive`** makes a launcher started without its script name fail (exit 1, a missing-parameter error on stderr) instead of prompting and taking the answer from the payload on stdin, as it does without the flag (both **observed** on Windows in `tests/test_hooks_json.py`).
- **`-NoLogo`** does **not** keep the banner off stdout when `-File` names a file that does not exist (an unset or stale `CLAUDE_PLUGIN_ROOT`): Windows PowerShell 5.1 prints its banner to stdout and fails before any script runs (**observed** on Windows in `tests/test_hooks_json.py`). All three hosts set `CLAUDE_PLUGIN_ROOT` on Windows (**observed** for VS Code and the CLI; for the desktop app, implied by its hooks reaching the launcher through that variable), so this does not arise through the manifest on them.
- **Constrained Language Mode** (AppLocker or WDAC script enforcement): the launcher checks its language mode before any step needing full language. Under Constrained Language Mode it skips the handle step below (refused there), runs the hook as usual, forwards its exit status, and writes `claude-remember: launcher: Constrained Language Mode; could not detach background work, the caller will wait for it` to stderr; the error path also exits 0 there. Launcher messages go to `[Console]::Error`; under Constrained Language Mode, which refuses that call, `Write-Error` is used instead. `Write-Warning` is avoided because Windows PowerShell 5.1 writes the warning stream to stdout, in front of a SessionStart hook's JSON (**observed** on Windows). **Observed by simulation** in `tests/test_hooks_json.py`, Windows only: the session's language mode is set to `ConstrainedLanguage` in process and the launcher called in it. Setting `__PSLockdownPolicy=4` in a child's environment did not engage the mode on this machine (the child reported `FullLanguage`, **observed**), so a real AppLocker / WDAC policy was not exercised.
- **Background work must not hold the hook's output open.** The hooks return at once and leave their work (the save, a due consolidation) to a detached background child; the host treats the hook as finished only when its stdout reaches end-of-file (shown for a Python caller in the test harness; **observed** for VS Code on Windows, see the [duration table](#2026-10-03-vs-code-agents-the-launcher-entry-and-its-detach-fix)).
- **Why the host waited.** Windows PowerShell 5.1 holds an extra inheritable duplicate of its own stdout handle (present from the start of the script, not created by the launcher), and every process it starts -- bash, and through bash every background child -- inherits it. So the detached child kept the host's stdout pipe open: `powershell.exe` exited in 0.4 s while the caller's stdout stayed open for the child's full 20 s. Starting bash differently (a captured pipeline, `System.Diagnostics.Process` with redirected streams, `cmd /c` with file redirections) did not help; each still inherits the duplicate (all **observed** in a test harness on Windows 11, Git Bash 5.2.37).
- **The fix.** Before starting bash the launcher clears the inherit flag on every handle except its three standard ones, which bash receives as its own stdio and does not pass on to a child whose stdio it redirected (**observed** on Windows: Git Bash started directly with piped stdio returns at once with the same background child).
- **The `Add-Type` step.** The handle step compiles a few lines of C# with `Add-Type` (about 0.15-0.25 s added per hook here). The helper is compiled in memory: with `TEMP` pointing at a directory that does not exist it still compiled and ran, and left no files in `%TEMP%` (**observed** on Windows); `Add-Type` still starts `csc.exe` (**reasoned**). The step is best effort: where it fails the launcher writes `claude-remember: launcher: could not detach background work (...); the caller will wait for it` to stderr, the hook still runs, and the host waits for the background work as before. `tests/test_hooks_json.py` pins a 15 s background child against a 10 s return bound (a broken launcher is held the full 15 s), with a foreground-sleep positive control.
- **Execution policy:** the entry passes `-ExecutionPolicy Bypass` to the child PowerShell. **Observed by simulation** with `-ExecutionPolicy Restricted` (the Windows client default) on the outer shell: each manifest value reached its script with the payload intact, while the earlier `& "...\run-hook.ps1"` form failed with `PSSecurityException`. That a `RemoteSigned` policy, or a real `CurrentUser` / `LocalMachine` setting, does not block it either follows from PowerShell's documented scope precedence (**reasoned**; not simulated). A policy enforced by Group Policy (`MachinePolicy` / `UserPolicy`) takes precedence over `-ExecutionPolicy` and cannot be overridden this way; whether it blocks depends on that policy's value (**not observed**). The live run's machine has `LocalMachine=Unrestricted`.
- **`claude plugin validate`** accepts the manifest with the `powershell` keys (**observed** on the current form: "Validation passed with warnings"; the one warning, that a root `CLAUDE.md` is not loaded as context, is unrelated to the hooks). That Claude Code ignores the key at run time and keeps using `command` is **reasoned** (the validator accepts it and the `command` strings are byte-for-byte unchanged), not observed in a Claude Code session.

## Verification log

### 2026-09-30: VS Code Agents, first live runs

**Observed** on Windows 11: three agent sessions against an existing project that already had a `.remember/` written by Claude Code. The plugin was a `git archive` export (LF line endings) of commit 39d1610, registered through `chat.pluginLocations` and a window reload. Its `powershell` entries used the earlier `& "$env:CLAUDE_PLUGIN_ROOT\scripts\run-hook.ps1" <name>.sh` form; the `-File` form was not used in these sessions. The probes of that day dumped the environment and stdin from inside firing plugin hooks, so what they report was seen, not only reasoned from documentation. The probes ran against 1.139.1 ([Versions](#versions)).

| Event | Fired | Notes |
| --- | --- | --- |
| SessionStart | yes | Once per session, in each of the three. The harness recorded `{"additionalContext": ...}` of 5,780 characters, and in session 1 the model's first reply listed the day file, `now.md`, `recent.md`, `archive.md`, the last handoff and the history note, so the recap reached the model. Hook log: `session-start took 2s`. |
| UserPromptSubmit | yes | Every turn; exited successfully. |
| PostToolUse | yes | Once per tool call in all three sessions (1, 1 and 2 calls). Log line: `post-tool: copilot transcript .../.copilot/session-state/<uuid>/events.jsonl`. |
| SessionEnd | yes | After every turn, not once per session, with `reason=complete` in every firing observed (four, across three sessions). Each ran the forced save: `extract`, the summarizer (`provider: claude`), a position update. |

End to end (**observed**):

- **`scripts/doctor.sh`**, run after session 1 (its timestamp precedes session 3's write): `VERDICT: capture is working -- last save 2026-09-30 21:13:09`.
- **Two sessions were SKIPped by the summarizer.** Session 1 (a question about the memory sections, then a command printing `hello`) and session 2 (one read-only turn summarising the README) were each extracted and the position advanced (in the log for both; session 2: `position -> 25`), but the summarizer returned `SKIP`, so nothing reached `now.md`. Re-running the extractor on session 2 gave three exchanges with the right roles (the prompt, the tool-only assistant turn as a `[TOOL: ...]` line, the answer); session 1's extract was not inspected. The save prompt asks for `SKIP` on a span with no substantive work, and it did (**observed**); that this is intended, not a capture failure, is **reasoned** from the prompt text and from session 3 writing normally.
- **Save written to the shared timeline.** Session 3, one turn that created a small file: `extract` found 4 exchanges (1 human), the summarizer returned an entry (`[write] appended (provider: claude): ## 21:21 | <branch>`), the position advanced (`[write] position -> 34`), and compression moved the entry from `now.md` into the day file (`now.md -> today-2026-09-30.md`, `752->360b`), which holds a one-line entry naming the file. This is the first observation of a VS Code Agents session being summarised into the same `.remember/` timeline Claude Code uses.

### 2026-10-03: VS Code Agents, the launcher entry and its detach fix

**Observed** on Windows 11, against the same project, with the plugin again a `git archive` export registered through `chat.pluginLocations`. Runs before the launcher fix used an export of d54685c; runs after it used 3311c67 (which contains the fix, 07c0e7d), and a final round used b40bf48. All three carry the `powershell -NoProfile -ExecutionPolicy Bypass -File ... run-hook.ps1` entries. Later launcher changes: see [How hooks are launched on Windows](#how-hooks-are-launched-on-windows).

| Event | Fired | Notes (exports of d54685c, 3311c67 and b40bf48) |
| --- | --- | --- |
| SessionStart | yes | Through the launcher from VS Code's mirror, like every hook below. Injected the recap as `{"additionalContext": ...}` (5,009 characters on d54685c, 4,802 on 3311c67, 2,564 on b40bf48, where upstream's #842 handoff redelivery cap held back a handoff already delivered many times); in the d54685c and 3311c67 runs the model listed the memory sections. |
| UserPromptSubmit | yes | Through the launcher. 2026-10-03: its output still does not reach the model. |
| PostToolUse | yes | Resolved the Copilot transcript. |
| SessionEnd | yes | After every turn, as on 2026-09-30. |

**Hook durations, before and after the detach fix** (07c0e7d), from the harness's `hook.start` / `hook.end` records in `events.jsonl`. Before the fix each hook lasted as long as its background work, so the turn waited for the whole save and the UI showed the next prompt as queued ("steering"); after it, each hook took only its own run time, and a burst's second prompt was no longer queued behind the first turn's save. All **observed** on Windows:

| Hook | Background work | Before (d54685c) | After (3311c67, then b40bf48) |
| --- | --- | --- | --- |
| SessionStart, daily consolidation due | consolidation (a summarizer call, ~35 s) | 40.0 s (one session) | not recorded |
| SessionStart | -- (nothing due) | 2.6-2.7 s | 2.7 s, 2.9 s (3311c67) and 3.0 s (b40bf48), one session each |
| SessionEnd, default | forced save: extract + summarizer (~5 s) | 7.4 s and 8.5 s (one session, two turns) | 0.7 s (3311c67, session A, two turns); 0.9-1.0 s (b40bf48, two turns) |
| SessionEnd, after a session that wrote a file | forced save + compression (~45 s) | 55.1 s (one session) | not recorded |
| SessionEnd, `turn_end_debounce_seconds=30` | 30 s sleep + forced save | 38-39 s (three sessions) | 0.7 s (3311c67, session B, two turns) |
| PostToolUse | background save | 1.1-1.7 s (four firings) | 1.5 s (3311c67) and 2.5 s (b40bf48) |

The fix was first shown in the test harness -- the real `scripts/session-end-hook.sh`, with a stub save sleeping 10 s, returned through the launcher in 0.7 s (`-File`) and 0.9 s (the full manifest form) instead of 11.8 s, and the save still completed about 11 s later -- and the VS Code durations dropped exactly as that predicted.

### 2026-10-03: Copilot CLI

**Observed** on Windows 11, the plugin loaded with `--plugin-dir` and, separately, from a local directory marketplace:

- The hooks ran through `scripts/run-hook.ps1` straight from that directory. SessionStart took 2.6-2.7 s and SessionEnd 0.8 s. The recap was injected: the model listed the memory files.
- A probe hook emitting several shapes showed that the CLI injects a top-level `additionalContext` for SessionStart **and** for UserPromptSubmit (VS Code does not for the latter); `hookSpecificOutput.additionalContext` and plain text were not injected.
- A `copilot` started from inside a Claude Code session inherits its `CLAUDE_CODE_*` variables, so the hint resolves to Claude Code and the recap goes out as plain text, which the CLI does not inject. With those variables removed the recap was injected. For VS Code the same case is **reasoned**, not observed.

### 2026-10-03: Copilot desktop app

**Observed** on Windows 11, the plugin given through a local directory marketplace: every hook fired through the launcher (SessionStart 2.2 s, SessionEnd 0.7 s, PostToolUse 1.4 s), the Copilot transcript was resolved and the saves ran.

### 2026-10-04: macOS (VS Code Agents, Copilot CLI, desktop app)

**Observed** unless marked, on the Mac ([Versions](#versions)). Launch path and durations are in [the macOS table](#on-macos-27); mirror, sync, UserPromptSubmit and payload keys under "What the hosts send".

Every macOS live run used commit c981dde: the CLI's first run used `--plugin-dir` pointed at a git checkout at c981dde; VS Code (after the failed sync from that checkout), the CLI's second run and the desktop app used a `git archive` export of c981dde. So the shell and pipeline code changed after the last Windows live run (b40bf48: e629250, a6953ae, 443b698) ran live on macOS; the later Windows launcher changes did not (see [How hooks are launched on Windows](#how-hooks-are-launched-on-windows)).

**VS Code Agents, registered from a git checkout: the sync failed.** With the git clone itself registered through `chat.pluginLocations`, VS Code's `agenthost.log` logged, 14 times:

```
[AgentPluginManager] Failed to sync plugin file://<checkout>: Error: Unknown system error -102: Unknown system error -102, open '<checkout>/.git/fsmonitor--daemon.ipc'
```

VS Code's mirror copies the whole registered folder, `.git/` included. With `core.fsmonitor=true` (set in that clone's local `.git/config`; global and system unset; what set it was not observed), `.git/fsmonitor--daemon.ipc` is a Unix-domain socket, `open()` on it fails (errno 102), and the sync aborts, leaving a partial mirror. No plugin hook ran, though the harness's hook records appeared. On Windows the fsmonitor IPC is a named pipe, not a file in `.git/`, so the same failure is not expected there (**reasoned**). Work-arounds: register a copy without `.git/` (a `git archive` export, the method used for every other VS Code run on both platforms) or, **not tried**, turn `core.fsmonitor` off in that checkout (`git config core.fsmonitor false`, then stop its daemon); either way re-register under a new path ("Install layout").

**VS Code Agents, registered from a `git archive` export: full pass**, three sessions. SessionStart injected a top-level `additionalContext` of 546 characters each time; in session 1 the model named the memory files (as reported by the user). SessionStart's hook records come after the first UserPromptSubmit's; the recap was still injected. PostToolUse logged `post-tool: copilot transcript ~/.copilot/session-state/<uuid>/events.jsonl`. `SessionEnd` came with `reason=complete` after **every** turn (four turns), and a forced save ran each time (three `SKIP`s from the summarizer, one entry). `hook-errors.log` empty; no stray processes.

**Copilot CLI**, `copilot --plugin-dir <dir>` started from a plain Terminal (not inside Claude Code); two sessions, full pass. SessionStart's top-level `additionalContext` was 388 and 629 characters. A two-turn session sent a single `SessionEnd`, on `/exit`, with `reason=user_exit`, not one per turn; the forced save ran then and wrote an entry. So `cooldowns.turn_end_debounce_seconds`, which acts only on `reason=complete` (read from the code), does not apply to this `/exit` save. (A session ended without `/exit`: see the gaps.)

**Copilot desktop app**, through a temporary local directory marketplace: the manifest at `<marketplace dir>/.claude-plugin/marketplace.json`; `copilot plugin marketplace add <marketplace dir>` printed `Marketplace "remember-local" added successfully.`; then `copilot plugin install remember@remember-local`.

- **An absolute plugin `source` in `marketplace.json` is rejected** by `copilot plugin install` with `Plugin path escapes marketplace directory: <path>`, and the failed install leaves a disabled "live plugin" entry behind. A relative `"source": "./remember"`, with the plugin copied inside the marketplace directory, installs ("loaded live … nothing was copied"). `~/.copilot/settings.json` did not exist before the install; the install created it.
- One new chat with a Project attached, two turns: a top-level `additionalContext` of 679 characters, and the model **named the memory files**; **`SessionEnd` with `reason=complete` after every turn**; a save per turn (one `SKIP`, one entry). `hook-errors.log` empty; no stray processes.
- **Removing it.** `copilot plugin uninstall remember@remember-local` only disables a live (local-marketplace) plugin: "Plugin … disabled. It is still on disk at <marketplace dir> — nothing was removed." `copilot plugin marketplace remove remember-local` then removed the marketplace. After uninstall and remove, `~/.copilot/settings.json` still held `{"extraKnownMarketplaces": {}, "enabledPlugins": {"remember@remember-local": false}}` (inert); it was deleted by hand, with the user's OK, since it had not existed before the install. The same stale `false` key was seen on Windows.

**`scripts/doctor.sh`**, on a CLI-only project with `CLAUDE_PROJECT_DIR` set, printed `FAIL Session dir MISSING` (expected), `OK   copilot session-state dir present`, the `OK   last save came from a VS Code Agents / Copilot session` line quoted under [Doctor](#doctor-on-copilot-projects) and `VERDICT: capture is working`; `jq` was reported as `/usr/bin/jq (jq-1.7.1-apple)`.

### Linux: the test suite

No host ran on Linux; the suite did (**observed** on WSL Ubuntu 26.04, Python 3.14, a `git archive` export of b40bf48). Three failures were this port's and are fixed (the `jq`-word lint tripped by a log line; two SessionStart plain-text controls that switched promos off through a variable nothing reads). The final run, on a git-backed `git archive` export of e4649ee (the adversarial fix wave) with the same setup, gave 4 failed / 3,331 passed / 87 skipped; the four are `test_lock_primitive` cases that also fail on `main` (9e78169). Every `*_vscode` module and both detach modules ran rather than skipping. Later, at e52b5d2 (WSL Ubuntu, Python 3.14.4): 3,307 passed / 4 failed / 90 skipped, coverage 93%; the 4 are the `test_lock_primitive` overlap cases, which also fail on `main` (**observed**).

### Windows: the test suite

At e52b5d2 (Python 3.14.4): 1,713 passed / 3 failed / 1,685 skipped, coverage 90%; the 3 failures are `test_autonomous_log_retention_487`, pre-existing on Windows (**observed**). No branch-caused failure on Windows or Linux at e52b5d2.

### macOS: the test suite

On the Mac above, at 62f3813 (**observed**): 1 failed / 3,299 passed / 101 skipped on Python 3.9.6 (12m55s), 2 failed / 3,298 passed / 101 skipped on 3.14.7 (12m34s), coverage 93%. Both failures were in this port's tests: a JSON error type that differs between Python builds (3.14.7 raised `JSONDecodeError` where the test expected `RecursionError`), and a harness that hid `jq` by dropping `/usr/bin`, which also hid `tr`. Both are fixed (3bad563, c981dde) and the two modules re-run green (15 and 14 passed on both Pythons); the full suite was not re-run. The SessionStart promo-on pair, skipped on Windows, ran and passed. The debounce and doctor modules were not among the suite's failures, and every skip in the `*_vscode` modules (29, with `test_hooks_json`) was Windows-only, so they ran and passed there (derived from the suite totals; per-module results were not recorded). No test hung, and no stray `sleep 3600` was left after either run.

## Saves per turn: evidence

Per-turn `SessionEnd`: see the per-host tables. On close, VS Code sent nothing (**observed** on Windows; not recorded on macOS). The last turn of a session is saved only by its own `SessionEnd`: the next session has a new id, and start-up recovery reads only Claude Code's transcript directory (read from the code). An earlier turn that its own save misses is picked up by the next save, which starts from the saved position (**observed** on Windows, see B below). The forced save bypasses the cooldown and minimum-message gates, so by default the summarizer runs once per turn. Saves are incremental (**observed** on Windows: the position advances between firings and nothing was duplicated). The CLI differs: see [the macOS entry](#2026-10-04-macos-vs-code-agents-copilot-cli-desktop-app).

- **A. Immediate (default).** Observed cost $0.0043-$0.0070 per summarizer call in VS Code on Windows and $0.0046-$0.0063 in VS Code on macOS (Haiku through `claude -p`).
- **B. Debounced.** **Observed live** in VS Code on Windows on 2026-10-03 with `N`=30: a two-turn burst (the second prompt sent 8.2 s after the first reply, by the harness timestamps, and not held) logged `turn-end save deferred 30s` twice, then `turn-end save superseded by a later turn` for the first turn, and one save ran 30 s after the second turn, covering both (4 exchanges, 2 human). Not run live on macOS. Also pinned by `tests/test_session_end_turn_debounce_vscode.py`, which drives the real hook (observed passing under Git Bash on Windows and under bash on Linux in WSL; for macOS see the suite section).
- **Internals** (read from the code). During the window `tmp/save-session.pid` names the sleeping save, so PostToolUse delta saves are suppressed until it finishes; that is why `N` is capped at 3600. A value from 3601 to nine digits is used as 3600, with a `session-end: cooldowns.turn_end_debounce_seconds=<N> clamped to 3600` log line on each firing (the clamp is pinned by the test module above); a value of ten or more digits, like any value that is not a plain non-negative integer, is read as `0`, with no log line. The debounce applies only under the `copilot` hint, for `reason=complete`, with a session id on stdin; if the token file cannot be written the save runs at once (`session-end: could not write … -- turn-end save not deferred`).
- **Config cache (#843).** Deleting a per-project `config.json` takes effect (**observed**: 30, then 0 after deleting the layer). On v0.36.0 it did not (**observed**: the key stayed at 30), fixed upstream in v0.37.0 (#843, 7a8f415), which this port includes.

## Doctor on Copilot projects

`scripts/doctor.sh` prints `OK   copilot session-state dir present` when `~/.copilot/session-state` (or `$COPILOT_HOME/session-state`) exists.

A project used only through Copilot hosts never gets Claude Code's transcript directory (`~/.claude/projects/<slug>`), so the Paths section still prints `FAIL Session dir MISSING`. When the session id recorded with the last successful save names an existing `<COPILOT_HOME or ~/.copilot>/session-state/<uuid>/events.jsonl`, doctor prints `OK   last save came from a VS Code Agents / Copilot session (<path>); Claude Code's transcript dir is not expected (issue: vscode)` and the verdict reads `capture is working` (or the summarizer verdict) instead of the #144 slug-mismatch verdict. Without such a transcript the #144 verdict is unchanged. The id is read with `jq`, so without `jq` the check does not run and the old verdict stands.

Pinned by `tests/test_doctor_copilot_only_project_vscode.py` (**observed** on Windows Git Bash and Linux WSL; for macOS see the suite section), which also reproduces the pre-fix #144 verdict. **Observed** live on macOS on a CLI-only project (2026-10-04 entry); not on Windows, where the project used also has a Claude Code history.

## Coverage gaps and follow-ups

Gaps (the limitations a user needs are in [install-vscode.md](install-vscode.md#limitations)):

- **`userConfig` recovery token.** Whether a Copilot host offers or passes upstream v0.39.0's `plugin.json` `userConfig` option (#860, `oauth_token`) to hooks was not checked on either platform (**not observed**).
- **Byte budget (#842).** Upstream's total SessionStart budget (`thresholds.session_start_max_bytes`, default 9,000 bytes) is measured on the plain-text recap's handoff and memory sections, before the copilot envelope wraps the recap; JSON escaping can make the injected string somewhat longer (**reasoned** from the code; the recaps in the Windows d54685c and 3311c67 runs were under it).
- **Promos.** Promos (`systemMessage`) are not emitted under the copilot hint (all three hosts); the emit branch skips them on purpose, as `systemMessage` was not probed (read from the code). The promo-on pair in `tests/test_session_start_copilot_stdout_vscode.py` pins the skip (see the macOS suite section). No live run shows it.
- **Per-hook startup cost.** On Windows each hook starts one extra Windows PowerShell process. In the test harness on one machine a trivial hook took about 0.5 s through the launcher alone (0.36 s before the handle step) and about 0.2 s more in the full manifest form (**observed** there, not measured in VS Code).
- **CLI session ended without `/exit`.** No other way of ending a CLI session than `/exit` was tried. One that sends no `SessionEnd`, such as a killed terminal, would leave its turns since the last PostToolUse delta save unsaved (**reasoned**, not tested). The CLI's `SessionEnd` reason on Windows was not recorded.
- **Doctor on a mixed project.** When the last save came from a Copilot session, doctor reads `capture is working` even if the project's Claude Code slug is genuinely mismatched (#144): the Copilot transcript satisfies the same guard a present Claude Code session dir would, and only the earlier `FAIL Session dir MISSING` line hints otherwise (**reasoned** from the code, not observed).
- **Gemini hint.** Gemini CLI has no signature in `pipeline/host.REGISTRY`, so a Gemini session launched from a shell that exports `COPILOT_CLI` would get the copilot hint, and its recap the copilot envelope (**reasoned**, not observed).
- **Windows versions, 2026-10-03.** VS Code and desktop app are reasoned from install records, not printed during the run.

Proposed follow-ups (none done in this port):
- **UserPromptSubmit.** `scripts/user-prompt-hook.sh` does not compute the copilot hint and switches on `CLAUDE_PROJECT_DIR`, which VS Code and the CLI set (observed on Windows), so it takes the plain-stdout branch. A fix needs the hook to resolve the hint, branch ahead of that switch, and emit a top-level `additionalContext` (the CLI did not inject the `hookSpecificOutput` shape its other branch emits), keeping the exit-0, never-block rule and weighing the cost on its fast path (#227). Evidence: on macOS the hook printed its plain-text stamp and no host recorded it (see "UserPromptSubmit stdout").
- **Doctor.** The masking above is also what spares a Copilot-only project the false #144 verdict, so a sound fix needs evidence that the project is also used from Claude Code, such as a prior Claude Code save; doctor records none today.
- **Gemini.** Capture a live hook-environment dump first (`pipeline/host.py` takes signatures from one; none exists for Gemini), then add the signature to `pipeline/host.REGISTRY` ahead of `COPILOT` and, in the same commit, to the hand-kept list in `scripts/lib-session-id.sh` that chooses the shell hint.
- **VS Code plugin sync fails on a `.git/` fsmonitor socket (macOS).** A candidate upstream VS Code report; the mechanism and work-arounds are in the 2026-10-04 entry.
