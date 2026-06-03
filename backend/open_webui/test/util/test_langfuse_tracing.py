import asyncio
import time

from open_webui.utils.telemetry.langfuse_tracing import build_generation_payload
import open_webui.utils.telemetry.langfuse_tracing as lt


def _ctx(**over):
    ctx = {
        "metadata": {
            "chat_id": "chat-123",
            "message_id": "msg-abc",
            "user_id": "user-9",
            "model": "gpt-4o-mini",
            "start_time": time.time() - 2.0,
        },
        "user": None,
        "form_data": {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
        },
        "assistant_message": {
            "content": "hello!",
            "usage": {"input_tokens": 11, "output_tokens": 5, "total_tokens": 16},
        },
    }
    ctx.update(over)
    return ctx


def test_payload_maps_core_fields():
    p = build_generation_payload(_ctx())
    assert p["model"] == "gpt-4o-mini"
    assert p["user_id"] == "user-9"
    assert p["session_id"] == "chat-123"
    assert p["trace_id_seed"] == "msg-abc"
    assert p["output"] == "hello!"
    assert p["usage_details"] == {"input": 11, "output": 5, "total": 16}
    assert p["latency"] is not None and p["latency"] >= 1.5


def test_payload_skips_channel_chats():
    assert build_generation_payload(_ctx(metadata={"chat_id": "channel:x"})) is None


def test_payload_none_without_usage():
    ctx = _ctx()
    ctx["assistant_message"] = {"content": "x"}
    assert build_generation_payload(ctx)["usage_details"] is None


def test_trace_chat_turn_is_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(lt, "ENABLE_LANGFUSE", False)

    def _boom():
        raise AssertionError("client should not init when disabled")

    monkeypatch.setattr(lt, "get_langfuse_client", _boom)
    # must not raise and must not init the client
    asyncio.run(lt.trace_chat_turn(_ctx()))


def test_emit_swallows_client_errors(monkeypatch):
    class Boom:
        def start_observation(self, **k):
            raise RuntimeError("network down")

    monkeypatch.setattr(lt, "ENABLE_LANGFUSE", True)
    monkeypatch.setattr(lt, "get_langfuse_client", lambda: Boom())
    # _emit must never raise
    lt._emit(build_generation_payload(_ctx()))
