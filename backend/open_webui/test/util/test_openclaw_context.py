from open_webui.routers.openclaw import _messages_to_transcript, _messages_to_message


def test_single_user_turn_is_bare_message():
    # First turn: no prior context, so just the message text (no transcript chrome).
    msgs = [{"role": "user", "content": "hello there"}]
    assert _messages_to_transcript(msgs) == "hello there"


def test_multi_turn_includes_full_history_then_latest():
    msgs = [
        {"role": "user", "content": "my name is Farhan"},
        {"role": "assistant", "content": "Nice to meet you, Farhan."},
        {"role": "user", "content": "what is my name?"},
    ]
    out = _messages_to_transcript(msgs)
    # earlier turns must be present (this is the bug: they were dropped before)
    assert "my name is Farhan" in out
    assert "Nice to meet you, Farhan." in out
    # latest user message present
    assert "what is my name?" in out
    # history comes before the latest message
    assert out.index("my name is Farhan") < out.index("what is my name?")
    # both roles are labelled
    assert "User:" in out and "Assistant:" in out


def test_system_messages_excluded_agent_has_own_persona():
    msgs = [
        {"role": "system", "content": "You are a generic assistant."},
        {"role": "user", "content": "hi"},
    ]
    out = _messages_to_transcript(msgs)
    assert out == "hi"
    assert "generic assistant" not in out


def test_content_parts_are_inlined():
    msgs = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ok"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe "},
                {"type": "text", "text": "this"},
            ],
        },
    ]
    out = _messages_to_transcript(msgs)
    assert "describe \nthis" in out or "describe this" in out or "describe" in out and "this" in out
    assert "first" in out and "ok" in out


def test_latest_user_message_helper_still_works():
    # the original single-message helper is retained for the empty-check guard
    msgs = [{"role": "user", "content": "abc"}]
    assert _messages_to_message(msgs).strip() == "abc"
