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
import time
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


# The exact `powershell` value: a child Windows PowerShell with its own
# `-ExecutionPolicy Bypass`, so a Restricted/RemoteSigned user policy on the
# shell VS Code starts cannot block the launcher, and `exit $LASTEXITCODE`
# so bash's exact status survives (`-Command "& x.ps1"` collapses it to 1).
# `-NoLogo -NonInteractive` on the child: no logo on any path that honours it,
# and a launcher run without its mandatory script name errors instead of
# prompting on stdin. NOT a fix for an unset or stale root: there `-File`
# fails before any script runs, and Windows PowerShell 5.1 prints its banner
# to stdout even with -NoLogo (observed) -- see docs/install-vscode.md.
_PS_VALUE = ('powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File '
             '"$env:CLAUDE_PLUGIN_ROOT\\scripts\\run-hook.ps1" {name}; exit $LASTEXITCODE')


def test_every_entry_has_a_powershell_launcher_key():
    entries = list(_iter_entries())
    assert len(entries) == len(_HOOK_SCRIPT_NAMES)
    for loc, hook in entries:
        name = _script_named_by(hook["command"])
        expected = _PS_VALUE.format(name=name)
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


# Launcher inputs the developer's own shell may carry (this suite runs inside
# a Claude Code session): every launcher run starts without them and only gets
# back what a case names.
_LAUNCHER_AMBIENT = ("CLAUDE_PLUGIN_ROOT", "COPILOT_PLUGIN_ROOT", "REMEMBER_BASH")


def _launcher_env(root: Path | None, env_extra=None, path_first_system32=True,
                  drop=()):
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in _LAUNCHER_AMBIENT + tuple(d.upper() for d in drop)}
    if root is not None:
        env["CLAUDE_PLUGIN_ROOT"] = str(root).replace("/", "\\")
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
    return env


def _run_launcher(root: Path, ps_value: str, env_extra=None, path_first_system32=True,
                  policy="Bypass"):
    return subprocess.run(
        [_WIN_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", policy,
         "-Command", ps_value],
        input=_PAYLOAD.encode("utf-8"),
        capture_output=True,
        env=_launcher_env(root, env_extra, path_first_system32),
        timeout=120,
    )


def _run_launcher_file(root: Path, script: str, env):
    """The launcher alone, via `-File` (what the manifest's child shell runs)."""
    return subprocess.run(
        [_WIN_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(root / "scripts" / "run-hook.ps1"), script],
        input=_PAYLOAD.encode("utf-8"), capture_output=True, env=env, timeout=120,
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
    """bash's exact exit code survives both the launcher alone (`-File`) and
    the manifest form (child `powershell -File ...; exit $LASTEXITCODE`). Also
    the positive control for the returncode == 0 assertions elsewhere: a
    failing stub is visible. Observed on Windows only."""
    root = _fake_plugin(tmp_path, {"exit3.sh": "cat >/dev/null\nexit 3\n"})
    direct = _run_launcher_file(root, "exit3.sh", _launcher_env(root))
    assert direct.returncode == 3, direct.stderr
    via_manifest = _run_launcher(root, _PS_VALUE.format(name="exit3.sh"))
    assert via_manifest.returncode == 3, via_manifest.stderr


@_needs_win_launcher
def test_manifest_values_run_under_restricted_execution_policy(tmp_path):
    """Windows' client default policy is `Restricted`, which refuses to load any
    .ps1. Each manifest value, run by a shell started with `-ExecutionPolicy
    Restricted`, must still reach its script and hand it the stdin payload
    byte-for-byte (non-ASCII included). Observed on Windows only, with the
    policy simulated on the outer shell's command line."""
    out_dir = tmp_path / "stdin-seen"
    out_dir.mkdir()
    stub = 'cat > "$REMEMBER_TEST_OUT/$(basename "$0").stdin"\n'
    root = _fake_plugin(tmp_path, {name: stub for name in _HOOK_SCRIPT_NAMES})
    for loc, hook in _iter_entries():
        result = _run_launcher(
            root, hook["powershell"],
            {"REMEMBER_TEST_OUT": str(out_dir).replace("\\", "/")}, policy="Restricted",
        )
        assert result.returncode == 0, f"{loc}: {result.stderr!r}"
        name = _script_named_by(hook["command"])
        seen = (out_dir / f"{name}.stdin").read_bytes()
        # Exact: bash inherits the launcher's stdin, so nothing is re-encoded
        # and no CRLF is appended (the payload has no trailing newline).
        assert seen == _PAYLOAD.encode("utf-8"), f"{loc}: {seen!r}"


@_needs_win_launcher
def test_restricted_policy_blocks_a_direct_script_call(tmp_path):
    """Positive control for the test above: the same simulated policy really
    does refuse a `& script.ps1` call, so passing above is not vacuous."""
    root = _fake_plugin(tmp_path, {"probe.sh": "cat >/dev/null\n"})
    result = _run_launcher(root, r'& "$env:CLAUDE_PLUGIN_ROOT\scripts\run-hook.ps1" probe.sh',
                           policy="Restricted")
    assert result.returncode != 0
    assert b"PSSecurityException" in result.stderr or b"disabled" in result.stderr


def _marker_stub():
    return 'echo ran >> "$REMEMBER_TEST_MARKER"\ncat >/dev/null\n'


@_needs_win_launcher
def test_launcher_with_empty_localappdata_still_exits_zero(tmp_path):
    """With LOCALAPPDATA unset, the launcher must not die on its own lookup:
    it reaches Git under ProgramFiles when it is installed there (this run's
    machine) and exits 0 either way. Observed on Windows only."""
    marker = tmp_path / "ran.txt"
    root = _fake_plugin(tmp_path, {"probe.sh": _marker_stub()})
    env = _launcher_env(root, {"REMEMBER_TEST_MARKER": str(marker).replace("\\", "/")},
                        drop=("LOCALAPPDATA",))
    result = _run_launcher_file(root, "probe.sh", env)
    assert result.returncode == 0, result.stderr
    git_under_pf = any(
        os.path.isfile(os.path.join(os.environ.get(v, ""), "Git", "bin", "bash.exe"))
        for v in ("ProgramFiles", "ProgramFiles(x86)") if os.environ.get(v))
    if git_under_pf:
        assert marker.is_file(), result.stderr


@_needs_win_launcher
def test_launcher_falls_through_a_missing_remember_bash(tmp_path):
    marker = tmp_path / "ran.txt"
    root = _fake_plugin(tmp_path, {"probe.sh": _marker_stub()})
    env = _launcher_env(root, {"REMEMBER_TEST_MARKER": str(marker).replace("\\", "/"),
                               "REMEMBER_BASH": str(tmp_path / "no-such" / "bash.exe")})
    result = _run_launcher_file(root, "probe.sh", env)
    assert result.returncode == 0, result.stderr
    assert marker.is_file(), result.stderr


@_needs_win_launcher
def test_launcher_error_exits_zero(tmp_path):
    """A terminating error inside the launcher (here: the bash it picked is not
    an executable) is reported on stderr and exits 0, never non-zero."""
    bogus = tmp_path / "bogus" / "bash.exe"
    bogus.parent.mkdir()
    bogus.write_text("not a program", encoding="utf-8")
    root = _fake_plugin(tmp_path, {"probe.sh": "cat >/dev/null\n"})
    result = _run_launcher_file(root, "probe.sh", _launcher_env(root, {"REMEMBER_BASH": str(bogus)}))
    assert result.returncode == 0, result.stderr
    assert b"claude-remember: launcher error" in result.stderr


# The hooks return at once and leave their work (a save, a consolidation) to a
# detached background child. The caller waits for the hook's stdout to reach
# EOF (observed here; reasoned for VS Code), so the launcher must not let that
# child inherit a handle to the caller's stdout or stderr pipe: Windows
# PowerShell 5.1 holds an extra inheritable duplicate of its own stdout handle,
# and every process it starts inherits every inheritable handle it holds
# (observed; docs/install-vscode.md). Margins are wide on purpose: a broken
# launcher is held for the whole 15 s background sleep, so the return bound sits
# 5 s below it (10 s) rather than near launcher start-up; and a 3 s foreground
# sleep is checked against a >= 3 s bound.
_BG_SLEEP_S = 15
_RETURN_BOUND_S = _BG_SLEEP_S - 5
_FG_SLEEP_S = 3
_BG_STDOUT = "stub stdout ünï – 日本"


def _bg_stub(background: bool) -> str:
    body = 'cat >/dev/null\nprintf "%s\\n" "' + _BG_STDOUT + '"\n'
    if background:
        body += (
            '( echo started >> "$REMEMBER_TEST_MARKER"; sleep ' + str(_BG_SLEEP_S)
            + '; echo finished >> "$REMEMBER_TEST_MARKER" ) </dev/null >/dev/null 2>&1 &\n'
            "disown 2>/dev/null || true\n"
        )
    return body + "exit 4\n"


def _timed(run):
    started = time.monotonic()
    result = run()
    return result, time.monotonic() - started


def _both_launcher_forms(root, script, env_extra):
    """(label, result, seconds) for the launcher alone (`-File`) and for the
    manifest form, each timed until the caller sees stdout and stderr close --
    which is what VS Code waits for."""
    env = _launcher_env(root, env_extra)
    direct, direct_s = _timed(lambda: _run_launcher_file(root, script, env))
    manifest, manifest_s = _timed(
        lambda: _run_launcher(root, _PS_VALUE.format(name=script), env_extra))
    return [("-File", direct, direct_s), ("manifest", manifest, manifest_s)]


@_needs_win_launcher
def test_launcher_does_not_wait_for_the_hooks_background_child(tmp_path):
    """A hook that backgrounds a 15 s child and exits must hand control back as
    soon as it exits, with its stdout and exit code intact -- and the child
    must keep running (the save it stands for still happens). Observed on
    Windows only; before the fix both forms took the full 15 s."""
    markers = [tmp_path / "bg-file.txt", tmp_path / "bg-manifest.txt"]
    root = _fake_plugin(tmp_path, {"bg.sh": _bg_stub(background=True)})
    env = _launcher_env(root, {"REMEMBER_TEST_MARKER": str(markers[0]).replace("\\", "/")})
    direct, direct_s = _timed(lambda: _run_launcher_file(root, "bg.sh", env))
    manifest, manifest_s = _timed(lambda: _run_launcher(
        root, _PS_VALUE.format(name="bg.sh"),
        {"REMEMBER_TEST_MARKER": str(markers[1]).replace("\\", "/")}))
    for label, result, took, marker in (("-File", direct, direct_s, markers[0]),
                                        ("manifest", manifest, manifest_s, markers[1])):
        assert took < _RETURN_BOUND_S, f"{label}: launcher held the caller for {took:.1f}s"
        assert result.returncode == 4, f"{label}: {result.stderr!r}"
        assert result.stdout.decode("utf-8").rstrip("\r\n") == _BG_STDOUT, f"{label}: {result.stdout!r}"
    # The background child was started, and outlives the launcher.
    deadline = time.monotonic() + _BG_SLEEP_S + 30
    while time.monotonic() < deadline and not all(
            m.is_file() and "finished" in m.read_text(encoding="utf-8") for m in markers):
        time.sleep(0.2)
    for marker in markers:
        assert marker.read_text(encoding="utf-8").split() == ["started", "finished"], marker


@_needs_win_launcher
def test_launcher_returns_fast_without_a_background_child(tmp_path):
    """Positive control for the bound above: the same stub with no background
    child also returns under it in both forms, so the bound measures the
    background child, not launcher start-up."""
    root = _fake_plugin(tmp_path, {"fg.sh": _bg_stub(background=False)})
    for label, result, took in _both_launcher_forms(root, "fg.sh", None):
        assert took < _RETURN_BOUND_S, f"{label}: {took:.1f}s"
        assert result.returncode == 4, f"{label}: {result.stderr!r}"
        assert result.stdout.decode("utf-8").rstrip("\r\n") == _BG_STDOUT, f"{label}: {result.stdout!r}"


@_needs_win_launcher
def test_launcher_still_waits_for_the_foreground_process(tmp_path):
    """Positive control for the "does not wait" case: a hook that sleeps in the
    FOREGROUND keeps the launcher for at least that long, in both forms, and
    its stdout still arrives -- the launcher returns when bash exits, not
    sooner."""
    stub = f'cat >/dev/null\nsleep {_FG_SLEEP_S}\nprintf "%s\\n" "{_BG_STDOUT}"\n'
    root = _fake_plugin(tmp_path, {"slow.sh": stub})
    for label, result, took in _both_launcher_forms(root, "slow.sh", None):
        assert took >= _FG_SLEEP_S, f"{label}: returned after {took:.1f}s"
        assert result.returncode == 0, f"{label}: {result.stderr!r}"
        assert result.stdout.decode("utf-8").rstrip("\r\n") == _BG_STDOUT, f"{label}: {result.stdout!r}"


@_needs_win_launcher
def test_launcher_forwards_a_large_non_ascii_stdout_complete(tmp_path):
    """SessionStart's recap is UTF-8 and can be large: >= 64 KB (past any pipe
    buffer) of non-ASCII output comes through byte-for-byte in both forms."""
    line = "recap line ünï – 日本 ✓ {:06d}\n"
    expected = "".join(line.format(i) for i in range(4000)).encode("utf-8")
    assert len(expected) >= 64 * 1024
    big = tmp_path / "big.txt"
    big.write_bytes(expected)
    root = _fake_plugin(tmp_path, {"big.sh": 'cat >/dev/null\ncat "$REMEMBER_TEST_BIG"\n'})
    for label, result, _took in _both_launcher_forms(
            root, "big.sh", {"REMEMBER_TEST_BIG": str(big).replace("\\", "/")}):
        assert result.returncode == 0, f"{label}: {result.stderr!r}"
        assert result.stdout == expected, (
            f"{label}: {len(result.stdout)} bytes vs {len(expected)} expected")


@_needs_win_launcher
def test_launcher_runs_the_hook_when_the_handle_step_fails(tmp_path):
    """The handle step is best effort: when it throws (here a type already
    loaded under its name, without its method), the hook still runs and its
    exit code is forwarded -- it is not swallowed by the launcher's own
    exit-0 catch. Observed on Windows only."""
    marker = tmp_path / "ran.txt"
    root = _fake_plugin(tmp_path, {"probe.sh": _marker_stub() + "exit 4\n"})
    result = _run_launcher(
        root,
        "Add-Type -TypeDefinition 'namespace ClaudeRemember { public static class Handles { } }'; "
        r'& "$env:CLAUDE_PLUGIN_ROOT\scripts\run-hook.ps1" probe.sh; exit $LASTEXITCODE',
        {"REMEMBER_TEST_MARKER": str(marker).replace("\\", "/")})
    assert marker.is_file(), result.stderr
    assert result.returncode == 4, result.stderr
    assert b"claude-remember: launcher: could not detach background work" in result.stderr
    assert b"the host will wait for it" in result.stderr


def _run_path_lookup_only(tmp_path, root, fake_dir_name, marker):
    """Run the launcher where only its PATH lookup can find bash. PATH holds a
    fake `bash.exe` (not a program) ahead of Git's. ProgramFiles cannot be
    overridden through the child's environment block (Windows recomputes it at
    process start -- observed), so the four base directories are reassigned
    inside the PowerShell process and the launcher is called in that process."""
    empty = tmp_path / "empty"
    empty.mkdir(exist_ok=True)
    fake = tmp_path / fake_dir_name
    fake.mkdir()
    (fake / "bash.exe").write_text("not a program", encoding="utf-8")
    git_bin = str(Path(GIT_BASH).parent)
    sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    env = _launcher_env(root, {
        "REMEMBER_TEST_MARKER": str(marker).replace("\\", "/"),
        "PATH": os.pathsep.join([str(fake), git_bin, sys32]),
    }, path_first_system32=False)
    launcher = root / "scripts" / "run-hook.ps1"
    command = (
        f"$env:ProgramW6432 = '{empty}'; "
        f"$env:ProgramFiles = '{empty}'; ${{env:ProgramFiles(x86)}} = '{empty}'; "
        f"$env:LOCALAPPDATA = '{empty}'; "
        f"& '{launcher}' probe.sh; exit $LASTEXITCODE"
    )
    return subprocess.run(
        [_WIN_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", command],
        input=_PAYLOAD.encode("utf-8"), capture_output=True, env=env, timeout=120,
    )


@_needs_win_launcher
def test_path_lookup_skips_the_windowsapps_alias(tmp_path):
    """A `bash.exe` under a WindowsApps directory (the Store WSL alias) is
    skipped like System32's. Observed on Windows only."""
    marker = tmp_path / "ran.txt"
    root = _fake_plugin(tmp_path, {"probe.sh": _marker_stub()})
    result = _run_path_lookup_only(tmp_path, root, "WindowsApps", marker)
    assert result.returncode == 0, result.stderr
    assert marker.is_file(), result.stderr


@_needs_win_launcher
def test_path_lookup_takes_the_first_bash_otherwise(tmp_path):
    """Positive control: the same layout under a neutral directory name picks
    the fake first, so the stub does not run -- the exclusion above is what
    made the difference."""
    marker = tmp_path / "ran.txt"
    root = _fake_plugin(tmp_path, {"probe.sh": _marker_stub()})
    result = _run_path_lookup_only(tmp_path, root, "SomeTools", marker)
    assert not marker.exists()
    assert result.returncode == 0, result.stderr


@_needs_win_launcher
def test_program_w6432_is_a_git_bash_base(tmp_path):
    """A 32-bit PowerShell sees ProgramFiles as "Program Files (x86)"; the
    64-bit Git under ProgramW6432 must still be found. Here the other three
    bases are emptied and PATH holds no bash at all, so only ProgramW6432 can
    reach it. Observed on Windows only (64-bit PowerShell, simulated)."""
    git_base = Path(GIT_BASH).parent.parent.parent  # <base>\Git\bin\bash.exe
    if not (git_base / "Git" / "bin" / "bash.exe").is_file():
        pytest.skip(f"Git Bash is not at <base>\\Git\\bin\\bash.exe here ({GIT_BASH})")
    marker = tmp_path / "ran.txt"
    root = _fake_plugin(tmp_path, {"probe.sh": _marker_stub()})
    empty = tmp_path / "empty"
    empty.mkdir()
    sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    env = _launcher_env(root, {"REMEMBER_TEST_MARKER": str(marker).replace("\\", "/"),
                               "PATH": sys32}, path_first_system32=False)
    launcher = root / "scripts" / "run-hook.ps1"
    command = (
        f"$env:ProgramW6432 = '{git_base}'; "
        f"$env:ProgramFiles = '{empty}'; ${{env:ProgramFiles(x86)}} = '{empty}'; "
        f"$env:LOCALAPPDATA = '{empty}'; "
        f"& '{launcher}' probe.sh; exit $LASTEXITCODE"
    )
    result = subprocess.run(
        [_WIN_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", command],
        input=_PAYLOAD.encode("utf-8"), capture_output=True, env=env, timeout=120)
    assert result.returncode == 0, result.stderr
    assert marker.is_file(), result.stderr
    assert b"Git Bash not found" not in result.stderr


@_needs_win_launcher
def test_remember_bash_pointing_at_a_directory_falls_through(tmp_path):
    """REMEMBER_BASH naming a directory is not a program: the launcher must
    skip it (Test-Path -PathType Leaf) and find Git Bash as usual, rather
    than try to run the directory."""
    marker = tmp_path / "ran.txt"
    root = _fake_plugin(tmp_path, {"probe.sh": _marker_stub()})
    a_dir = tmp_path / "a-directory"
    a_dir.mkdir()
    env = _launcher_env(root, {"REMEMBER_TEST_MARKER": str(marker).replace("\\", "/"),
                               "REMEMBER_BASH": str(a_dir)})
    result = _run_launcher_file(root, "probe.sh", env)
    assert result.returncode == 0, result.stderr
    assert marker.is_file(), result.stderr
    assert b"launcher error" not in result.stderr


# --- stdin: bash inherits the launcher's handle -----------------------------
# The launcher does not read stdin; bash reads the host's pipe itself. So the
# bytes arrive exactly (no re-encoding, no appended CRLF), EOF is the host's
# own, and the hooks' own `read -t 1` bound applies to a pipe left open.

_STDIN_SEEN_STUB = 'cat > "$REMEMBER_TEST_OUT"\nexit 5\n'


def _launcher_with_input(root, script, payload: bytes, env, manifest: bool):
    if manifest:
        argv = [_WIN_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command", _PS_VALUE.format(name=script)]
    else:
        argv = [_WIN_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(root / "scripts" / "run-hook.ps1"), script]
    return subprocess.run(argv, input=payload, capture_output=True, env=env, timeout=120)


@_needs_win_launcher
@pytest.mark.parametrize("manifest", [False, True], ids=["-File", "manifest"])
def test_launcher_hands_bash_the_exact_stdin_bytes(tmp_path, manifest):
    """No trailing newline, non-ASCII, a CR in the middle: what bash reads is
    byte-identical to what the host wrote. `cat` returning at all is the EOF
    check (it would hang otherwise; the run has a timeout)."""
    out = tmp_path / "seen.bin"
    root = _fake_plugin(tmp_path, {"seen.sh": _STDIN_SEEN_STUB})
    payload = '{"a":"ünï – 日本","b":"x\ry"}'.encode("utf-8")
    env = _launcher_env(root, {"REMEMBER_TEST_OUT": str(out).replace("\\", "/")})
    result = _launcher_with_input(root, "seen.sh", payload, env, manifest)
    assert result.returncode == 5, result.stderr
    assert out.read_bytes() == payload


@_needs_win_launcher
@pytest.mark.parametrize("manifest", [False, True], ids=["-File", "manifest"])
def test_launcher_hands_bash_a_large_stdin_complete(tmp_path, manifest):
    """>= 1 MB of stdin (past every pipe buffer) arrives complete."""
    out = tmp_path / "seen.bin"
    root = _fake_plugin(tmp_path, {"seen.sh": _STDIN_SEEN_STUB})
    payload = (b'{"transcript":"' + "ünï 日本 ".encode("utf-8") * 90_000 + b'"}')
    assert len(payload) >= 1024 * 1024
    env = _launcher_env(root, {"REMEMBER_TEST_OUT": str(out).replace("\\", "/")})
    result = _launcher_with_input(root, "seen.sh", payload, env, manifest)
    assert result.returncode == 5, result.stderr
    seen = out.read_bytes()
    assert len(seen) == len(payload) and seen == payload


_READ_T1_STUB = 'IFS= read -r -t 1 line\nprintf "got:%s\\n" "$line"\nexit 0\n'
# Same shape as the detach tests above. The held-open case keeps stdin open
# for _HELD_OPEN_S, so a launcher that waited for EOF is held that whole time
# and then killed (rc None): the launcher exiting at all is the proof that it
# did not wait. The return bound sits 10 s below the hold -- a hang detector,
# not a start-up measurement: a run chains two cold Windows PowerShell 5.1
# starts, a first Add-Type compile and the stub's 1 s read.
_HELD_OPEN_S = 30
_HELD_OPEN_BOUND_S = _HELD_OPEN_S - 10


def _held_open(root, script, payload: bytes, env, manifest: bool, close: bool):
    """Write the payload and either close stdin or keep it open for
    _HELD_OPEN_S; return (seconds until the launcher exited or the hold
    ended, stdout, rc -- None when the launcher was still running)."""
    if manifest:
        argv = [_WIN_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command", _PS_VALUE.format(name=script)]
    else:
        argv = [_WIN_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(root / "scripts" / "run-hook.ps1"), script]
    started = time.monotonic()
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env)
    try:
        proc.stdin.write(payload)
        proc.stdin.flush()
        if close:
            proc.stdin.close()
        deadline = started + _HELD_OPEN_S
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        took = time.monotonic() - started
        if proc.poll() is None:
            proc.kill()
            proc.wait()
            return took, b"", None
        return took, proc.stdout.read(), proc.returncode
    finally:
        if not proc.stdin.closed:
            proc.stdin.close()
        proc.stdout.close()
        proc.stderr.close()


@_needs_win_launcher
@pytest.mark.parametrize("manifest", [False, True], ids=["-File", "manifest"])
def test_launcher_returns_when_bash_exits_with_stdin_held_open(tmp_path, manifest):
    """The host writes the payload and never closes stdin: a hook that reads
    with `read -t 1` and exits must return the launcher, not leave it blocked
    on the open pipe."""
    root = _fake_plugin(tmp_path, {"readt.sh": _READ_T1_STUB})
    took, out, rc = _held_open(root, "readt.sh", b'{"a":1}', _launcher_env(root),
                               manifest, close=False)
    assert rc == 0, f"launcher still running after {took:.1f}s with stdin held open"
    assert took < _HELD_OPEN_BOUND_S, f"{took:.1f}s"
    assert out.decode("utf-8").rstrip("\r\n") == 'got:{"a":1}'


@_needs_win_launcher
def test_launcher_returns_with_stdin_closed_too(tmp_path):
    """Positive control for the held-open case: the same stub with stdin
    closed returns and reads the same payload."""
    root = _fake_plugin(tmp_path, {"readt.sh": _READ_T1_STUB})
    took, out, rc = _held_open(root, "readt.sh", b'{"a":1}\n', _launcher_env(root),
                               manifest=False, close=True)
    assert rc == 0 and took < _HELD_OPEN_BOUND_S
    assert out.decode("utf-8").rstrip("\r\n") == 'got:{"a":1}'


# --- Constrained Language Mode ----------------------------------------------
# CLM is what AppLocker / WDAC script enforcement imposes. Setting
# `__PSLockdownPolicy=4` in a child's environment did NOT engage it on this
# machine's Windows PowerShell 5.1 (LanguageMode stayed FullLanguage --
# observed), so the session's language mode is set in-process and the
# launcher is called in that session, where scripts inherit it (observed).

_CLM_STDOUT = "clm stdout ünï – 日本"


def _run_launcher_in_language_mode(root, mode, marker):
    launcher = root / "scripts" / "run-hook.ps1"
    pre = f"$ExecutionContext.SessionState.LanguageMode = '{mode}'; " if mode else ""
    command = pre + f"& '{launcher}' probe.sh; exit $LASTEXITCODE"
    env = _launcher_env(root, {"REMEMBER_TEST_MARKER": str(marker).replace("\\", "/")})
    return subprocess.run(
        [_WIN_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", command],
        input=_PAYLOAD.encode("utf-8"), capture_output=True, env=env, timeout=120)


def _clm_stub():
    return ('cat > "$REMEMBER_TEST_MARKER"\nprintf "%s\\n" "' + _CLM_STDOUT + '"\nexit 4\n')


@_needs_win_launcher
def test_launcher_runs_the_hook_under_constrained_language_mode(tmp_path):
    """Under CLM the hook still runs with the payload on stdin, its stdout
    and exit code come through, and one line on stderr says the background
    work could not be detached (nothing on stdout but the hook's own)."""
    marker = tmp_path / "ran.txt"
    root = _fake_plugin(tmp_path, {"probe.sh": _clm_stub()})
    result = _run_launcher_in_language_mode(root, "ConstrainedLanguage", marker)
    assert marker.is_file(), result.stderr
    assert marker.read_bytes() == _PAYLOAD.encode("utf-8")
    assert result.returncode == 4, result.stderr
    assert result.stdout.decode("utf-8").rstrip("\r\n") == _CLM_STDOUT, result.stdout
    assert b"Constrained Language Mode" in result.stderr, result.stderr
    assert b"launcher error" not in result.stderr, result.stderr


@_needs_win_launcher
def test_full_language_mode_takes_the_normal_path(tmp_path):
    """Positive control: the same call without CLM runs the hook too, and
    says nothing about Constrained Language Mode -- the line above comes from
    the language-mode check, not from every run."""
    marker = tmp_path / "ran.txt"
    root = _fake_plugin(tmp_path, {"probe.sh": _clm_stub()})
    result = _run_launcher_in_language_mode(root, None, marker)
    assert marker.is_file(), result.stderr
    assert result.returncode == 4, result.stderr
    assert result.stdout.decode("utf-8").rstrip("\r\n") == _CLM_STDOUT, result.stdout
    assert b"Constrained Language Mode" not in result.stderr, result.stderr


@_needs_win_launcher
def test_launcher_error_under_constrained_language_mode_still_exits_zero(tmp_path):
    """The outer catch is CLM-safe: an error inside the launcher under CLM
    (here: REMEMBER_BASH is a file that is not a program) is reported and the
    launcher exits 0, instead of the catch itself throwing."""
    bogus = tmp_path / "bogus" / "bash.exe"
    bogus.parent.mkdir()
    bogus.write_text("not a program", encoding="utf-8")
    root = _fake_plugin(tmp_path, {"probe.sh": "cat >/dev/null\n"})
    launcher = root / "scripts" / "run-hook.ps1"
    command = ("$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'; "
               f"& '{launcher}' probe.sh; exit $LASTEXITCODE")
    result = subprocess.run(
        [_WIN_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", command],
        input=_PAYLOAD.encode("utf-8"), capture_output=True,
        env=_launcher_env(root, {"REMEMBER_BASH": str(bogus)}), timeout=120)
    assert result.returncode == 0, result.stderr
    assert b"claude-remember: launcher error" in result.stderr, result.stderr
