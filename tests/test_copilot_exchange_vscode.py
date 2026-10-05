"""VS Code Agents (Copilot harness) exchange reader (issue: vscode)."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline import host as _host
from pipeline.extract import extract_messages

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
VSCODE_EVENTS = os.path.join(FIXTURES, "vscode-events.jsonl")


def _by_type(t):
    with open(VSCODE_EVENTS, encoding="utf-8") as f:
        return [o for o in map(json.loads, f) if o.get("type") == t]


def test_user_message_is_human_and_uses_content_not_transformed():
    obj = _by_type("user.message")[0]
    role, text = _host.copilot_exchange(obj)
    assert role == "HUMAN"
    assert text == obj["data"]["content"].strip()
    # negative: VS Code's injected reminder travels in transformedContent only
    assert "<system_reminder>" not in text
    # positive control for the negative: the fixture line really carries it
    assert "<system_reminder>" in obj["data"].get("transformedContent", "")


def test_fixture_tool_request_turn_yields_tool_lines():
    """The fixture's assistant turns carry empty `content`: this covers the
    tool lines only (prose is covered by the constructed cases below)."""
    obj = next(o for o in _by_type("assistant.message") if o["data"].get("toolRequests"))
    role, text = _host.copilot_exchange(obj)
    assert role == "AGENT"
    assert "[TOOL: " in text
    name = obj["data"]["toolRequests"][0]["name"]
    assert f"[TOOL: {name}" in text


def _constructed_assistant(content, tool_requests):
    """An `assistant.message` record CONSTRUCTED for this test, shaped like the
    fixture's records (same keys), because every assistant line in the captured
    fixture has an empty `content`."""
    return {
        "type": "assistant.message",
        "data": {"messageId": "m-1", "model": "example-model", "content": content,
                 "toolRequests": tool_requests, "turnId": "1"},
        "id": "e-1", "timestamp": "2026-09-06T18:07:51.300Z", "parentId": "p-1",
    }


def test_assistant_prose_and_tool_line_both_appear_in_order():
    obj = _constructed_assistant(
        "  I will read the README first.  ",
        [{"toolCallId": "c1", "name": "view", "type": "function",
          "arguments": {"path": "c:\\Users\\user\\proj\\README.md"}}],
    )
    role, text = _host.copilot_exchange(obj)
    assert role == "AGENT"
    assert text == "I will read the README first.\n[TOOL: view README.md]"


def test_assistant_prose_only_is_kept():
    obj = _constructed_assistant("The answer is 42.", [])
    assert _host.copilot_exchange(obj) == ("AGENT", "The answer is 42.")


def test_assistant_message_tool_only_still_yields_agent_line():
    """Review focus 1: an empty-content, tool-only turn is not dropped."""
    obj = next(o for o in _by_type("assistant.message") if o["data"].get("toolRequests"))
    obj = json.loads(json.dumps(obj))
    obj["data"]["content"] = ""
    role, text = _host.copilot_exchange(obj)
    assert role == "AGENT" and text.startswith("[TOOL: ")


def test_blank_assistant_message_without_tools_is_none():
    obj = json.loads(json.dumps(_by_type("assistant.message")[0]))
    obj["data"]["content"] = "   "
    obj["data"]["toolRequests"] = []
    assert _host.copilot_exchange(obj) is None


def test_non_message_events_are_none():
    for t in ("session.start", "tool.execution_complete", "hook.start", "assistant.turn_start"):
        for obj in _by_type(t):
            assert _host.copilot_exchange(obj) is None, t


def test_tool_request_formatting_matches_claude_style():
    fmt = _host._format_copilot_tool_request
    assert fmt({"name": "view", "arguments": {"path": "c:\\Users\\user\\proj\\README.md"}}) == "[TOOL: view README.md]"
    assert fmt({"name": "powershell", "arguments": {"command": "git status"}}) == "[TOOL: powershell `git status`]"
    assert fmt({"name": "bash", "arguments": '{"command": "ls -la"}'}) == "[TOOL: bash `ls -la`]"   # JSON-string args
    assert fmt({"name": "task_complete", "arguments": {"summary": "x"}}) == "[TOOL: task_complete]"
    assert fmt({"arguments": {}}) == "[TOOL: ?]"


def test_oversized_string_arguments_render_bounded_without_decoding():
    """200,000 `[` (model-controlled, double-encoded): not decoded, rendered
    as a bounded `[TOOL: ...]` line instead of raising."""
    line = _host._format_copilot_tool_request({"name": "edit", "arguments": "[" * 200_000})
    assert line.startswith("[TOOL: edit ")
    assert len(line) < 200


def test_deeply_nested_string_arguments_under_the_size_cap_do_not_raise():
    """Under the size cap but past the decoder's recursion limit. Depending on
    the Python version the decoder raises either `RecursionError` (3.9, 3.14.4)
    or `JSONDecodeError` (3.14.7); both outcomes are acceptable, so this pins
    only the contract that holds on every version: no exception, and a bounded
    `[TOOL: edit...]` line. Each branch is pinned deterministically below."""
    deep = "[" * 60_000
    assert len(deep) <= _host._COPILOT_ARGS_DECODE_MAX
    line = _host._format_copilot_tool_request({"name": "edit", "arguments": deep})
    assert line.startswith("[TOOL: edit")
    assert len(line) < 200


def test_recursion_error_from_the_decoder_renders_the_raw_string_truncated(monkeypatch):
    """Interpreter-independent: the decoder is made to raise RecursionError."""
    def boom(_s):
        raise RecursionError("maximum recursion depth exceeded")
    monkeypatch.setattr(_host.json, "loads", boom)
    raw = "[" * 1000
    line = _host._format_copilot_tool_request({"name": "edit", "arguments": raw})
    assert line == f"[TOOL: edit `{raw[:80]}`]"
    assert line.startswith("[TOOL: edit `")
    assert raw[:80] in line


def test_json_decode_error_from_the_decoder_renders_no_detail(monkeypatch):
    """Positive control for the test above: a JSONDecodeError takes the other
    branch, and the result is exactly the bare `[TOOL: edit]`."""
    def boom(_s):
        raise json.JSONDecodeError("x", "", 0)
    monkeypatch.setattr(_host.json, "loads", boom)
    line = _host._format_copilot_tool_request({"name": "edit", "arguments": "[" * 1000})
    assert line == "[TOOL: edit]"


def test_a_poison_tool_line_does_not_stop_extraction(tmp_path):
    """One such line among real exchanges: extract_messages still returns the
    exchanges on both sides of it."""
    lines = [
        {"type": "session.start", "data": {}},
        {"type": "user.message", "data": {"content": "first question"}},
        {"type": "assistant.message",
         "data": {"content": "", "toolRequests": [{"name": "t", "arguments": "[" * 200_000}]}},
        {"type": "assistant.message", "data": {"content": "first answer"}},
        {"type": "user.message", "data": {"content": "second question"}},
    ]
    path = tmp_path / "events.jsonl"
    path.write_text("".join(json.dumps(o) + "\n" for o in lines), encoding="utf-8")
    msgs = extract_messages(str(path), envelope="copilot")
    texts = [t for _r, t in msgs]
    assert any("first question" in t for t in texts)
    assert any("first answer" in t for t in texts)
    assert any("second question" in t for t in texts)


def test_copilot_transcript_lookup_rejects_a_trailing_newline(tmp_path, monkeypatch):
    """fullmatch, not match-with-$: `<uuid>\\n` is not a uuid. Every file is
    made to "exist" so only the id check can refuse (a directory whose name
    ends in a newline cannot be created on Windows filesystems)."""
    uuid = "11111111-2222-4333-8444-555555555555"
    monkeypatch.setattr(os.path, "isfile", lambda _p: True)
    env = {"COPILOT_HOME": str(tmp_path)}
    assert _host.copilot_transcript_for(uuid, env) is not None   # positive control
    assert _host.copilot_transcript_for(uuid + "\n", env) is None


def test_extract_messages_dispatches_copilot_envelope():
    msgs = extract_messages(VSCODE_EVENTS, envelope="copilot")
    roles = [r for r, _ in msgs]
    assert "HUMAN" in roles and "AGENT" in roles
    # ordering is chronological: the first user message precedes the first agent one
    assert roles.index("HUMAN") < roles.index("AGENT")
    # negative: an unrecognised envelope still yields nothing (existing contract)
    assert extract_messages(VSCODE_EVENTS, envelope="unrecognised") == []
