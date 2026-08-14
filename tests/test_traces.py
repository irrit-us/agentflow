from agentflow.specs import AgentKind
from agentflow.traces import create_trace_parser


def test_codex_trace_parser_extracts_assistant_message():
    parser = create_trace_parser(AgentKind.CODEX, "plan")
    events = parser.feed('{"type":"response.output_item.done","item":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"codex ok"}]}}')
    assert events[0].kind == "assistant_message"
    assert parser.finalize() == "codex ok"


def test_codex_trace_parser_ignores_unstable_feature_warning():
    parser = create_trace_parser(AgentKind.CODEX, "plan")

    assert parser.feed('{"type":"item.completed","item":{"id":"item_0","type":"error","message":"Under-development features enabled: responses_websockets_v2. To suppress this warning, set suppress_unstable_features_warning = true in /home/shou/.codex/config.toml."}}') == []

    events = parser.feed('{"type":"response.output_item.done","item":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"codex ok"}]}}')

    assert events[0].kind == "assistant_message"
    assert parser.finalize() == "codex ok"


def test_codex_trace_parser_keeps_real_error_items():
    parser = create_trace_parser(AgentKind.CODEX, "plan")

    events = parser.feed('{"type":"item.completed","item":{"id":"item_0","type":"error","message":"permission denied"}}')

    assert events[0].kind == "item_completed"
    assert events[0].title == "Item completed: error"
    assert events[0].content == "permission denied"


def test_claude_trace_parser_extracts_result():
    parser = create_trace_parser(AgentKind.CLAUDE, "implement")
    parser.feed('{"type":"assistant","message":{"content":[{"type":"text","text":"working"}]}}')
    parser.feed('{"type":"result","result":"done"}')
    assert parser.finalize() == "working\ndone"


def test_claude_trace_parser_dedupes_matching_result():
    parser = create_trace_parser(AgentKind.CLAUDE, "implement")
    parser.feed('{"type":"assistant","message":{"content":[{"type":"text","text":"working"}]}}')
    parser.feed('{"type":"result","result":"working"}')
    assert parser.finalize() == "working"


def test_claude_trace_parser_ignores_hook_chatter():
    parser = create_trace_parser(AgentKind.CLAUDE, "implement")

    assert parser.feed('{"type":"system","subtype":"hook_started","hook_name":"SessionStart:startup"}') == []
    assert parser.feed('{"type":"system","subtype":"hook_response","hook_name":"SessionStart:startup","output":"very large startup payload"}') == []

    events = parser.feed('{"type":"assistant","message":{"content":[{"type":"text","text":"working"}]}}')

    assert events[0].kind == "assistant_message"
    assert parser.finalize() == "working"


def test_claude_trace_parser_keeps_hook_failures():
    parser = create_trace_parser(AgentKind.CLAUDE, "implement")

    events = parser.feed('{"type":"system","subtype":"hook_failed","hook_name":"SessionStart:startup","stderr":"hook exploded"}')

    assert events[0].kind == "hook_error"
    assert events[0].title == "Hook failed: SessionStart:startup"
    assert events[0].content == "hook exploded"


def test_kimi_trace_parser_extracts_text_part():
    parser = create_trace_parser(AgentKind.KIMI, "review")
    parser.feed('{"jsonrpc":"2.0","method":"event","params":{"type":"ContentPart","payload":{"type":"text","text":"kimi trace"}}}')
    assert parser.finalize() == "kimi trace"


def test_pi_trace_parser_extracts_final_assistant_text_from_agent_end():
    parser = create_trace_parser(AgentKind.PI, "scan")
    # Realistic Pi event sequence: session / agent_start / turn_start / message_start
    # (user) / message_end (user) / message_start (assistant) / message_update deltas /
    # message_end (assistant) / turn_end / agent_end.
    parser.feed('{"type":"session","id":"abc","cwd":"/tmp"}')
    parser.feed('{"type":"agent_start"}')
    parser.feed('{"type":"turn_start"}')
    parser.feed('{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"Hello"}}')
    parser.feed('{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":" there"}}')
    parser.feed(
        '{"type":"message_end","message":{"role":"assistant",'
        '"content":[{"type":"text","text":"Hello there"}]}}'
    )
    parser.feed(
        '{"type":"agent_end","messages":['
        '{"role":"user","content":[{"type":"text","text":"say hi"}]},'
        '{"role":"assistant","content":[{"type":"text","text":"Hello there"}]}'
        "]}"
    )
    assert parser.finalize() == "Hello there"


def test_pi_trace_parser_emits_delta_events():
    parser = create_trace_parser(AgentKind.PI, "scan")
    events = parser.feed(
        '{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"partial"}}'
    )
    assert len(events) == 1
    assert events[0].kind == "assistant_delta"
    assert events[0].content == "partial"


def test_pi_trace_parser_prefers_agent_end_when_present():
    parser = create_trace_parser(AgentKind.PI, "scan")
    # Only feed agent_end with a single assistant message.
    parser.feed(
        '{"type":"agent_end","messages":['
        '{"role":"assistant","content":[{"type":"text","text":"final answer"}]}'
        "]}"
    )
    assert parser.finalize() == "final answer"


def test_opencode_trace_parser_extracts_completed_text_parts():
    parser = create_trace_parser(AgentKind.OPENCODE, "plan")
    events = parser.feed(
        '{"type":"message.part.updated","part":{"type":"text","text":"opencode ok","state":"completed"}}'
    )
    assert events[0].kind == "assistant_delta"
    assert events[0].content == "opencode ok"
    assert parser.finalize() == "opencode ok"


def test_opencode_trace_parser_unwraps_payload_envelope():
    parser = create_trace_parser(AgentKind.OPENCODE, "plan")
    parser.feed(
        '{"payload":{"type":"message.part.updated","part":{"type":"text","text":"wrapped","state":"completed"}}}'
    )
    assert parser.finalize() == "wrapped"


def test_opencode_trace_parser_ignores_incomplete_text_parts():
    parser = create_trace_parser(AgentKind.OPENCODE, "plan")
    parser.feed(
        '{"type":"message.part.updated","part":{"type":"text","text":"partial","state":"streaming"}}'
    )
    assert parser.finalize() == ""


def test_opencode_trace_parser_joins_text_parts_on_message_updated():
    parser = create_trace_parser(AgentKind.OPENCODE, "plan")
    events = parser.feed(
        '{"type":"message.updated","message":{"parts":['
        '{"type":"text","text":"Hello"},'
        '{"type":"text","text":" world"},'
        '{"type":"tool","tool":"read"}]}}'
    )
    assert events[0].kind == "assistant_message"
    assert events[0].content == "Hello world"
    assert parser.finalize() == "Hello world"


def test_opencode_trace_parser_accumulates_deltas():
    parser = create_trace_parser(AgentKind.OPENCODE, "plan")
    parser.feed('{"type":"message.part.delta","part":{"type":"text","delta":"Hello"}}')
    parser.feed('{"type":"message.part.delta","part":{"type":"text","delta":" world"}}')
    assert parser.finalize() == "Hello\nworld"


def test_opencode_trace_parser_emits_session_and_tool_events():
    parser = create_trace_parser(AgentKind.OPENCODE, "plan")
    idle = parser.feed('{"type":"session.idle"}')
    assert idle[0].kind == "event"
    assert idle[0].title == "Session idle"

    tool = parser.feed('{"type":"tool.execute.before","tool":{"title":"read"}}')
    assert tool[0].kind == "tool_call"
    assert tool[0].title == "Tool Execute Before"

    error = parser.feed('{"type":"error","error":{"message":"boom"}}')
    assert error[0].kind == "error"


def test_opencode_trace_parser_handles_step_text_schema():
    parser = create_trace_parser(AgentKind.OPENCODE, "plan")
    start = parser.feed('{"type":"step_start"}')
    assert start[0].kind == "event"
    assert start[0].title == "step-start"

    text = parser.feed('{"type":"text","part":{"type":"text","text":"OK"}}')
    assert text[0].kind == "assistant_message"
    assert text[0].content == "OK"

    finish = parser.feed('{"type":"step_finish"}')
    assert finish[0].kind == "event"
    assert parser.finalize() == "OK"


def test_opencode_trace_parser_step_text_ignores_empty_parts():
    parser = create_trace_parser(AgentKind.OPENCODE, "plan")
    parser.feed('{"type":"text","part":{"type":"text","text":""}}')
    assert parser.finalize() == ""


def test_goose_trace_parser_extracts_message_content_text_parts():
    parser = create_trace_parser(AgentKind.GOOSE, "review")
    events = parser.feed(
        '{"type":"message","message":{"role":"assistant",'
        '"content":[{"type":"text","text":"goose answer"}]}}'
    )
    assert events[0].kind == "assistant_message"
    assert events[0].content == "goose answer"
    assert parser.finalize() == "goose answer"


def test_goose_trace_parser_emits_complete_event():
    parser = create_trace_parser(AgentKind.GOOSE, "review")
    events = parser.feed('{"type":"complete"}')
    assert events[0].kind == "event"
    assert events[0].title == "Complete"


def test_goose_trace_parser_emits_error_event():
    parser = create_trace_parser(AgentKind.GOOSE, "review")
    events = parser.feed('{"type":"error","error":"goose exploded"}')
    assert events[0].kind == "error"
    assert events[0].content == "goose exploded"


def test_deepseek_trace_parser_uses_terminal_result_as_authoritative_output():
    parser = create_trace_parser(AgentKind.DEEPSEEK, "implement")
    assistant = parser.feed(
        '{"type":"session_event","sessionId":"session-1","event":'
        '{"type":"assistant/message","data":{"message":{"role":"assistant",'
        '"content":[{"type":"text","text":"streamed answer"}]}}}}'
    )
    result = parser.feed(
        '{"type":"result","sessionId":"session-1","output":"terminal answer"}'
    )

    assert assistant[0].kind == "assistant_message"
    assert assistant[0].content == "streamed answer"
    assert result[0].kind == "result"
    assert parser.finalize() == "terminal answer"
    assert parser.supports_raw_stdout_fallback() is False


def test_deepseek_trace_parser_falls_back_to_last_assistant_message_without_result():
    parser = create_trace_parser(AgentKind.DEEPSEEK, "implement")
    parser.feed(
        '{"type":"session_event","sessionId":"session-1","event":'
        '{"type":"assistant/message","data":{"message":{"content":'
        '[{"type":"text","text":"fallback"}]}}}}'
    )

    assert parser.finalize() == "fallback"


def test_deepseek_trace_parser_normalizes_tools_and_turn_end():
    parser = create_trace_parser(AgentKind.DEEPSEEK, "implement")
    call = parser.feed(
        '{"type":"session_event","event":{"type":"tool/call",'
        '"data":{"name":"shell","arguments":"{\\"command\\":\\"pwd\\"}"}}}'
    )
    result = parser.feed(
        '{"type":"session_event","event":{"type":"tool/result",'
        '"data":{"message":{"content":[{"type":"tool-result","content":'
        '[{"type":"text","text":"ok"}]}]}}}}'
    )
    ended = parser.feed(
        '{"type":"session_event","event":{"type":"turn/end",'
        '"data":{"reason":{"kind":"completed"}}}}'
    )

    assert call[0].kind == "tool_call"
    assert call[0].title == "Tool call: shell"
    assert result[0].kind == "tool_result"
    assert result[0].content == "ok"
    assert ended[0].kind == "completed"
    assert ended[0].content == "completed"
