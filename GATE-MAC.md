# Gate: macOS probe run (operator procedure)

Same probe, same tokens, as the Windows gate (read its preamble on positive
controls; it applies here unchanged). Nothing here touches a git checkout or
the NEVER-TOUCH path (`~/.copilot/installed-plugins/_direct/Digital-Process-Tools--claude-remember--remember`;
on this Mac it is expected to be absent, which `sentinel.sh` reports as such).
If the sentinel's HEAD or worktree lines change between two runs, or NT
appears/disappears: **stop and tell the human partner; repair nothing.**

| token | printed by |
| --- | --- |
| `TOKEN_CLAUDEFMT_7326` | `.claude-plugin/plugin.json` -> `hooks/hooks.json` (Claude format) |
| `TOKEN_COPILOT_ROOT_8253` | root `plugin.json` -> `hooks/hooks.copilot-root.json` |
| `TOKEN_COPILOT_GH_6977` | `.github/plugin/plugin.json` -> `hooks/hooks.copilot-gh.json` |

On macOS the interesting extra is Q7: which handler key the hosts run from the
Copilot hooks files (`handler_key` = `bash` or `command`), and `bash_running`
(expected `/bin/bash`, 3.2.57).

## 0. Once, before any host

Get the kit onto the Mac. On Windows (Git Bash):

```bash
KIT=$HOME/remember-mac-run-2026-10-08/probe-kit
tar -czf "$(cygpath -u "$LOCALAPPDATA")/probe-kit.tgz" -C "$(dirname "$KIT")" probe-kit
```

Move `probe-kit.tgz` to the Mac by any means, then in a **plain Terminal**
(not one inside Claude Code, VS Code or the desktop app):

```bash
RUN=~/remember-mac-run-2026-10-08
mkdir -p "$RUN" && tar -xzf ~/Downloads/probe-kit.tgz -C "$RUN"     # adjust the .tgz path
KIT=$HOME/remember-mac-run-2026-10-08/probe-kit
BASE="$RUN/probe"
mkdir -p "$BASE/proj" "$BASE/sentinel" "$BASE/logs"
bash "$KIT/selftest.sh"                       # expect: selftest: 0 failure(s), 38 calls (no PowerShell legs on macOS)
bash "$KIT/sentinel.sh" --save "$BASE/sentinel"
copilot plugin list                           # read-only; note it; no remember-probe should be listed
mvlog() { mv "$1" "$BASE/logs/$2.jsonl"; [ -f "$1.err" ] && mv "$1.err" "$BASE/logs/$2.err"; echo "filed $BASE/logs/$2.jsonl"; }
```

The selftest is the first run of the recorder under macOS's bash 3.2: if it
fails, stop and paste its output (the probe would not record either).

Paths: stage dir `~/remember-mac-run-2026-10-08/probe/stage-<variant>`; log
`~/remember-mac-run-2026-10-08/probe/probe-log.jsonl`; project
`~/remember-mac-run-2026-10-08/probe/proj`. Prompt everywhere:

```
Reply with exactly the token you were given at session start, nothing else
```

**Order.** Variant `all` first in every host. Only if a host loads the Claude
manifest (reply `TOKEN_CLAUDEFMT_7326` and/or only `claudefmt` rows) try
`gh-only`, then `root-only`. Optional (Q2): run the other single-manifest
variant than the one `all` picked. Optional (Q3/Q4): the winner again with
`--camel`. A fresh stage dir per variant.

## A. Copilot CLI

```bash
bash "$KIT/sentinel.sh" --save "$BASE/sentinel"
bash "$KIT/stage.sh" all "$BASE/stage-all"
cd "$BASE/proj"
COPILOT_PLUGIN_DIR_ONLY=1 copilot --plugin-dir "$BASE/stage-all"
```

In the CLI: trust the folder if asked; copy startup warnings about the plugin
dir or manifests; `/env` and copy its hooks section; send the prompt and copy
the reply; `/exit`. (Non-interactive alternative: add
`-p "Reply with exactly the token you were given at session start, nothing else"`.)
If the probe does not load at all, retry once without `COPILOT_PLUGIN_DIR_ONLY=1`
and say so.

```bash
bash "$KIT/sentinel.sh" --save "$BASE/sentinel"
bash "$KIT/collect.sh" "$BASE/probe-log.jsonl" --raw
mvlog "$BASE/probe-log.jsonl" cli-all
rm -rf "$BASE/stage-all"
```

Paste back: reply, warnings, `/env` hooks section, `collect.sh` output, sentinel outputs.

## B. VS Code

```bash
bash "$KIT/sentinel.sh" --save "$BASE/sentinel"
bash "$KIT/stage.sh" all "$BASE/stage-all"
echo "$BASE/stage-all"                        # absolute path for settings.json
```

1. **Preferences: Open User Settings (JSON)**; add
   `"chat.pluginLocations": { "/Users/<you>/remember-mac-run-2026-10-08/probe/stage-all": true }`
   (inside an existing `chat.pluginLocations` if there is one; set any other
   claude-remember path there to `false` for the run and note its old value).
2. **File: Open Folder** `~/remember-mac-run-2026-10-08/probe/proj`.
3. **Developer: Reload Window**, then a **New Chat** (the sync happens on the first chat).
4. Send the prompt; copy the reply; close the chat.
5. Optional: **Developer: Open Logs Folder** -> `agenthost.log`, lines with `remember-probe`.

```bash
bash "$KIT/sentinel.sh" --save "$BASE/sentinel"
bash "$KIT/collect.sh" "$BASE/probe-log.jsonl" --raw
mvlog "$BASE/probe-log.jsonl" vscode-all
```

Removal: delete the `chat.pluginLocations` entry (restore anything changed),
**Developer: Reload Window**, `rm -rf "$BASE/stage-all"`. The mirror under
`~/Library/Application Support/Code/agentPlugins/` may stay. A registered path
is not re-mirrored: new dir name per variant.

## C. Copilot desktop app

```bash
bash "$KIT/sentinel.sh" --save "$BASE/sentinel"
bash "$KIT/stage.sh" all "$BASE/mkt-all" --marketplace
copilot plugin list
copilot plugin marketplace add "$BASE/mkt-all"
copilot plugin install remember-probe@remember-probe-local
bash "$KIT/sentinel.sh" --save "$BASE/sentinel"
```

Restart the desktop app if it was open; new chat **with a Project**
(`~/remember-mac-run-2026-10-08/probe/proj`); send the prompt; copy the reply;
close the chat. No CLI sessions meanwhile.

```bash
bash "$KIT/sentinel.sh" --save "$BASE/sentinel"
bash "$KIT/collect.sh" "$BASE/mkt-all/probe-log.jsonl" --raw
mvlog "$BASE/mkt-all/probe-log.jsonl" desktop-all
copilot plugin uninstall remember-probe@remember-probe-local
copilot plugin marketplace remove remember-probe-local
bash "$KIT/sentinel.sh" --save "$BASE/sentinel"
copilot plugin list
rm -rf "$BASE/mkt-all"
```

`~/.copilot/settings.json` may keep an inert `"remember-probe@remember-probe-local": false`;
if that file did not exist before step C, it was created by the install (seen
before); ask the human partner before deleting it.

## D. Claude Code (Q8)

```bash
bash "$KIT/sentinel.sh" --save "$BASE/sentinel"
bash "$KIT/stage.sh" all "$BASE/stage-all-claude"
cd "$BASE/proj"
claude --plugin-dir "$BASE/stage-all-claude" -p "Reply with exactly the token you were given at session start, nothing else"
bash "$KIT/sentinel.sh" --save "$BASE/sentinel"
bash "$KIT/collect.sh" "$BASE/probe-log.jsonl" --raw
mvlog "$BASE/probe-log.jsonl" claude-all
rm -rf "$BASE/stage-all-claude"
# positive control for the Claude path's injection:
bash "$KIT/stage.sh" all "$BASE/stage-all-claude-hso" --claudefmt-out hso
claude --plugin-dir "$BASE/stage-all-claude-hso" -p "Reply with exactly the token you were given at session start, nothing else"
bash "$KIT/collect.sh" "$BASE/probe-log.jsonl"; mvlog "$BASE/probe-log.jsonl" claude-all-hso; rm -rf "$BASE/stage-all-claude-hso"
```

Expected: only `claudefmt` rows, `handler_key=command`. The first reply may
carry no token (top-level envelope); the `hso` run should. Claude Code's own
installed `remember` plugin may write `.remember/` into the scratch `proj`.

## Finish

```bash
bash "$KIT/sentinel.sh" --save "$BASE/sentinel"
for f in "$BASE"/logs/*.jsonl; do echo "== $f"; bash "$KIT/collect.sh" "$f"; done
```

Keep `$BASE/logs` until written up; then `rm -rf "$BASE" "$KIT"` (and
`~/Downloads/probe-kit.tgz`). Paste back everything listed per host, then
**answer only after it finishes**.
