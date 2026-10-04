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
- On Windows you need Git for Windows (Git Bash) and `jq`, same as [windows.md](windows.md). Without `jq` nothing is injected on this host: the recap falls back to plain text, which VS Code does not inject, and the SessionStart hook logs `session-start: copilot host without jq -- ...` so the silence is diagnosable (read from the code).
- Summaries run through the `claude` CLI (Claude Code's), which must be installed and on `PATH`; see **Summaries** below.

## How hooks are launched on Windows

- **Observed:** VS Code runs a hook entry's `command` string through Windows PowerShell. There a bare `bash` can resolve to `C:\Windows\System32\bash.exe` (WSL) when System32 precedes Git on `PATH`: no Windows environment, no `/c/` paths, and the plugin's script is never reached. A `windows` override key is ignored.
- **Observed:** a `powershell` key on the entry is honoured and replaces `command` (one invocation per entry). `hooks/hooks.json` therefore carries, next to each unchanged `command`, a `powershell` key of exactly this form:

  ```
  powershell -NoProfile -ExecutionPolicy Bypass -File "$env:CLAUDE_PLUGIN_ROOT\scripts\run-hook.ps1" <name>.sh; exit $LASTEXITCODE
  ```

  It starts a child Windows PowerShell that runs `scripts/run-hook.ps1` with the hook script's file name. The launcher locates Git Bash explicitly, forwards stdin (the payload) and stdout as UTF-8, and returns bash's exit status; `exit $LASTEXITCODE` passes that exact status on (**observed** on Windows in `tests/test_hooks_json.py`, which also checks the payload reaches bash byte-for-byte, non-ASCII included).
- **Launcher lookup order** (read from `scripts/run-hook.ps1`), first existing file wins:
  1. `$env:REMEMBER_BASH`, if set (a path that does not exist falls through to the next step);
  2. `Git\bin\bash.exe` under `%ProgramFiles%`;
  3. the same under `%ProgramFiles(x86)%`;
  4. the same under `%LOCALAPPDATA%\Programs` (skipped when `LOCALAPPDATA` is unset);
  5. the first `bash.exe` on `PATH` whose path contains neither `System32` nor `WindowsApps` (WSL's launcher and its Microsoft Store alias).

  If none is found it writes `claude-remember: Git Bash not found; install Git for Windows or set REMEMBER_BASH to bash.exe` to stderr and exits 0, so the hook does nothing rather than failing the session. Any other error inside the launcher itself (for example a `REMEMBER_BASH` that is not a program) is written as `claude-remember: launcher error: ...` to stderr and also exits 0. Only bash's own exit status is forwarded, and the hook scripts exit 0 on every path. The plugin root is taken from `CLAUDE_PLUGIN_ROOT`, then `COPILOT_PLUGIN_ROOT`, then the launcher's own parent directory.
- **Background work must not hold the hook's output open.** The hooks return at once and leave their work (the save, a due consolidation) to a detached background child; the host treats the hook as finished only when its stdout reaches end-of-file (**reasoned** for VS Code: shown for a Python caller in the test harness, and inferred for VS Code from hook durations that match the background work). Windows PowerShell 5.1 holds an extra, inheritable duplicate of its own stdout handle (present from the start of the script, not created by the launcher), and every process it starts -- bash, and through bash every background child -- inherits every inheritable handle. So the detached child kept a copy of the host's stdout pipe and the host waited for it to finish: `powershell.exe` itself exited in 0.4 s while the caller's stdout stayed open for the child's full 20 s, and changing how the launcher starts bash (a captured pipeline, `System.Diagnostics.Process` with redirected streams, `cmd /c` with file redirections) did not help, because each of those still inherits the duplicate (all **observed** in a test harness on Windows 11, Git Bash 5.2.37). Before starting bash the launcher therefore clears the inherit flag on every handle it holds except its three standard ones, which bash receives as its own stdio and does not pass on to a child whose stdio it redirected (**observed**: Git Bash started directly with piped stdio returns at once with the same background child). That step compiles a few lines of C# with `Add-Type` (about 0.15-0.25 s added per hook here); it is best effort, so where it fails the launcher writes `claude-remember: launcher: could not detach background work (...); the host will wait for it` to stderr, the hook still runs, and the host just waits for the background work, as before. `tests/test_hooks_json.py` pins a 15 s background child against a 10 s return bound (a broken launcher is held for the full 15 s), with a foreground-sleep positive control.
- **Execution policy:** the entry passes `-ExecutionPolicy Bypass` to the child PowerShell, so a `Restricted` (the Windows client default) or `RemoteSigned` policy on the shell VS Code starts does not block it. **Observed** by simulation with `-ExecutionPolicy Restricted` on the outer shell: each manifest value reached its script with the payload intact, while the earlier `& "...\run-hook.ps1"` form failed under the same simulation with `PSSecurityException`. A real `CurrentUser` / `LocalMachine` setting and `RemoteSigned` were not simulated; that the child's `-ExecutionPolicy Bypass` outranks them follows from PowerShell's documented scope precedence (**reasoned**). A policy enforced by Group Policy (the `MachinePolicy` / `UserPolicy` scopes) takes precedence over `-ExecutionPolicy` and cannot be overridden this way (**not observed**). The live run's machine has `LocalMachine=Unrestricted`.
- `claude plugin validate` accepts the manifest with the `powershell` keys (**observed**, re-run on the current form). That Claude Code ignores the key at run time and keeps using `command` is **reasoned** (the validator accepts it and the `command` strings are byte-for-byte unchanged), not observed in a Claude Code session. macOS/Linux VS Code is expected to use `command` (**reasoned**).

## What VS Code sends, and what this plugin does with it

- **Payload on stdin** (observed) is snake_case: `hook_event_name`, `session_id`, `cwd`, `timestamp`; plus `source` and `initial_prompt` (SessionStart), `prompt` (UserPromptSubmit), `tool_name` / `tool_input` / `tool_result` (PostToolUse), `reason` (SessionEnd). There is no `transcript_path`. A captured payload is `tests/fixtures/vscode-hook-stdin-vscode.json`.
- **`session_id`** is a bare uuid on the plugin-hook path (observed 2026-09-30). A prefixed form, `agent-host-copilotcli:/<uuid>`, was seen in the extension-host hook log on 2026-09-29. `scripts/lib-session-id.sh` (`remember_normalize_session_id`) strips that prefix before the hooks' character-allowlist validators, so both forms are handled.
- **Environment** (observed): `CLAUDE_PLUGIN_ROOT`, `COPILOT_PLUGIN_ROOT` and `PLUGIN_ROOT` (all the mirror path, backslash-separated), `CLAUDE_PROJECT_DIR` and `COPILOT_PROJECT_DIR` (the project), `COPILOT_HOME`, `COPILOT_CLI=1`, `AI_AGENT=github_copilot_vscode_agent`. Host detection (`COPILOT.signature_vars` in `pipeline/host.py`, and `remember_session_id_host_hint` in `scripts/lib-session-id.sh`) keys on `COPILOT_CLI` / `COPILOT_PLUGIN_ROOT`, not `COPILOT_HOME`; the signature of any host registered before Copilot in `pipeline/host.REGISTRY` (Claude Code, Codex, Antigravity) wins when also present, in both the Python and the shell check. The captured environment is `tests/fixtures/vscode-env-vscode.txt`.
- **Transcript:** `~/.copilot/session-state/<uuid>/events.jsonl` (or under `$COPILOT_HOME`), one JSON object per line with a dotted `type` and a `data` object (observed; sample in `tests/fixtures/vscode-events.jsonl`). It is found by file existence, not by host detection: `find_session` in `pipeline/extract.py` and `post-tool-hook.sh` both look up the stdin uuid via `copilot_transcript_for` (`pipeline/host.py`) / `remember_copilot_transcript_for` (shell mirror). `sniff_envelope` reports such a file as `"copilot"` and `copilot_exchange` turns each line into a `(role, text)` exchange.
- **SessionStart stdout:** on this host the memory recap is emitted as `{"additionalContext": "<recap>"}` (the emit branch at the end of `scripts/session-start-hook.sh`, taken when `REMEMBER_HOST_HINT=copilot` and `jq` is available; otherwise the plain recap is printed, which this host does not inject -- without `jq` the hook logs a line saying so). **Observed:** of three shapes emitted in one hook entry, only the top-level `additionalContext` reached the model; `hookSpecificOutput.additionalContext` and plain text did not. Caveat: the shapes were emitted by consecutive commands, so "unsupported" and "a later command's output wins" could not be separated.
- **Summaries** run through the `claude` CLI on this host (`claude -p`), so Claude Code's CLI must be installed and on `PATH`; there is no Copilot-native summarizer. The save log records `provider: claude` (**observed**). `_choose_summarizer_provider` in `pipeline/haiku.py` has an explicit warning for a Copilot transcript falling back to `claude`, but it is only logged when a transcript path is supplied to the pipeline (`REMEMBER_TRANSCRIPT_PATH`), which VS Code does not do (**observed**: the live run's save log shows `provider: claude` and no such warning).
- **`.remember/`** is the project's usual directory, shared with Claude Code sessions in the same project.
- `scripts/doctor.sh` prints an `OK   copilot session-state dir present` line when `~/.copilot/session-state` (or `$COPILOT_HOME/session-state`) exists.
- **`scripts/doctor.sh` on a project used only through VS Code Agents.** Such a project never gets Claude Code's transcript directory (`~/.claude/projects/<slug>`), so the Paths section still prints `FAIL Session dir MISSING`. When the session id recorded with the last successful save names an existing `<COPILOT_HOME or ~/.copilot>/session-state/<uuid>/events.jsonl`, doctor prints `OK   last save came from a VS Code Agents / Copilot session (<path>); Claude Code's transcript dir is not expected (issue: vscode)` and the verdict reads `capture is working` (or the summarizer verdict) instead of the #144 slug-mismatch verdict. Without such a transcript the #144 verdict is unchanged. The session id is read with `jq`, so without `jq` the Copilot check does not run and the old verdict stands. (Read from the code and pinned by `tests/test_doctor_copilot_only_project_vscode.py`, **observed** under Git Bash on Windows; **reasoned** for macOS/Linux. Before this check, a Copilot-only project got the #144 verdict; that is reproduced by the same test, not observed in a live VS Code project, since the live project also has a Claude Code history.)

## Verification

**Observed** on 2026-09-30 (Windows 11, Git for Windows bash 5.2.37, Windows PowerShell 5.1): three agent sessions on the Copilot harness, run against an existing project that already had a `.remember/` directory written by Claude Code. The plugin was a `git archive` export (LF line endings) of commit 39d1610, registered through `chat.pluginLocations` and followed by a window reload; VS Code ran the hooks from its mirror under `%APPDATA%\Code\agentPlugins\`. The earlier probes were recorded against VS Code 1.139.1; `code --version` reported 1.140.0 when these sessions ran, and the running window's version was not separately confirmed. That commit's `powershell` entries used the earlier `& "$env:CLAUDE_PLUGIN_ROOT\scripts\run-hook.ps1" <name>.sh` form; the current `powershell -NoProfile -ExecutionPolicy Bypass -File ...` form was not used in these three sessions; it was first run live on 2026-10-03, of which this document records the hook durations below.

| Event | Fired | Notes |
| --- | --- | --- |
| SessionStart | yes | Fired once per session, in each of the three sessions. The harness recorded the output as `{"additionalContext": ...}` of 5,780 characters, and the model's first reply listed the day file, `now.md`, `recent.md`, `archive.md`, the last handoff and the history note, so the recap reached the model. Hook log: `session-start took 2s`. |
| UserPromptSubmit | yes | Fired on every turn and exited successfully. Its output does not reach the model on this host (see Known gaps). |
| PostToolUse | yes | Fired once per tool call in all three sessions (1, 1 and 2 calls). Log line: `post-tool: copilot transcript .../.copilot/session-state/<uuid>/events.jsonl`. |
| SessionEnd | yes | Fired after every turn, not once per session, with `reason=complete` in every firing observed (four, across three sessions) (see [Saves per turn](#saves-per-turn-two-behaviours)). Each firing ran the forced save: `extract`, then the summarizer (`provider: claude`), then a position update. |

End to end (**observed**):

- **Recap injected.** In session 1 the model could name the memory sections from the SessionStart context.
- **`scripts/doctor.sh`**, run against the project after session 1 (its timestamp precedes session 3's write): `VERDICT: capture is working -- last save 2026-09-30 21:13:09`.
- **Two sessions were SKIPped by the summarizer.** Session 1 (a question about the memory sections, then a command printing `hello`) and session 2 (one read-only turn that read the README and summarised it) were each extracted, and the position advanced after each SKIP (observed in the log for both sessions; session 2's log shows `position -> 25`), but the summarizer returned `SKIP` and nothing was written to `now.md`. Session 2's extract was inspected by re-running the extractor on its transcript: three exchanges (the prompt, the tool-only assistant turn rendered as a `[TOOL: ...]` line, and the answer) with the right roles, matching the conversation. Session 1's extract content was not inspected. The save prompt tells the model to return `SKIP` for a span with no substantive work, and it treated these that way (**observed** that it did; the reading that this is the prompt's intended judgement and not a capture failure is **reasoned**, from the prompt text and from session 3 writing normally).
- **Save written to the shared timeline.** In session 3, one turn that created a small file in the project: `extract` found 4 exchanges (1 human), the summarizer returned an entry (`[write] appended (provider: claude): ## 21:21 | <branch>`), the position advanced (`[write] position -> 34`), and the regular compression step moved the entry from `now.md` into the day file (`now.md -> today-2026-09-30.md`, `752->360b`). The day file holds a one-line entry naming the file that session created. This is the first observation of a VS Code Agents session being summarised into the same `.remember/` timeline Claude Code uses.
- **Hook durations held the turn (before the launcher fix).** **Observed** on 2026-10-03 (VS Code 1.140.0, Windows 11, Git Bash 5.2.37, Windows PowerShell 5.1; the branch at d54685c through the current `powershell -File` entry), from the harness's own `hook.start` / `hook.end` records in `events.jsonl`: each hook took as long as the work it had put in the background, so the model's turn waited for the whole save and the UI showed the next prompt as queued until then.

  | Hook | What it puts in the background | Observed duration |
  | --- | --- | --- |
  | SessionStart, daily consolidation due | consolidation (a summarizer call, ~35 s) | 40.0 s |
  | SessionStart, nothing due | -- | 2.6-2.7 s |
  | SessionEnd, default | forced save: extract + summarizer (~5 s) | 7.4-8.5 s |
  | SessionEnd, after a session that wrote a file | forced save + compression (~45 s) | 55.1 s |
  | SessionEnd, `turn_end_debounce_seconds=30` | 30 s sleep + forced save | 38-39 s (3 sessions) |
  | PostToolUse | background save | 1.1-1.7 s |

  The cause is the inherited stdout handle described under [How hooks are launched on Windows](#how-hooks-are-launched-on-windows). **After the fix, observed in the test harness; not yet observed in VS Code:** the real `scripts/session-end-hook.sh`, with a stub save that sleeps 10 s, returned through the launcher in 0.7 s (`-File`) and 0.9 s (the full manifest form) instead of 11.8 s, and the save still completed about 11 s later.

Not observed: macOS, Linux, the standalone Copilot CLI, and any session shape beyond these three.

## Saves per turn: two behaviours

What this host does (**observed** in the three recorded sessions): it sends `SessionEnd` with `reason=complete` after every turn, and sends nothing when the session is closed. There is therefore no later end signal to save on. A turn that its own `SessionEnd` does not save is not saved by anything else: the next session has a new id, and start-up recovery reads only Claude Code's transcript directory (read from the code). The hook's forced save bypasses the cooldown and minimum-message gates, so by default the summarizer runs once per turn. Saves are incremental (**observed**: the position advances between firings and nothing was duplicated).

You can choose between two behaviours with `cooldowns.turn_end_debounce_seconds`:

- **A. Immediate (default, `0`).** Every turn is saved as soon as its `SessionEnd` arrives: one summarizer call per turn that has new content (the observed cost was roughly half a cent to seven tenths of a cent per call), and a loss window of only the few seconds the save itself takes. **Observed** in the live run described under Verification. The save runs in the background, so the turn should not wait for it; before the launcher fix VS Code held each turn for the whole save (7.4-8.5 s observed), and that the fix ends this is observed in the test harness only, not yet in VS Code.
- **B. Debounced (`N` > 0).** Each turn's save waits `N` seconds in the background (the wait is not meant to hold the turn; before the launcher fix it did, 38-39 s observed with `N`=30), then runs only if no later turn of the same session has ended in the meantime. A burst of turns is saved once, `N` seconds after its last turn: one summarizer call per burst instead of one per turn, and one summary covering the whole burst rather than several fragments (**reasoned**, not tested: the tests stub the save). The cost is a loss window of `N` seconds: if the machine sleeps or VS Code is killed inside it, that burst can go unsaved, and only a later turn in the same session would save it. The daily log records `session-end: turn-end save deferred Ns (cooldowns.turn_end_debounce_seconds)` when a save is scheduled and `session-end: turn-end save superseded by a later turn` when one stands down. **Tested, not yet observed live**: `tests/test_session_end_turn_debounce_vscode.py` drives the real hook under Git Bash on Windows; macOS/Linux is reasoned.

To turn B on, put this in `~/.remember/config.json` (all projects) or `<project>/.remember/config.json` (one project); 30 is an example:

```json
{
  "cooldowns": {
    "turn_end_debounce_seconds": 30
  }
}
```

Other hosts are unaffected: the debounce applies only under the `copilot` host hint (VS Code Agents; the standalone Copilot CLI too, if it sends `complete` — unverified) and only when the reason is `complete`. Claude Code, Codex and the rest, and any other `SessionEnd` reason, keep the immediate save whatever the key says. A value that is not a plain non-negative integer is read as `0`. See also [configuration.md](configuration.md).

## Known gaps

- **SessionEnd fires after every turn on this host, and nothing fires on close**; see [Saves per turn](#saves-per-turn-two-behaviours) for the two behaviours this allows.
- The harness reports the merged SessionStart result as failed (`success=false`) when any other installed plugin's SessionStart hook fails (**observed**: another plugin's hook failed under PowerShell in every session); this plugin's context was still injected.
- UserPromptSubmit output does not reach the model on this host (**observed**: neither a `hookSpecificOutput` nor a top-level `additionalContext` from the plugin's UserPromptSubmit hook was injected). Whatever `scripts/user-prompt-hook.sh` prints is therefore not seen by the model here.
- Promos (`systemMessage`) are not emitted on this host; the emit branch skips them on purpose, as `systemMessage` was not probed (**reasoned** from the code; no test or live run shows it).
- Each hook firing starts one extra Windows PowerShell process (the child that carries `-ExecutionPolicy Bypass`) on top of the shell VS Code starts: a per-hook startup cost. In the test harness on one machine, a trivial hook through the launcher alone took about 0.5 s (0.36 s before the handle step was added; the step adds about 0.15-0.25 s end to end), and the full manifest form about 0.2 s more (**observed** there, not measured in VS Code).
- An execution policy enforced by Group Policy (`MachinePolicy` / `UserPolicy` scope) overrides `-ExecutionPolicy Bypass`, so under such a policy the launcher cannot run (**not observed**).
- The current `powershell` entry form was first run live on 2026-10-03, of which this document records the hook durations; the three recorded sessions used the earlier `&` form (see Verification). The launcher fix for those durations is observed in the test harness only, not yet in VS Code.
- A VS Code started from inside a Claude Code shell inherits `CLAUDE_CODE_*`; the host hint then resolves to Claude Code and the recap is emitted as plain text, which VS Code does not inject (**reasoned**, not observed).
- macOS/Linux VS Code and the standalone Copilot CLI: unverified.
