"""The four core hooks and hooks/hooks.json carry nothing of the Copilot port
(issue: vscode).

The port is an adapter layer: hooks/hooks.copilot.json binds
scripts/copilot-*-hook.sh, which rewrite the payload and delegate to these
files unchanged. They stay byte-identical to upstream, so upstream's
compile-and-size gate on them (120 KiB) is upstream's alone. CI cannot diff
against the upstream ref, so this guard stands in for that check: none of
the port's tokens may appear in them. The byte-for-byte check itself is
`git diff fd8b655 HEAD -- <these files>` (docs/vscode-verification.md).
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_FILES = (
    "scripts/session-start-hook.sh",
    "scripts/user-prompt-hook.sh",
    "scripts/post-tool-hook.sh",
    "scripts/session-end-hook.sh",
    "hooks/hooks.json",
)
# Case-sensitive on purpose: upstream's own prose may say "Copilot" one day;
# these are the spellings the port's code used.
PORT_TOKENS = ("copilot", "COPILOT", "agent-host", "REMEMBER_HOST_HINT")


def _found(rel: str) -> list[str]:
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    return [t for t in PORT_TOKENS if t in text]


@pytest.mark.parametrize("rel", CORE_FILES)
def test_core_file_carries_no_port_token(rel):
    assert _found(rel) == []


def test_the_scan_finds_the_tokens_where_the_port_lives():
    """Positive control: the same scan on the port's own library finds them,
    so an empty result above is not a scan that cannot see."""
    assert set(_found("scripts/lib-session-id.sh")) >= {"copilot", "COPILOT", "agent-host"}
