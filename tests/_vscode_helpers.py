"""Shared helpers for the VS Code Agents / Copilot port's tests (issue: vscode).

One topic per helper module, as tests/_bash_runner.py and
tests/_compiled_hooks.py are: what several of the port's test files used to
define for themselves.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline import host as _host  # noqa: E402

# The session id the port's tests use wherever any bare uuid will do.
UUID = "11111111-2222-4333-8444-555555555555"

# Every host signature the registry knows, plus the hint the hooks export.
# The suite usually runs inside a Claude Code (or Copilot, or Codex) session,
# so a test strips these from the environment it hands a hook and adds back
# only what a case names. Read from the registry, so a new host row cannot
# leave its signature in the inherited environment.
HOST_ENV = tuple(dict.fromkeys(
    tuple(v for h in _host.REGISTRY for v in h.signature_vars) + ("REMEMBER_HOST_HINT",)))


def posix(p) -> str:
    """Forward slashes: the hooks derive their own directory with
    `${BASH_SOURCE[0]%/*}`, which only splits on `/` (see
    tests/test_session_end_log_names_488.py::_posix_path)."""
    return str(p).replace("\\", "/")


def hook_logs(log_dir: Path, pattern: str = "*.log") -> str:
    """Every daily log under LOG_DIR matching PATTERN, joined by newlines."""
    return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in sorted(log_dir.glob(pattern)))


def tree(root: Path) -> set[str]:
    """Every path under ROOT, relative and with forward slashes."""
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


def created_outside(root: Path, before: set[str], prefixes: tuple[str, ...],
                    exact: tuple[str, ...] = ()) -> list[str]:
    """Paths under ROOT that are new since BEFORE (a `tree(root)`) and are
    neither one of EXACT nor under one of PREFIXES -- what a traversal test
    asserts is empty."""
    return sorted(p for p in tree(root) - before
                  if p not in exact and not p.startswith(prefixes))
