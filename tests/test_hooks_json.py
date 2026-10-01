"""Lint hooks/hooks.json for cross-shell dispatch safety.

Catches historical regressions:
  - 4d50166: unquoted ${CLAUDE_PLUGIN_ROOT} broke paths with spaces.
  - d18e02c: leftover `2>>` stderr redirects in hook command strings.
  - #82:     unquoted/unwrapped ${VAR} causes PowerShell ParserError on Windows.

Three layers:
  1. Structural — JSON shape, command non-empty, referenced script files exist.
  2. Static lint — regex checks for known foot-guns.
  3. Live parse — bash -n and pwsh -Command dry-parse; skip if shell missing.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ._bash_runner import find_git_bash

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_JSON = REPO_ROOT / "hooks" / "hooks.json"
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Extracted to _bash_runner.py (#432) so test_hook_cwd_leak_417.py and
# test_transcript_path_leak_424.py can share it instead of skipping on the
# whole Windows platform. Re-imported under the original private name so
# nothing else in this file has to change.
_find_git_bash = find_git_bash


def _iter_commands():
    """Yield (event, index, command_string) for every hook entry."""
    data = json.loads(HOOKS_JSON.read_text())
    for event, groups in data.get("hooks", {}).items():
        for gi, group in enumerate(groups):
            for hi, hook in enumerate(group.get("hooks", [])):
                assert hook.get("type") == "command", (
                    f"{event}[{gi}].hooks[{hi}]: unsupported type {hook.get('type')!r}"
                )
                cmd = hook.get("command", "")
                assert isinstance(cmd, str) and cmd.strip(), (
                    f"{event}[{gi}].hooks[{hi}]: empty/missing command"
                )
                yield f"{event}[{gi}].hooks[{hi}]", cmd


def test_hooks_json_is_valid_json():
    json.loads(HOOKS_JSON.read_text())


def test_every_referenced_script_exists():
    """${CLAUDE_PLUGIN_ROOT}/scripts/foo.sh must resolve to a real file."""
    pat = re.compile(r"\$\{?CLAUDE_PLUGIN_ROOT\}?/(scripts/[A-Za-z0-9_./-]+\.sh)")
    found_any = False
    for loc, cmd in _iter_commands():
        for rel in pat.findall(cmd):
            found_any = True
            path = REPO_ROOT / rel
            assert path.is_file(), f"{loc}: references missing script {rel}"
    assert found_any, "no script references found — regex drift?"


def test_every_shipped_hook_script_is_wired():
    """Every scripts/*-hook.sh must be registered in hooks.json.

    Regression guard for the 0.8.x manifest gap: user-prompt-hook.sh shipped
    (and the README documented it) but hooks.json never wired it, so the
    UserPromptSubmit timestamp injection and its hooks.d/ dispatch were dead
    code for plugin installs.
    """
    all_commands = "\n".join(cmd for _loc, cmd in _iter_commands())
    for script in sorted(SCRIPTS_DIR.glob("*-hook.sh")):
        if script.name.startswith("agy-"):
            # Antigravity CLI (`agy`, #563) has no per-plugin manifest at
            # all -- its own hooks.json lives at a shared, per-machine path
            # (~/.gemini/config/hooks.json) with no working variable
            # substitution, so there is no static file this repo could
            # check in for hooks/hooks.json's OWN wiring check to find (see
            # scripts/install_agy_hooks.py's own module docstring). These
            # scripts have their own wiring guard instead:
            # tests/test_install_agy_hooks_563.py's
            # test_build_entry_references_scripts_that_exist and
            # test_build_entry_only_names_confirmed_events pin that the
            # installer's generated manifest references every one of them.
            continue
        assert script.name in all_commands, (
            f"scripts/{script.name} ships with the plugin but no hooks.json "
            f"command references it"
        )


def test_plugin_root_var_is_double_quoted():
    """${CLAUDE_PLUGIN_ROOT} must sit inside double quotes (spaces in install path).

    Regression guard for 4d50166.
    """
    for loc, cmd in _iter_commands():
        for m in re.finditer(r"\$\{?CLAUDE_PLUGIN_ROOT\}?", cmd):
            before = cmd[: m.start()]
            after = cmd[m.end() :]
            opening = before.rfind('"')
            closing = after.find('"')
            assert opening != -1 and closing != -1, (
                f"{loc}: ${{CLAUDE_PLUGIN_ROOT}} not inside double quotes — "
                f"breaks on install paths with spaces"
            )


def test_no_stderr_redirects_in_command():
    """Hook commands must not contain `2>` / `2>>` — let Claude Code capture stderr.

    Regression guard for d18e02c.
    """
    for loc, cmd in _iter_commands():
        assert "2>>" not in cmd and "2>" not in cmd, (
            f"{loc}: contains stderr redirect — remove, Claude Code captures it"
        )


def test_no_bare_dollar_braces_outside_known_vars():
    """Flag ${VAR} patterns other than ${CLAUDE_PLUGIN_ROOT}.

    PowerShell parses ${...} as its own subexpression and chokes on most contents
    (issue #82). Only the known-safe variable is allowed. Anything else must be
    wrapped via `bash -c '...'` so PowerShell sees an opaque single-quoted string.
    """
    allowed = {"CLAUDE_PLUGIN_ROOT"}
    pat = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
    for loc, cmd in _iter_commands():
        # If command is wrapped in `bash -c '...'`, PowerShell sees opaque body — skip.
        if re.search(r"\bbash\s+-c\s+'", cmd):
            continue
        for var in pat.findall(cmd):
            assert var in allowed, (
                f"{loc}: ${{{var}}} risks PowerShell ParserError. "
                f"Either wrap command in `bash -c '...'` or use bare $VAR."
            )


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="Windows `bash` resolves to WSL launcher, not Git Bash — "
    "claude-code on Windows dispatches via pwsh anyway; "
    "bash-side coverage comes from ubuntu/macos legs.",
)
def test_commands_parse_under_bash():
    """`bash -n` dry-parses each command with a stubbed CLAUDE_PLUGIN_ROOT.

    Pipes the command to bash via stdin — no temp file, no Windows path
    translation hazards (Git Bash chokes on `C:\\...` style paths), no
    list2cmdline quote mangling.
    """
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": "/tmp/stub plugin root"}
    for loc, cmd in _iter_commands():
        result = subprocess.run(
            ["bash", "-n", "/dev/stdin"],
            input=cmd + "\n",
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, (
            f"{loc}: bash syntax error\ncmd: {cmd}\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )


GIT_BASH = _find_git_bash()


# Hard error signatures that mean the script (or one of its sourced files)
# broke under Git Bash — the #82/#84 family: CRLF line endings turn into a
# stray \r ("$'\r': command not found"), MSYS path mangling, bad test
# comparisons, or a propagated Python traceback.
_GIT_BASH_ERROR_SIGNATURES = (
    "command not found",
    "syntax error",
    "$'\\r'",
    "\\r': ",
    "unexpected end of file",
    "integer expression expected",
    "Traceback (most recent call last)",
    "No such file or directory",
)


@pytest.mark.skipif(GIT_BASH is None, reason="Git Bash not found (non-Windows or not installed)")
def test_session_start_hook_runs_under_git_bash(tmp_path):
    """Execute session-start-hook.sh for real under Git Bash on Windows (#82).

    Parse-only checks can't see #82: `bash "${VAR}/x.sh"` is always valid syntax.
    The real failure is at *execution* — CRLF-poisoned scripts, MSYS path
    translation, bad `[ ]` comparisons — exactly the #84 family the Git-Bash
    reporter hits. So we run the hook in a sandbox (HOME + CLAUDE_PROJECT_DIR
    point at a tmp dir; CLAUDE_PLUGIN_ROOT at the real repo so scripts resolve)
    and assert it exits 0 with no hard error signature on stderr.

    Fires on the existing windows-latest CI leg — Git ships preinstalled.
    """
    assert GIT_BASH is not None  # guaranteed by skipif; narrows type for the checker
    repo = str(REPO_ROOT).replace("\\", "/")
    script = f"{repo}/scripts/session-start-hook.sh"
    env = {
        **os.environ,
        "CLAUDE_PLUGIN_ROOT": repo,
        "CLAUDE_PROJECT_DIR": str(tmp_path).replace("\\", "/"),
        "HOME": str(tmp_path).replace("\\", "/"),
    }
    result = subprocess.run(
        [GIT_BASH, script],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    offenders = [s for s in _GIT_BASH_ERROR_SIGNATURES if s in result.stderr]
    assert result.returncode == 0 and not offenders, (
        f"session-start-hook.sh broke under Git Bash "
        f"(exit {result.returncode}, signatures {offenders})\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )


@pytest.mark.skipif(GIT_BASH is None, reason="Git Bash not found (non-Windows or not installed)")
def test_session_start_hook_survives_crlf_checkout(tmp_path):
    """Run the hook with CRLF-converted scripts under Git Bash (#82 repro).

    GitHub's checkout gives LF, so the plain execution test can't see the
    reporter's failure. Windows users with `core.autocrlf=true` get the
    scripts with CRLF line endings — each line then carries a trailing \\r that
    bash reads as a literal character ("$'\\r': command not found", broken
    `[ ]` comparisons). This is the same family as the #84 save-pipeline bug.

    We copy the repo to a sandbox, rewrite every *.sh to CRLF, and run the hook
    from there. The assertion is the permanent guarantee — the hook must survive
    a CRLF checkout. Until the scripts are made CRLF-safe this test reproduces
    #82 (red); once fixed it stays green.
    """
    assert GIT_BASH is not None  # guaranteed by skipif; narrows type for the checker
    plugin = tmp_path / "plugin"
    shutil.copytree(
        REPO_ROOT,
        plugin,
        ignore=shutil.ignore_patterns(".git", "tests", ".remember", "__pycache__", "*.pyc"),
    )
    for sh in plugin.rglob("*.sh"):
        lf = sh.read_bytes().replace(b"\r\n", b"\n")  # normalise first
        sh.write_bytes(lf.replace(b"\n", b"\r\n"))    # then force CRLF

    home = tmp_path / "home"
    home.mkdir()
    plugin_root = str(plugin).replace("\\", "/")
    env = {
        **os.environ,
        "CLAUDE_PLUGIN_ROOT": plugin_root,
        "CLAUDE_PROJECT_DIR": str(home).replace("\\", "/"),
        "HOME": str(home).replace("\\", "/"),
    }
    result = subprocess.run(
        [GIT_BASH, f"{plugin_root}/scripts/session-start-hook.sh"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    offenders = [s for s in _GIT_BASH_ERROR_SIGNATURES if s in result.stderr]
    assert result.returncode == 0 and not offenders, (
        f"session-start-hook.sh broke on a CRLF checkout under Git Bash "
        f"(exit {result.returncode}, signatures {offenders}) — reproduces #82\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )


PROBLEMATIC_PATHS = [
    # ── baseline ────────────────────────────────────────────────────
    "C:/Users/dev/plugin",
    # ── spaces (historical bug 4d50166) ─────────────────────────────
    "C:/Program Files/My Plugin",
    "C:/Users/Jane Doe/.claude/plugins/cache/org/remember/0.7.2",
    # ── trailing space (Windows quietly strips on save) ─────────────
    "C:/Users/dev /plugin",
    # ── non-ASCII usernames (Latin1 + extended) ─────────────────────
    "C:/Users/Émilie/plugin",
    "C:/Users/café/plugin",
    "C:/Users/Łukasz/plugin",
    # ── non-ASCII beyond Latin1 (combining marks, CJK, RTL, emoji) ──
    "C:/Users/Việt/plugin",
    "C:/Users/中文/plugin",
    "C:/Users/مرحبا/plugin",
    "C:/Users/dev🎉/plugin",
    # combining mark: e + U+0301 (not precomposed é)
    "C:/Users/café/plugin",
    # ── PowerShell-hostile chars ────────────────────────────────────
    "C:/Users/Jane's OneDrive/plugin",          # apostrophe (OneDrive)
    "C:/Users/dev/with`backtick/plugin",        # PS escape char
    "C:/Users/dev/with$dollar/plugin",          # PS var sigil
    "C:/Users/dev/with[brackets]/plugin",       # PS wildcard / index
    "C:/Users/dev/with(parens)/plugin",         # subexpression-ish
    "C:/Users/dev/with{braces}/plugin",         # scriptblock / var-name braces
    "C:/Users/dev/with;semi/plugin",            # statement separator
    "C:/Users/dev/with#hash/plugin",            # PS comment char
    "C:/Users/dev/with@at/plugin",              # here-string / splat
    # ── cmd.exe-passthrough flavour ─────────────────────────────────
    "C:/Users/dev/with%percent%/plugin",        # cmd var expansion
    "C:/Users/dev/with&amp/plugin",             # cmd command separator
    # ── slash mixing & oddities ─────────────────────────────────────
    "C:\\Users\\dev\\plugin",                    # all-backslash
    "C:\\Users/dev\\mixed/plugin",               # mixed
    "C:/PROGRA~1/plugin",                        # 8.3 short name
    # ── UNC paths ───────────────────────────────────────────────────
    "//server/share/plugin",
    "\\\\server\\share\\plugin",
    # ── MSYS / Git-Bash drive forms (#82: reporter launches from Git Bash) ──
    # When Claude Code is launched from Git Bash, CLAUDE_PLUGIN_ROOT can arrive
    # as an MSYS-style POSIX path rather than C:\... — the shape never fuzzed
    # before. Leading "/c" reads as a path under pwsh, but the space/funky-char
    # variants below still exercise the same parser hazards.
    "/c/Users/dev/plugin",
    "/c/Program Files/My Plugin",
    "/c/Users/Jane Doe/.claude/plugins/cache/org/remember/0.7.2",
    "/c/Users/Jane's OneDrive/plugin",
    "/c/Users/Émilie/plugin",
    "/cygdrive/c/Users/dev/plugin",
    "/mnt/c/Users/dev/plugin",
    # ── mixed nightmare ─────────────────────────────────────────────
    "C:/Users/Émilie's Files/Café (work)/plugin",
    "C:/Users/中文 dev's/with `backtick` & $var [v2]/plugin",
]


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh not on PATH")
@pytest.mark.parametrize("plugin_root", PROBLEMATIC_PATHS, ids=lambda p: p[:32])
def test_commands_parse_after_substitution(plugin_root):
    """Substitute ${CLAUDE_PLUGIN_ROOT} ourselves, then parse under PowerShell.

    Claude Code on Windows may expand the env variable before handing the
    command string to pwsh. Paths containing spaces / non-ASCII / apostrophes /
    backticks / dollar signs can then break the parser even though the raw
    template was fine. This guards every install-path shape the reporter or
    future users might have.
    """
    parser_probe = (
        "$src = [Console]::In.ReadToEnd(); "
        "$errors = $null; "
        "$null = [System.Management.Automation.Language.Parser]::ParseInput("
        "$src, [ref]$null, [ref]$errors); "
        "if ($errors) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
    )
    for loc, cmd in _iter_commands():
        substituted = re.sub(r"\$\{?CLAUDE_PLUGIN_ROOT\}?", lambda _m: plugin_root, cmd)
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command", parser_probe],
            input=substituted + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, (
            f"{loc}: PowerShell ParserError after substituting "
            f"CLAUDE_PLUGIN_ROOT={plugin_root!r}\n"
            f"substituted cmd: {substituted}\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh not on PATH")
def test_commands_parse_under_powershell():
    """PowerShell dry-parses each command with a stubbed CLAUDE_PLUGIN_ROOT.

    Direct guard for #82 — the Windows ParserError surfaces here on any OS that
    has pwsh installed. GitHub-hosted runners ship pwsh on all three matrix legs.

    Pipes the command source via stdin and parses it through
    System.Management.Automation.Language.Parser — bypasses CLI arg encoding.
    """
    parser_probe = (
        "$env:CLAUDE_PLUGIN_ROOT = '/tmp/stub plugin root'; "
        "$src = [Console]::In.ReadToEnd(); "
        "$errors = $null; "
        "$null = [System.Management.Automation.Language.Parser]::ParseInput("
        "$src, [ref]$null, [ref]$errors); "
        "if ($errors) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
    )
    for loc, cmd in _iter_commands():
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command", parser_probe],
            input=cmd + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, (
            f"{loc}: PowerShell ParserError\ncmd: {cmd}\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Windows PowerShell launcher (issue: vscode)
#
# VS Code Agents (Copilot harness) on Windows runs a hook entry's `command`
# through Windows PowerShell, where a bare `bash` can resolve to the WSL
# launcher. A sibling `powershell` key on each entry is honoured there and
# points at scripts/run-hook.ps1, which finds Git Bash explicitly. Claude Code
# ignores the extra key and keeps using `command`. The functional tests below
# are OBSERVED on Windows only; that macOS/Linux ignore the `powershell` key is
# REASONED from the host's documented behaviour, not observed.
# ---------------------------------------------------------------------------

_HOOK_SCRIPT_NAMES = (
    "session-start-hook.sh",
    "user-prompt-hook.sh",
    "post-tool-hook.sh",
    "session-end-hook.sh",
)
RUN_HOOK_PS1 = SCRIPTS_DIR / "run-hook.ps1"


def _iter_entries():
    """Yield (loc, hook_entry_dict) for every hook entry."""
    data = json.loads(HOOKS_JSON.read_text())
    for event, groups in data.get("hooks", {}).items():
        for gi, group in enumerate(groups):
            for hi, hook in enumerate(group.get("hooks", [])):
                yield f"{event}[{gi}].hooks[{hi}]", hook


def _script_named_by(command: str) -> str:
    m = re.search(r"scripts/([A-Za-z0-9_.-]+\.sh)", command)
    assert m, f"no script in command {command!r}"
    return m.group(1)


def test_every_entry_has_a_powershell_launcher_key():
    entries = list(_iter_entries())
    assert len(entries) == len(_HOOK_SCRIPT_NAMES)
    for loc, hook in entries:
        name = _script_named_by(hook["command"])
        expected = f'& "$env:CLAUDE_PLUGIN_ROOT\\scripts\\run-hook.ps1" {name}'
        assert hook["powershell"] == expected, f"{loc}: wrong powershell launcher"
    assert RUN_HOOK_PS1.is_file(), "scripts/run-hook.ps1 is missing"


def test_command_strings_are_unchanged_for_claude_code():
    """Positive control: the `command` Claude Code (and VS Code on macOS/Linux)
    runs is byte-for-byte what it was before the `powershell` key existed."""
    commands = [hook["command"] for _loc, hook in _iter_entries()]
    assert commands == [
        f'bash "${{CLAUDE_PLUGIN_ROOT}}/scripts/{name}"' for name in _HOOK_SCRIPT_NAMES
    ]


_PS_EXE = shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(_PS_EXE is None, reason="no PowerShell on PATH")
def test_powershell_values_parse_under_powershell():
    """Each `powershell` value dry-parses (same probe as the `command` check)."""
    parser_probe = (
        "$src = [Console]::In.ReadToEnd(); "
        "$errors = $null; "
        "$null = [System.Management.Automation.Language.Parser]::ParseInput("
        "$src, [ref]$null, [ref]$errors); "
        "if ($errors) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
    )
    seen = 0
    for loc, hook in _iter_entries():
        seen += 1
        result = subprocess.run(
            [_PS_EXE, "-NoProfile", "-NonInteractive", "-Command", parser_probe],
            input=hook["powershell"] + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, (
            f"{loc}: PowerShell ParserError\ncmd: {hook['powershell']}\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
    assert seen == len(_HOOK_SCRIPT_NAMES)


_WIN_POWERSHELL = shutil.which("powershell.exe") if sys.platform == "win32" else None
_needs_win_launcher = pytest.mark.skipif(
    sys.platform != "win32" or _WIN_POWERSHELL is None or GIT_BASH is None,
    reason="Windows PowerShell + Git Bash required (observed on Windows only)",
)
_PAYLOAD = '{"hook_event_name":"SessionStart","prompt":"ünï – 日本"}'


def _fake_plugin(tmp_path, stubs: dict[str, str]) -> Path:
    root = tmp_path / "plug in root"  # space on purpose
    (root / "scripts").mkdir(parents=True)
    shutil.copyfile(RUN_HOOK_PS1, root / "scripts" / "run-hook.ps1")
    for name, body in stubs.items():
        (root / "scripts" / name).write_bytes(("#!/usr/bin/env bash\n" + body).encode("utf-8"))
    return root


def _run_launcher(root: Path, ps_value: str, env_extra=None, path_first_system32=True):
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(root).replace("/", "\\")}
    if path_first_system32:
        sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        rest = [p for p in env["PATH"].split(os.pathsep) if p.lower() != sys32.lower()]
        env["PATH"] = os.pathsep.join([sys32, *rest])
    for key, value in (env_extra or {}).items():
        # Windows env names are case-insensitive but os.environ upper-cases them:
        # drop the existing spelling so the override is the only one the child sees.
        for existing in [k for k in env if k.lower() == key.lower()]:
            del env[existing]
        env[key] = value
    return subprocess.run(
        [_WIN_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", ps_value],
        input=_PAYLOAD.encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=120,
    )


@_needs_win_launcher
def test_powershell_launcher_reaches_script_with_backslash_plugin_root(tmp_path):
    """Each entry's `powershell` value runs its script under Git Bash even with
    System32 (WSL bash) first on PATH, a backslash plugin root containing a
    space, and a non-ASCII UTF-8 payload on stdin. Observed on Windows only."""
    marker = tmp_path / "marker.txt"
    stub = 'echo "$(basename "$0")" >> "$REMEMBER_TEST_MARKER"\ncat\n'
    root = _fake_plugin(tmp_path, {name: stub for name in _HOOK_SCRIPT_NAMES})
    for loc, hook in _iter_entries():
        result = _run_launcher(
            root, hook["powershell"], {"REMEMBER_TEST_MARKER": str(marker).replace("\\", "/")}
        )
        assert result.returncode == 0, f"{loc}: {result.stderr!r}"
        assert "ünï – 日本" in result.stdout.decode("utf-8"), f"{loc}: payload mangled"
    ran = marker.read_text(encoding="utf-8").split()
    assert sorted(ran) == sorted(_HOOK_SCRIPT_NAMES)


@_needs_win_launcher
def test_powershell_launcher_forwards_bash_exit_code(tmp_path):
    """The launcher returns bash's exit code (`-File` form). Also the positive
    control for the returncode == 0 assertions above: a failing stub is visible.

    Observed: under `powershell -Command "& script.ps1"` (the form the manifest
    uses) Windows PowerShell 5.1 collapses any non-zero script exit to 1, so
    the exact code only survives `-File`; non-zero still stays non-zero."""
    root = _fake_plugin(tmp_path, {"exit3.sh": "cat >/dev/null\nexit 3\n"})
    launcher = str(root / "scripts" / "run-hook.ps1")
    direct = subprocess.run(
        [_WIN_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", launcher, "exit3.sh"],
        input=b"{}", capture_output=True, timeout=120,
    )
    assert direct.returncode == 3, direct.stderr
    via_command = _run_launcher(root, r'& "$env:CLAUDE_PLUGIN_ROOT\scripts\run-hook.ps1" exit3.sh')
    assert via_command.returncode != 0, via_command.stderr
