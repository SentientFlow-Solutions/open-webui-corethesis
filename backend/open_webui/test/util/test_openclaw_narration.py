from open_webui.routers.openclaw import _narration_line, _sse_chunk

import json


def test_tick_zero_connects():
    assert _narration_line(0, 0.0) == "Connecting to OpenClaw agent…\n"


def test_tick_one_working():
    assert _narration_line(1, 5.2) == "Agent is working on your request…\n"


def test_elapsed_updates_every_third_tick():
    assert _narration_line(3, 15.4) == "Still working — 15s elapsed…\n"
    assert _narration_line(6, 30.9) == "Still working — 30s elapsed…\n"
    assert _narration_line(9, 45.0) == "Still working — 45s elapsed…\n"


def test_silent_ticks_return_none():
    for tick in (2, 4, 5, 7, 8):
        assert _narration_line(tick, tick * 5.0) is None


def test_sse_chunk_reasoning_delta():
    raw = _sse_chunk("id1", 123, "openclaw", "", reasoning="Connecting…\n")
    assert raw.startswith("data: ")
    payload = json.loads(raw[len("data: ") :])
    delta = payload["choices"][0]["delta"]
    assert delta == {"reasoning_content": "Connecting…\n"}


def test_sse_chunk_none_reasoning_is_empty_keepalive():
    raw = _sse_chunk("id1", 123, "openclaw", "", reasoning=None)
    payload = json.loads(raw[len("data: ") :])
    assert payload["choices"][0]["delta"] == {}
