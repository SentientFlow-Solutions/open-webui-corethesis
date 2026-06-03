from open_webui.routers.langfuse import aggregate_overview, aggregate_chat


def test_aggregate_overview_sums_daily_and_latency():
    daily = [
        {
            "date": "2026-06-02",
            "countTraces": 3,
            "totalCost": 0.01,
            "usage": [
                {"model": "gpt-4o-mini", "totalUsage": 120, "totalCost": 0.01, "countTraces": 3}
            ],
        },
        {
            "date": "2026-06-03",
            "countTraces": 2,
            "totalCost": 0.02,
            "usage": [
                {"model": "gpt-4o-mini", "totalUsage": 80, "totalCost": 0.02, "countTraces": 2}
            ],
        },
    ]
    observations = [{"latency": 1.0}, {"latency": 3.0}, {"latency": 0}]
    out = aggregate_overview(daily, observations)
    assert out["totals"]["tokens"] == 200
    assert round(out["totals"]["cost"], 4) == 0.03
    assert out["totals"]["requests"] == 5
    assert out["totals"]["avgLatency"] == 2.0  # (1+3)/2, zeros ignored
    assert out["byModel"][0]["model"] == "gpt-4o-mini"
    assert out["byModel"][0]["tokens"] == 200
    assert len(out["daily"]) == 2


def test_aggregate_chat_from_traces():
    traces = [
        {"totalCost": 0.01, "latency": 2.0},
        {"totalCost": 0.02, "latency": 4.0},
    ]
    out = aggregate_chat(traces, total_items=2)
    assert out["turns"] == 2
    assert round(out["cost"], 4) == 0.03
    assert out["avgLatency"] == 3.0


def test_aggregate_chat_handles_empty():
    out = aggregate_chat([], total_items=None)
    assert out == {"turns": 0, "cost": 0.0, "avgLatency": 0.0}
