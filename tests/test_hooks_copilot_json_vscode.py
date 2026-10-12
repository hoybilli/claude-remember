"""The Copilot-native manifest: root plugin.json and hooks/hooks.copilot.json
(issue: vscode).

The Copilot hosts (VS Code Agents, the Copilot CLI, the desktop app) load
the root plugin.json when it is present and then do not load
hooks/hooks.json (observed in the 2026-10-08 probe, docs/vscode-
verification.md); Claude Code loads only .claude-plugin/plugin.json. So the
Copilot binding lives here and hooks/hooks.json stays byte-identical to
upstream (tests/test_core_hooks_carry_no_copilot_vscode.py).

Format, from the probe: `version` 1; PascalCase event keys, which make the
hosts send snake_case payloads with `hook_event_name`; a flat list of
handlers per event, each with `bash` (macOS/Linux) and `powershell`
(Windows) and no `command` fallback; `timeoutSec`.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
COPILOT_HOOKS = REPO_ROOT / "hooks" / "hooks.copilot.json"
CLAUDE_HOOKS = REPO_ROOT / "hooks" / "hooks.json"
ROOT_MANIFEST = REPO_ROOT / "plugin.json"
CLAUDE_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
SCRIPTS = REPO_ROOT / "scripts"

EXPECTED = {
    "SessionStart": "copilot-session-start-hook.sh",
    "UserPromptSubmit": "user-prompt-hook.sh",
    "PostToolUse": "copilot-post-tool-hook.sh",
    "SessionEnd": "copilot-session-end-hook.sh",
}
_BASH_VALUE = 'bash "${{CLAUDE_PLUGIN_ROOT}}/scripts/{name}"'
# The launcher string tests/test_hooks_json.py drives through run-hook.ps1,
# with the wrapper's name: run-hook.ps1 resolves its target beside itself.
_PS_VALUE = ('powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File '
             '"$env:CLAUDE_PLUGIN_ROOT\\scripts\\run-hook.ps1" {name}; exit $LASTEXITCODE')
# Each wrapper task adds its script here once the script exists (Tasks 4-6).
_SCRIPTS_THAT_MUST_EXIST = (
    "user-prompt-hook.sh",
    "copilot-session-start-hook.sh",
    "copilot-post-tool-hook.sh",
)


def _load() -> dict:
    return json.loads(COPILOT_HOOKS.read_text(encoding="utf-8"))


def _handlers():
    for event, handlers in _load()["hooks"].items():
        for i, handler in enumerate(handlers):
            yield f"{event}[{i}]", event, handler


def test_version_is_1():
    assert _load()["version"] == 1


def test_exactly_the_four_pascalcase_events():
    assert sorted(_load()["hooks"]) == sorted(EXPECTED)


def test_one_handler_per_event():
    assert {e: len(h) for e, h in _load()["hooks"].items()} == {e: 1 for e in EXPECTED}


def test_every_handler_has_bash_and_powershell_and_no_command():
    seen = 0
    for loc, event, handler in _handlers():
        seen += 1
        assert handler["type"] == "command", loc
        assert "command" not in handler, f"{loc}: no `command` fallback key"
        assert handler["bash"] == _BASH_VALUE.format(name=EXPECTED[event]), loc
        assert handler["powershell"] == _PS_VALUE.format(name=EXPECTED[event]), loc
    assert seen == len(EXPECTED)


def test_session_end_carries_upstreams_timeout_and_nothing_else_does():
    upstream = json.loads(CLAUDE_HOOKS.read_text(encoding="utf-8"))
    timeout = upstream["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"]
    hooks = _load()["hooks"]
    assert hooks["SessionEnd"][0]["timeoutSec"] == timeout == 10
    # Paired control: the other three handlers carry none (as upstream).
    assert [e for e in EXPECTED if e != "SessionEnd" and "timeoutSec" in hooks[e][0]] == []


_PARSER_PROBE = (
    "$src = [Console]::In.ReadToEnd(); "
    "$errors = $null; "
    "$null = [System.Management.Automation.Language.Parser]::ParseInput("
    "$src, [ref]$null, [ref]$errors); "
    "if ($errors) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
)
_PS_EXE = shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(_PS_EXE is None, reason="no PowerShell on PATH")
def test_powershell_values_parse_under_powershell():
    for loc, _event, handler in _handlers():
        r = subprocess.run([_PS_EXE, "-NoProfile", "-NonInteractive", "-Command", _PARSER_PROBE],
                           input=handler["powershell"] + "\n", capture_output=True,
                           text=True, encoding="utf-8")
        assert r.returncode == 0, f"{loc}: {r.stderr!r}"


@pytest.mark.skipif(_PS_EXE is None, reason="no PowerShell on PATH")
def test_the_powershell_probe_rejects_a_broken_value():
    """Positive control for the parse test above."""
    r = subprocess.run([_PS_EXE, "-NoProfile", "-NonInteractive", "-Command", _PARSER_PROBE],
                       input='powershell -File "unterminated\n', capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode != 0


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None,
                    reason="Windows `bash` may be WSL's launcher; the bash key runs on macOS/Linux")
def test_bash_values_parse_under_bash():
    for loc, _event, handler in _handlers():
        r = subprocess.run(["bash", "-n", "/dev/stdin"], input=handler["bash"] + "\n",
                           capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, f"{loc}: {r.stderr!r}"


def test_root_manifest_names_the_copilot_hooks_file_and_mirrors_claudes():
    root = json.loads(ROOT_MANIFEST.read_text(encoding="utf-8"))
    claude = json.loads(CLAUDE_MANIFEST.read_text(encoding="utf-8"))
    assert root["hooks"] == "./hooks/hooks.copilot.json"
    for field in ("name", "version", "description", "author", "license", "keywords"):
        assert root[field] == claude[field], field


def test_root_manifest_carries_no_scheme_literal():
    """check_release_tree.py FAILs a scheme literal outside SCHEME_ALLOWLIST,
    which names .claude-plugin/plugin.json but not plugin.json -- so the
    root manifest does not mirror `repository`. Positive control: the Claude
    manifest does carry one."""
    assert "://" not in ROOT_MANIFEST.read_text(encoding="utf-8")
    assert "://" in CLAUDE_MANIFEST.read_text(encoding="utf-8")


def test_the_claude_manifest_names_no_hooks_file():
    """Claude Code finds hooks/hooks.json by convention; the release checker
    refuses a manifest that also names it. The Copilot file is named only by
    the root manifest (positive control: the test above)."""
    assert "hooks" not in json.loads(CLAUDE_MANIFEST.read_text(encoding="utf-8"))


def test_root_manifest_is_a_version_site():
    """A release bumps every file in .oss.json's version_sites; the root
    manifest's version must move with .claude-plugin/plugin.json's."""
    sites = json.loads((REPO_ROOT / ".oss.json").read_text(encoding="utf-8"))["version_sites"]
    assert "plugin.json" in sites
    assert ".claude-plugin/plugin.json" in sites  # positive control


@pytest.mark.parametrize("name", _SCRIPTS_THAT_MUST_EXIST)
def test_named_script_exists(name):
    assert (SCRIPTS / name).is_file()


_BOUND_SCRIPT = re.compile(r'/scripts/([^"\s]+)')


def _unbound(script_names, handlers) -> list[str]:
    """The names in `script_names` that no handler's `bash` value binds.

    Each bound name is extracted whole from the `bash` values, so a name that
    is only a prefix of a bound one does not count as bound."""
    bound = {m.group(1) for h in handlers for m in _BOUND_SCRIPT.finditer(h.get("bash", ""))}
    return sorted(set(script_names) - bound)


def test_every_copilot_script_is_bound():
    """tests/test_hooks_json.py's wiring guard exempts scripts/copilot-*.sh
    (hooks/hooks.json is upstream's); this is their wiring guard instead: a
    copilot-*.sh that hooks/hooks.copilot.json never binds is dead code.
    Vacuous until the wrappers land (Tasks 3-6); the control below is not."""
    names = [p.name for p in SCRIPTS.glob("copilot-*.sh")]
    assert _unbound(names, [h for _loc, _e, h in _handlers()]) == []


def test_the_binding_check_reports_an_unbound_name():
    """Positive control for the guard above: against the real handlers, a
    bound name is found and an unbound one is reported."""
    handlers = [h for _loc, _e, h in _handlers()]
    assert _unbound(["copilot-session-end-hook.sh"], handlers) == []
    assert _unbound(["copilot-session-end-hook.sh", "copilot-stray.sh"], handlers) == [
        "copilot-stray.sh"]
    # A name that is only a prefix of a bound name is not bound.
    assert _unbound(["copilot-session-end"], handlers) == ["copilot-session-end"]
