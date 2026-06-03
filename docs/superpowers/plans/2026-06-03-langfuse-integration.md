# Langfuse Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trace every chat turn to the self-hosted Langfuse and give each user in-app usage views (an overall "My Usage" sidebar page + a per-chat footer bar), matching Open WebUI's design and never blocking or breaking a chat.

**Architecture:** A backend tracing module emits one Langfuse trace+generation per chat turn from the chat middleware (fire-and-forget, after usage normalization). A backend proxy router queries Langfuse's v1 public API server-side (keys never reach the browser), forcing the userId from the authenticated session. The frontend adds a sidebar entry → `/usage` dashboard and an inline per-chat footer, both fed by the proxy.

**Tech Stack:** Python/FastAPI, `langfuse==3.7.0` Python SDK (verified against server v3.163.0), `httpx` (existing dep), SvelteKit, chart.js 4.5.0 (existing).

**Verified facts (probed 2026-06-03):** SDK pattern `client.start_observation(as_type="generation", trace_context={"trace_id": ...}, model=, input=, output=, usage_details={"input","output","total"})` → `.update_trace(user_id=, session_id=)` → `.end(end_time=now_ns + latency*1e9)`. Cost auto-computes from model+usage. Reads: `metrics/daily?userId=` (tokens/cost/counts/by-model), `observations?userId=&type=GENERATION` (latency), `traces?sessionId=&userId=` (per-chat cost/turns/latency; `/observations` does NOT honor `sessionId`). Basic auth `(public_key, secret_key)`.

---

## File Structure

**Backend (create):**
- `backend/open_webui/utils/telemetry/langfuse_tracing.py` — tracing client singleton + `build_generation_payload(ctx)` (pure) + `trace_chat_turn(ctx)` (fire-and-forget). *Named `_tracing` to never shadow the `langfuse` pip package.*
- `backend/open_webui/routers/langfuse.py` — proxy router: `/config`, `/usage/overview`, `/usage/chat/{chat_id}`; pure aggregators `aggregate_overview(...)`, `aggregate_chat(...)`.
- `backend/open_webui/test/util/test_langfuse_tracing.py`, `backend/open_webui/test/util/test_langfuse_router.py` — unit tests for the pure functions.

**Backend (modify):**
- `backend/open_webui/env.py` — `LANGFUSE_PUBLIC_KEY/SECRET_KEY/BASE_URL`, `ENABLE_LANGFUSE`.
- `backend/open_webui/main.py` — router import + mount; `enable_langfuse` in the `/api/config` features; `metadata["start_time"]` for latency.
- `backend/open_webui/utils/middleware.py` — import + one `await trace_chat_turn(ctx)` call in each of the two response handlers.
- `backend/pyproject.toml`, `backend/requirements.txt` — add `langfuse==3.7.0`.

**Frontend (create):**
- `src/lib/apis/langfuse/index.ts` — typed client for the proxy.
- `src/routes/(app)/usage/+page.svelte` — route page.
- `src/lib/components/usage/Dashboard.svelte` — overall dashboard.
- `src/lib/components/chat/LangfuseUsageBar.svelte` — per-chat footer.

**Frontend (modify):**
- `src/lib/components/layout/Sidebar.svelte` — 5 touch points for the `usage` entry.
- `src/lib/components/chat/Chat.svelte` — render `<LangfuseUsageBar>` above `<MessageInput>`.
- `src/lib/i18n/locales/en-US/translation.json` — new keys.

**Frontend testing note:** the repo has no Svelte component test harness; frontend tasks are verified with `npx svelte-check` (type/markup) + `npx vite build` (compiles) + a runtime smoke check, not vitest. Backend tasks use real pytest TDD.

---

## Phase 1 — Backend tracing

### Task 1: Langfuse env vars

**Files:**
- Modify: `backend/open_webui/env.py` (append a new config block; place near the OTEL block ~line 1060)

- [ ] **Step 1: Add the env block**

Append after the OTEL settings region in `env.py`:

```python
####################################
# Langfuse (observability + usage views)
####################################

LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
LANGFUSE_BASE_URL = os.environ.get("LANGFUSE_BASE_URL", "").rstrip("/")

# Only enable when all three are present; an explicit ENABLE_LANGFUSE=false can
# still disable it, but it can never be force-enabled without keys.
_LANGFUSE_CONFIGURED = bool(
    LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL
)
ENABLE_LANGFUSE = (
    os.environ.get("ENABLE_LANGFUSE", str(_LANGFUSE_CONFIGURED)).lower() == "true"
    and _LANGFUSE_CONFIGURED
)
```

- [ ] **Step 2: Verify it imports**

Run: `cd backend && python -c "from open_webui.env import ENABLE_LANGFUSE, LANGFUSE_BASE_URL; print(ENABLE_LANGFUSE, repr(LANGFUSE_BASE_URL))"`
Expected: `False ''` (no keys set locally).

- [ ] **Step 3: Commit**

```bash
git add backend/open_webui/env.py
git commit -m "feat(langfuse): add LANGFUSE_* env config"
```

---

### Task 2: `build_generation_payload` (pure mapping ctx → Langfuse kwargs)

**Files:**
- Create: `backend/open_webui/utils/telemetry/langfuse_tracing.py`
- Test: `backend/open_webui/test/util/test_langfuse_tracing.py`

- [ ] **Step 1: Write the failing test**

Create `backend/open_webui/test/util/test_langfuse_tracing.py`:

```python
import time
from open_webui.utils.telemetry.langfuse_tracing import build_generation_payload


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
        "form_data": {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest open_webui/test/util/test_langfuse_tracing.py -v`
Expected: FAIL — `ModuleNotFoundError: open_webui.utils.telemetry.langfuse_tracing`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/open_webui/utils/telemetry/langfuse_tracing.py`:

```python
"""Langfuse tracing for chat completions.

Emits one Langfuse trace + generation per chat turn so token/cost/latency usage
is visible in Langfuse and in Open WebUI's in-app usage views.

Opt-in: when LANGFUSE_* env vars are unset, every entry point is a no-op.
Failures are swallowed — tracing must never delay or break a chat.

NOTE: this module is intentionally NOT named ``langfuse.py`` so it never shadows
the ``langfuse`` pip package.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from open_webui.env import (
    ENABLE_LANGFUSE,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_SECRET_KEY,
    LANGFUSE_BASE_URL,
)

log = logging.getLogger(__name__)

_client = None
_init_attempted = False


def _is_traceable_chat(chat_id: Optional[str]) -> bool:
    if not chat_id:
        return False
    return not (chat_id.startswith("local:") or chat_id.startswith("channel:"))


def build_generation_payload(ctx: dict) -> Optional[dict]:
    """Pure: turn a middleware ``ctx`` into kwargs for a Langfuse generation.

    Returns None for untraceable chats. ``usage_details`` is None when the turn
    reported no token usage.
    """
    metadata = ctx.get("metadata") or {}
    chat_id = metadata.get("chat_id")
    if not _is_traceable_chat(chat_id):
        return None

    assistant_message = ctx.get("assistant_message") or {}
    usage = assistant_message.get("usage") or {}
    usage_details = {}
    if usage.get("input_tokens") is not None:
        usage_details["input"] = int(usage["input_tokens"])
    if usage.get("output_tokens") is not None:
        usage_details["output"] = int(usage["output_tokens"])
    if usage.get("total_tokens") is not None:
        usage_details["total"] = int(usage["total_tokens"])

    model = (
        metadata.get("model")
        or (ctx.get("model") or {}).get("id")
        or (ctx.get("form_data") or {}).get("model")
    )
    if isinstance(model, dict):
        model = model.get("id")

    user = ctx.get("user")
    user_id = metadata.get("user_id") or getattr(user, "id", None)
    message_id = metadata.get("message_id")

    start_time = metadata.get("start_time")
    latency = None
    if isinstance(start_time, (int, float)):
        latency = max(0.0, time.time() - start_time)

    return {
        "trace_id_seed": message_id or None,
        "name": "chat-completion",
        "model": model,
        "input": (ctx.get("form_data") or {}).get("messages"),
        "output": assistant_message.get("content"),
        "usage_details": usage_details or None,
        "metadata": {"message_id": message_id, "chat_id": chat_id, "source": "open-webui"},
        "user_id": user_id,
        "session_id": chat_id,
        "latency": latency,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest open_webui/test/util/test_langfuse_tracing.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/open_webui/utils/telemetry/langfuse_tracing.py backend/open_webui/test/util/test_langfuse_tracing.py
git commit -m "feat(langfuse): pure ctx->generation payload mapper + tests"
```

---

### Task 3: Client singleton + `trace_chat_turn` (fire-and-forget, error-swallowing)

**Files:**
- Modify: `backend/open_webui/utils/telemetry/langfuse_tracing.py`
- Test: `backend/open_webui/test/util/test_langfuse_tracing.py`

- [ ] **Step 1: Write the failing test** — append to the test file:

```python
import open_webui.utils.telemetry.langfuse_tracing as lt


def test_trace_chat_turn_is_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(lt, "ENABLE_LANGFUSE", False)
    import asyncio
    # must not raise and must not call the client
    monkeypatch.setattr(lt, "get_langfuse_client", lambda: (_ for _ in ()).throw(AssertionError("should not init")))
    asyncio.get_event_loop().run_until_complete(lt.trace_chat_turn(_ctx()))


def test_emit_swallows_client_errors(monkeypatch):
    class Boom:
        def start_observation(self, **k):
            raise RuntimeError("network down")
    monkeypatch.setattr(lt, "ENABLE_LANGFUSE", True)
    monkeypatch.setattr(lt, "get_langfuse_client", lambda: Boom())
    # _emit must never raise
    lt._emit(build_generation_payload(_ctx()))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest open_webui/test/util/test_langfuse_tracing.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'get_langfuse_client'` / `_emit`.

- [ ] **Step 3: Add the implementation** — append to `langfuse_tracing.py`:

```python
def get_langfuse_client():
    """Lazily build a singleton Langfuse client, or None if disabled/unavailable."""
    global _client, _init_attempted
    if not ENABLE_LANGFUSE:
        return None
    if _init_attempted:
        return _client
    _init_attempted = True
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=LANGFUSE_PUBLIC_KEY,
            secret_key=LANGFUSE_SECRET_KEY,
            host=LANGFUSE_BASE_URL,
            environment="open-webui",
        )
    except Exception as e:  # pragma: no cover - defensive
        log.warning(f"Langfuse client init failed; tracing disabled: {e}")
        _client = None
    return _client


def _emit(payload: dict) -> None:
    """Synchronous emit — runs in a worker thread; never raises."""
    client = get_langfuse_client()
    if client is None:
        return
    try:
        from langfuse import Langfuse

        trace_context = None
        seed = payload.get("trace_id_seed")
        if seed:
            trace_context = {"trace_id": Langfuse.create_trace_id(seed=seed)}

        gen = client.start_observation(
            as_type="generation",
            trace_context=trace_context,
            name=payload["name"],
            model=payload.get("model"),
            input=payload.get("input"),
            output=payload.get("output"),
            usage_details=payload.get("usage_details"),
            metadata=payload.get("metadata"),
        )
        gen.update_trace(
            name="chat",
            user_id=payload.get("user_id"),
            session_id=payload.get("session_id"),
            input=payload.get("input"),
            output=payload.get("output"),
            tags=["open-webui"],
        )
        latency = payload.get("latency")
        if isinstance(latency, (int, float)) and latency > 0:
            gen.end(end_time=time.time_ns() + int(latency * 1_000_000_000))
        else:
            gen.end()
    except Exception as e:
        log.debug(f"Langfuse emit failed (ignored): {e}")


async def trace_chat_turn(ctx: dict) -> None:
    """Fire-and-forget: emit a Langfuse trace for this chat turn. Never raises."""
    if not ENABLE_LANGFUSE:
        return
    try:
        payload = build_generation_payload(ctx)
        if payload is None or not payload.get("usage_details"):
            return
        asyncio.get_running_loop().run_in_executor(None, _emit, payload)
    except Exception as e:  # pragma: no cover - defensive
        log.debug(f"trace_chat_turn skipped (ignored): {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest open_webui/test/util/test_langfuse_tracing.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/open_webui/utils/telemetry/langfuse_tracing.py backend/open_webui/test/util/test_langfuse_tracing.py
git commit -m "feat(langfuse): client singleton + fire-and-forget trace_chat_turn"
```

---

### Task 4: Add the `langfuse` dependency

**Files:**
- Modify: `backend/pyproject.toml` (insert after `"mcp==1.26.0",` ~line 53)
- Modify: `backend/requirements.txt` (append in the same logical area, mirroring pyproject)

- [ ] **Step 1: Edit `pyproject.toml`** — add the line right after `"mcp==1.26.0",`:

```python
    "mcp==1.26.0",

    "langfuse==3.7.0",
```

- [ ] **Step 2: Edit `requirements.txt`** — add a matching line (find the `mcp==1.26.0` line and add below it):

```
langfuse==3.7.0
```

- [ ] **Step 3: Verify install resolves locally** (optional but recommended)

Run: `cd backend && python -c "import importlib.metadata as m; print('declared langfuse==3.7.0')"`
Expected: prints the line (real install happens on the box via `uv sync`).

- [ ] **Step 4: Commit**

```bash
git add backend/pyproject.toml backend/requirements.txt
git commit -m "build(langfuse): pin langfuse==3.7.0"
```

---

### Task 5: Capture per-turn start_time in metadata

**Files:**
- Modify: `backend/open_webui/main.py` (the metadata dict assembled in `chat_completion`, ~lines 1766-1794)

- [ ] **Step 1: Add `start_time`** — inside the metadata dict construction, add a key:

```python
        "start_time": time.time(),
```

(Place it among the other metadata keys. `time` is already imported in `main.py`; if not, add `import time` at the top.)

- [ ] **Step 2: Verify the app still imports**

Run: `cd backend && python -c "import open_webui.main" 2>&1 | tail -3`
Expected: no traceback (warnings about optional deps are fine).

- [ ] **Step 3: Commit**

```bash
git add backend/open_webui/main.py
git commit -m "feat(langfuse): stamp per-turn start_time in chat metadata"
```

---

### Task 6: Hook tracing into the chat middleware

**Files:**
- Modify: `backend/open_webui/utils/middleware.py` (import near the top; one call in each handler)

- [ ] **Step 1: Add the import** near the other `open_webui.utils...` imports at the top of `middleware.py`:

```python
from open_webui.utils.telemetry.langfuse_tracing import trace_chat_turn
```

- [ ] **Step 2: Hook the non-streaming handler** — in `non_streaming_chat_response_handler`, immediately after the `ctx['assistant_message'] = { ... }` assignment (~line 3533) and before `await outlet_filter_handler(ctx)`:

```python
                    await trace_chat_turn(ctx)
```

- [ ] **Step 3: Hook the streaming handler** — in `streaming_chat_response_handler`, immediately after the `ctx['assistant_message'] = { ... }` assignment (~line 5108) and before `await outlet_filter_handler(ctx)`:

```python
                await trace_chat_turn(ctx)
```

(Match the surrounding indentation exactly at each site.)

- [ ] **Step 4: Verify import + that hooks are reachable**

Run: `cd backend && python -c "import open_webui.utils.middleware as m; print('ok')"`
Expected: `ok`.
Run: `cd backend && grep -n "await trace_chat_turn(ctx)" open_webui/utils/middleware.py`
Expected: two matches.

- [ ] **Step 5: Commit**

```bash
git add backend/open_webui/utils/middleware.py
git commit -m "feat(langfuse): emit a trace per chat turn from chat middleware"
```

---

## Phase 2 — Backend usage proxy

### Task 7: Pure aggregators for usage payloads

**Files:**
- Create: `backend/open_webui/routers/langfuse.py` (aggregators only in this task)
- Test: `backend/open_webui/test/util/test_langfuse_router.py`

- [ ] **Step 1: Write the failing test**

Create `backend/open_webui/test/util/test_langfuse_router.py`:

```python
from open_webui.routers.langfuse import aggregate_overview, aggregate_chat


def test_aggregate_overview_sums_daily_and_latency():
    daily = [
        {"date": "2026-06-02", "countTraces": 3, "totalCost": 0.01,
         "usage": [{"model": "gpt-4o-mini", "totalUsage": 120, "totalCost": 0.01, "countTraces": 3}]},
        {"date": "2026-06-03", "countTraces": 2, "totalCost": 0.02,
         "usage": [{"model": "gpt-4o-mini", "totalUsage": 80, "totalCost": 0.02, "countTraces": 2}]},
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest open_webui/test/util/test_langfuse_router.py -v`
Expected: FAIL — `ModuleNotFoundError: open_webui.routers.langfuse`.

- [ ] **Step 3: Write the aggregators**

Create `backend/open_webui/routers/langfuse.py`:

```python
"""Per-user Langfuse usage proxy.

Queries the self-hosted Langfuse v1 public API server-side (keys never reach the
browser) and always scopes results to the authenticated user. Failures degrade
to empty/zeroed payloads so the UI never breaks.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)


def _avg_latency(rows: list[dict]) -> float:
    lats = [r.get("latency") for r in rows if isinstance(r.get("latency"), (int, float)) and r.get("latency")]
    return round(sum(lats) / len(lats), 3) if lats else 0.0


def aggregate_overview(daily: list[dict], observations: list[dict]) -> dict:
    """Build the overall usage payload from daily-metrics + observations rows."""
    tokens = cost = requests = 0
    by_model: dict[str, dict] = {}
    series: list[dict] = []
    for day in daily or []:
        day_tokens = 0
        for u in day.get("usage") or []:
            t = int(u.get("totalUsage") or 0)
            c = float(u.get("totalCost") or 0)
            r = int(u.get("countTraces") or 0)
            day_tokens += t
            m = u.get("model") or "unknown"
            agg = by_model.setdefault(m, {"model": m, "tokens": 0, "cost": 0.0, "requests": 0})
            agg["tokens"] += t
            agg["cost"] += c
            agg["requests"] += r
        tokens += day_tokens
        cost += float(day.get("totalCost") or 0)
        requests += int(day.get("countTraces") or 0)
        series.append({"date": day.get("date"), "tokens": day_tokens, "cost": float(day.get("totalCost") or 0), "requests": int(day.get("countTraces") or 0)})
    by_model_list = sorted(by_model.values(), key=lambda x: x["tokens"], reverse=True)
    for m in by_model_list:
        m["cost"] = round(m["cost"], 6)
    return {
        "totals": {
            "tokens": tokens,
            "cost": round(cost, 6),
            "requests": requests,
            "avgLatency": _avg_latency(observations),
        },
        "byModel": by_model_list,
        "daily": sorted(series, key=lambda x: x["date"] or ""),
    }


def aggregate_chat(traces: list[dict], total_items: Optional[int] = None) -> dict:
    """Build the per-chat payload from traces filtered by sessionId."""
    cost = sum(float(t.get("totalCost") or 0) for t in traces or [])
    return {
        "turns": int(total_items if total_items is not None else len(traces or [])),
        "cost": round(cost, 6),
        "avgLatency": _avg_latency(traces or []),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest open_webui/test/util/test_langfuse_router.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/open_webui/routers/langfuse.py backend/open_webui/test/util/test_langfuse_router.py
git commit -m "feat(langfuse): pure usage aggregators + tests"
```

---

### Task 8: Langfuse HTTP client + the three endpoints

**Files:**
- Modify: `backend/open_webui/routers/langfuse.py`

- [ ] **Step 1: Add imports + HTTP helper + endpoints** — append to `routers/langfuse.py`:

```python
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends

from open_webui.env import (
    ENABLE_LANGFUSE,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_SECRET_KEY,
    LANGFUSE_BASE_URL,
)
from open_webui.utils.auth import get_verified_user

router = APIRouter()

# tiny TTL cache to absorb polling and respect Langfuse rate limits
_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 20.0


async def _lf_get(path: str, params: dict) -> dict:
    """GET against the Langfuse public API with Basic auth. Returns {} on error."""
    if not ENABLE_LANGFUSE:
        return {}
    key = f"{path}?{sorted(params.items())}"
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{LANGFUSE_BASE_URL}{path}",
                params={k: v for k, v in params.items() if v is not None},
                auth=(LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY),
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.debug(f"Langfuse query failed ({path}): {e}")
        return {}
    _cache[key] = (now, data)
    return data


@router.get("/config")
async def langfuse_config(user=Depends(get_verified_user)):
    return {"enabled": bool(ENABLE_LANGFUSE)}


@router.get("/usage/overview")
async def usage_overview(days: int = 30, user=Depends(get_verified_user)):
    if not ENABLE_LANGFUSE:
        return {"enabled": False, "totals": {"tokens": 0, "cost": 0, "requests": 0, "avgLatency": 0}, "byModel": [], "daily": []}
    days = max(1, min(int(days or 30), 365))
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    daily = await _lf_get(
        "/api/public/metrics/daily",
        {"userId": user.id, "fromTimestamp": start, "toTimestamp": end, "limit": days},
    )
    obs = await _lf_get(
        "/api/public/observations",
        {"userId": user.id, "type": "GENERATION", "fromStartTime": start, "limit": 100},
    )
    out = aggregate_overview(daily.get("data", []), obs.get("data", []))
    out["enabled"] = True
    return out


@router.get("/usage/chat/{chat_id}")
async def usage_chat(chat_id: str, user=Depends(get_verified_user)):
    if not ENABLE_LANGFUSE:
        return {"enabled": False, "turns": 0, "cost": 0, "avgLatency": 0}
    # userId is forced from the session — a user can never read another's data
    data = await _lf_get(
        "/api/public/traces",
        {"sessionId": chat_id, "userId": user.id, "limit": 100},
    )
    total_items = (data.get("meta") or {}).get("totalItems")
    out = aggregate_chat(data.get("data", []), total_items=total_items)
    out["enabled"] = True
    return out
```

- [ ] **Step 2: Verify it imports**

Run: `cd backend && python -c "from open_webui.routers.langfuse import router; print([r.path for r in router.routes])"`
Expected: `['/config', '/usage/overview', '/usage/chat/{chat_id}']`.

- [ ] **Step 3: Re-run the aggregator tests (still green)**

Run: `cd backend && python -m pytest open_webui/test/util/test_langfuse_router.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/open_webui/routers/langfuse.py
git commit -m "feat(langfuse): usage proxy endpoints (config, overview, per-chat)"
```

---

### Task 9: Mount the router + expose the feature flag

**Files:**
- Modify: `backend/open_webui/main.py`

- [ ] **Step 1: Import the router** — add `langfuse,` to the `from open_webui.routers import ( ... )` tuple (after `openclaw,` ~line 109):

```python
    openclaw,
    langfuse,
```

- [ ] **Step 2: Import the flag** — add `ENABLE_LANGFUSE` to the existing `from open_webui.env import (...)` block in `main.py`:

```python
    ENABLE_LANGFUSE,
```

- [ ] **Step 3: Mount the router** — after the openclaw include (~line 1460):

```python
app.include_router(langfuse.router, prefix="/api/v1/langfuse", tags=["langfuse"])
```

- [ ] **Step 4: Expose the feature flag** — inside the `@app.get("/api/config")` `features` dict, in the authenticated (`if user is not None`) block, add:

```python
                    "enable_langfuse": ENABLE_LANGFUSE,
```

- [ ] **Step 5: Verify**

Run: `cd backend && python -c "import open_webui.main as m; paths=[r.path for r in m.app.routes if 'langfuse' in getattr(r,'path','')]; print(paths)"`
Expected: includes `/api/v1/langfuse/config`, `/api/v1/langfuse/usage/overview`, `/api/v1/langfuse/usage/chat/{chat_id}`.

- [ ] **Step 6: Run the full backend langfuse test suite**

Run: `cd backend && python -m pytest open_webui/test/util/test_langfuse_tracing.py open_webui/test/util/test_langfuse_router.py -v`
Expected: PASS (7 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/open_webui/main.py
git commit -m "feat(langfuse): mount usage proxy + expose enable_langfuse feature flag"
```

---

## Phase 3 — Frontend

### Task 10: API client `src/lib/apis/langfuse/index.ts`

**Files:**
- Create: `src/lib/apis/langfuse/index.ts`

- [ ] **Step 1: Write the client** (mirrors `apis/openclaw/index.ts`):

```typescript
import { WEBUI_API_BASE_URL } from '$lib/constants';

export type LangfuseConfig = { enabled: boolean };

export type UsageTotals = {
	tokens: number;
	cost: number;
	requests: number;
	avgLatency: number;
};

export type ModelUsage = { model: string; tokens: number; cost: number; requests: number };
export type DailyUsage = { date: string; tokens: number; cost: number; requests: number };

export type UsageOverview = {
	enabled: boolean;
	totals: UsageTotals;
	byModel: ModelUsage[];
	daily: DailyUsage[];
};

export type ChatUsage = { enabled: boolean; turns: number; cost: number; avgLatency: number };

const handle = async (res: Response) => {
	if (!res.ok) {
		let detail = res.statusText;
		try {
			const body = await res.json();
			detail = body?.detail ?? detail;
		} catch {
			/* ignore */
		}
		throw new Error(detail || `Request failed: ${res.status}`);
	}
	return res.json();
};

export const getLangfuseConfig = async (token: string): Promise<LangfuseConfig> => {
	return fetch(`${WEBUI_API_BASE_URL}/langfuse/config`, {
		headers: { authorization: `Bearer ${token}` }
	}).then(handle);
};

export const getUsageOverview = async (token: string, days = 30): Promise<UsageOverview> => {
	return fetch(`${WEBUI_API_BASE_URL}/langfuse/usage/overview?days=${days}`, {
		headers: { authorization: `Bearer ${token}` }
	}).then(handle);
};

export const getChatUsage = async (token: string, chatId: string): Promise<ChatUsage> => {
	return fetch(`${WEBUI_API_BASE_URL}/langfuse/usage/chat/${encodeURIComponent(chatId)}`, {
		headers: { authorization: `Bearer ${token}` }
	}).then(handle);
};
```

- [ ] **Step 2: Type-check**

Run: `npx svelte-check --tsconfig ./tsconfig.json 2>&1 | grep -i langfuse || echo "no langfuse type errors"`
Expected: `no langfuse type errors`.

- [ ] **Step 3: Commit**

```bash
git add src/lib/apis/langfuse/index.ts
git commit -m "feat(langfuse): frontend API client for the usage proxy"
```

---

### Task 11: i18n keys

**Files:**
- Modify: `src/lib/i18n/locales/en-US/translation.json`

- [ ] **Step 1: Add keys** — insert these keys in their alphabetical positions (empty string = falls back to the key text):

```json
	"Cost": "",
	"My Usage": "",
	"Requests": "",
	"Tokens": "",
	"Usage": "",
	"Avg latency": "",
	"Usage by model": "",
	"No usage data yet": ""
```

(Place each key alphabetically; `"Tokens"`/`"Usage"` may already exist — if so, leave the existing entry and only add the missing ones. Do not create duplicate keys.)

- [ ] **Step 2: Validate JSON**

Run: `node -e "JSON.parse(require('fs').readFileSync('src/lib/i18n/locales/en-US/translation.json','utf8')); console.log('valid json')"`
Expected: `valid json`.

- [ ] **Step 3: Commit**

```bash
git add src/lib/i18n/locales/en-US/translation.json
git commit -m "feat(langfuse): i18n keys for usage views"
```

---

### Task 12: Sidebar "My Usage" entry (5 touch points)

**Files:**
- Modify: `src/lib/components/layout/Sidebar.svelte`

- [ ] **Step 1: Pinned items** (~line 79):

```javascript
	const DEFAULT_PINNED_ITEMS = ['notes', 'workspace', 'openclaw', 'usage'];
```

- [ ] **Step 2: Visibility** — in `isMenuItemVisible`, add a case after the `openclaw` case (~line 142). Visible to every signed-in user, gated on the feature flag:

```javascript
			case 'usage':
				// Per-user Langfuse usage. Visible to all signed-in users when the
				// Langfuse integration is configured on the backend.
				return $config?.features?.enable_langfuse ?? false;
```

- [ ] **Step 3: Menu meta** — in the `getMenuItemMeta` `items` object, add after the `openclaw` entry (~line 154; add a comma to the openclaw line):

```javascript
			openclaw: { label: 'OpenClaw', href: '/?models=openclaw', iconType: 'openclaw' },
			usage: { label: 'My Usage', href: '/usage', iconType: 'usage' }
```

- [ ] **Step 4: Collapsed icon** — in the mini-sidebar icon block, add after the openclaw `{:else if itemId === 'openclaw'}` SVG (~line 948), `stroke-width="1.5"` (chart-bar icon):

```svelte
								{:else if itemId === 'usage'}
									<svg
										xmlns="http://www.w3.org/2000/svg"
										fill="none"
										viewBox="0 0 24 24"
										stroke-width="1.5"
										stroke="currentColor"
										class="size-4.5"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z"
										/>
									</svg>
```

- [ ] **Step 5: Expanded icon** — in the full-sidebar icon block, add after the openclaw `{:else if itemId === 'openclaw'}` SVG (~line 1210), identical path but `stroke-width="2"`:

```svelte
								{:else if itemId === 'usage'}
									<svg
										xmlns="http://www.w3.org/2000/svg"
										fill="none"
										viewBox="0 0 24 24"
										stroke-width="2"
										stroke="currentColor"
										class="size-4.5"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z"
										/>
									</svg>
```

- [ ] **Step 6: Type-check / build**

Run: `npx svelte-check --tsconfig ./tsconfig.json 2>&1 | grep -iE "Sidebar.svelte" || echo "no Sidebar errors"`
Expected: `no Sidebar errors`.

- [ ] **Step 7: Commit**

```bash
git add src/lib/components/layout/Sidebar.svelte
git commit -m "feat(langfuse): sidebar 'My Usage' entry"
```

---

### Task 13: Overall usage dashboard + route

**Files:**
- Create: `src/lib/components/usage/Dashboard.svelte`
- Create: `src/routes/(app)/usage/+page.svelte`

- [ ] **Step 1: Dashboard component**

Create `src/lib/components/usage/Dashboard.svelte`:

```svelte
<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { config } from '$lib/stores';
	import { getUsageOverview, type UsageOverview } from '$lib/apis/langfuse';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { formatNumber } from '$lib/utils';

	const i18n: any = getContext('i18n');

	let loading = true;
	let data: UsageOverview | null = null;
	let days = 30;

	const fmtCost = (n: number) => (n ? `$${n.toFixed(n < 0.01 ? 4 : 2)}` : '$0');
	const fmtLatency = (n: number) => (n ? `${n.toFixed(1)}s` : '—');

	const load = async () => {
		loading = true;
		const token = localStorage.token;
		try {
			data = await getUsageOverview(token, days);
		} catch (e) {
			data = null;
		}
		loading = false;
	};

	onMount(async () => {
		if (!($config?.features?.enable_langfuse ?? false)) {
			await goto('/');
			return;
		}
		await load();
	});

	$: maxModelTokens = Math.max(1, ...(data?.byModel ?? []).map((m) => m.tokens));
</script>

<div class="flex flex-col w-full h-full p-4 md:p-6 overflow-y-auto">
	<div class="flex items-center justify-between mb-4">
		<div class="text-2xl font-medium">{$i18n.t('My Usage')}</div>
		<select
			class="text-sm bg-transparent border border-gray-100 dark:border-gray-850 rounded-lg px-2 py-1 outline-none"
			bind:value={days}
			on:change={load}
		>
			<option value={7}>7d</option>
			<option value={30}>30d</option>
			<option value={90}>90d</option>
		</select>
	</div>

	{#if loading}
		<div class="flex justify-center items-center h-40"><Spinner /></div>
	{:else if !data || (data.totals.requests === 0 && data.totals.tokens === 0)}
		<div class="flex justify-center items-center h-40 text-gray-500 text-sm">
			{$i18n.t('No usage data yet')}
		</div>
	{:else}
		<div class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
			{#each [['Tokens', formatNumber(data.totals.tokens)], ['Cost', fmtCost(data.totals.cost)], ['Requests', formatNumber(data.totals.requests)], ['Avg latency', fmtLatency(data.totals.avgLatency)]] as [label, value]}
				<div class="bg-gray-50 dark:bg-gray-850 rounded-2xl p-4">
					<div class="text-xs text-gray-500 mb-1">{$i18n.t(label)}</div>
					<div class="text-xl font-medium">{value}</div>
				</div>
			{/each}
		</div>

		<div class="text-sm font-medium mb-2">{$i18n.t('Usage by model')}</div>
		<div class="flex flex-col gap-2 mb-6">
			{#each data.byModel as m}
				<div class="flex items-center gap-3 text-sm">
					<div class="w-40 truncate text-gray-600 dark:text-gray-300" title={m.model}>{m.model}</div>
					<div class="flex-1 bg-gray-100 dark:bg-gray-800 rounded-full h-2.5 overflow-hidden">
						<div class="bg-black dark:bg-white h-full rounded-full" style="width: {(m.tokens / maxModelTokens) * 100}%"></div>
					</div>
					<div class="w-24 text-right tabular-nums">{formatNumber(m.tokens)}</div>
					<div class="w-20 text-right tabular-nums text-gray-500">{fmtCost(m.cost)}</div>
				</div>
			{/each}
		</div>
	{/if}
</div>
```

- [ ] **Step 2: Route page**

Create `src/routes/(app)/usage/+page.svelte`:

```svelte
<script lang="ts">
	import Dashboard from '$lib/components/usage/Dashboard.svelte';
</script>

<svelte:head>
	<title>Usage</title>
</svelte:head>

<Dashboard />
```

- [ ] **Step 3: Build to verify it compiles**

Run: `NODE_OPTIONS="--max-old-space-size=5120" npx vite build 2>&1 | tail -5`
Expected: build completes without errors referencing `usage` / `langfuse`.

- [ ] **Step 4: Commit**

```bash
git add src/lib/components/usage/Dashboard.svelte src/routes/\(app\)/usage/+page.svelte
git commit -m "feat(langfuse): overall My Usage dashboard + /usage route"
```

---

### Task 14: Per-chat inline footer bar

**Files:**
- Create: `src/lib/components/chat/LangfuseUsageBar.svelte`
- Modify: `src/lib/components/chat/Chat.svelte` (insert above `<MessageInput>` ~line 3096)

- [ ] **Step 1: Footer component**

Create `src/lib/components/chat/LangfuseUsageBar.svelte`:

```svelte
<script lang="ts">
	import { getContext } from 'svelte';
	import { config } from '$lib/stores';
	import { getChatUsage, type ChatUsage } from '$lib/apis/langfuse';

	const i18n: any = getContext('i18n');

	export let chatId: string = '';
	export let history: { messages: Record<string, any> } = { messages: {} };
	export let temporary: boolean = false;

	let usage: ChatUsage | null = null;

	// Instant, local token + turn count (masks Langfuse ingestion lag).
	$: localTokens = Object.values(history?.messages ?? {}).reduce((sum: number, m: any) => {
		const u = m?.usage;
		if (!u) return sum;
		const total =
			u.total_tokens ??
			(u.input_tokens ?? u.prompt_tokens ?? 0) + (u.output_tokens ?? u.completion_tokens ?? 0);
		return sum + (Number(total) || 0);
	}, 0);
	$: localTurns = Object.values(history?.messages ?? {}).filter(
		(m: any) => m?.role === 'assistant' && m?.usage
	).length;

	$: enabled = ($config?.features?.enable_langfuse ?? false) && !!chatId && !temporary;

	const fmtNum = (n: number) => new Intl.NumberFormat().format(n);
	const fmtCost = (n: number) => (n ? `$${n.toFixed(n < 0.01 ? 4 : 2)}` : '$0');

	let lastFetched = '';
	const refresh = async () => {
		if (!enabled) return;
		try {
			usage = await getChatUsage(localStorage.token, chatId);
		} catch {
			/* keep showing local values */
		}
	};

	// Refetch when the chat changes; re-fetch shortly after a turn finishes
	// (Langfuse ingestion lag) by reacting to the local turn count.
	$: if (enabled && chatId !== lastFetched) {
		lastFetched = chatId;
		refresh();
	}
	$: if (enabled && localTurns) {
		setTimeout(refresh, 4000);
	}

	$: shownTokens = localTokens; // tokens are authoritative locally
	$: shownTurns = usage?.turns ?? localTurns;
</script>

{#if enabled && (shownTokens > 0 || shownTurns > 0)}
	<div
		class="mx-auto w-full max-w-6xl px-3.5 pb-1 flex items-center justify-center gap-2 text-xs text-gray-400 dark:text-gray-500 select-none"
	>
		<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="size-3.5">
			<path d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z" />
		</svg>
		<span class="tabular-nums">{fmtNum(shownTokens)} {$i18n.t('Tokens').toLowerCase()}</span>
		{#if usage}
			<span>·</span><span class="tabular-nums">{fmtCost(usage.cost)}</span>
			<span>·</span><span class="tabular-nums">{shownTurns} {$i18n.t('turns')}</span>
			{#if usage.avgLatency}
				<span>·</span><span class="tabular-nums">{usage.avgLatency.toFixed(1)}s {$i18n.t('avg')}</span>
			{/if}
		{:else}
			<span>·</span><span class="tabular-nums">{shownTurns} {$i18n.t('turns')}</span>
		{/if}
	</div>
{/if}
```

- [ ] **Step 2: Import in Chat.svelte** — add to the component imports near the top (alongside other `$lib/components/chat/...` imports):

```javascript
	import LangfuseUsageBar from '$lib/components/chat/LangfuseUsageBar.svelte';
```

- [ ] **Step 3: Render it** — in `Chat.svelte`, inside the `<div class=" pb-2 {dragged ? 'z-0' : 'z-10'}">` wrapper, immediately before `<MessageInput` (~line 3096):

```svelte
			<LangfuseUsageBar chatId={$chatId} {history} temporary={$temporaryChatEnabled} />
```

- [ ] **Step 4: Add i18n fallback keys** — ensure `"turns": ""` and `"avg": ""` exist in `src/lib/i18n/locales/en-US/translation.json` (add alphabetically if missing), then validate JSON:

Run: `node -e "JSON.parse(require('fs').readFileSync('src/lib/i18n/locales/en-US/translation.json','utf8')); console.log('valid json')"`
Expected: `valid json`.

- [ ] **Step 5: Build to verify it compiles**

Run: `NODE_OPTIONS="--max-old-space-size=5120" npx vite build 2>&1 | tail -5`
Expected: build completes; no errors referencing `LangfuseUsageBar`.

- [ ] **Step 6: Commit**

```bash
git add src/lib/components/chat/LangfuseUsageBar.svelte src/lib/components/chat/Chat.svelte src/lib/i18n/locales/en-US/translation.json
git commit -m "feat(langfuse): per-chat inline usage footer"
```

---

## Phase 4 — Integration verification & deploy

### Task 15: End-to-end live verification

**Files:** none (verification only)

- [ ] **Step 1: Run all backend langfuse tests**

Run: `cd backend && python -m pytest open_webui/test/util/test_langfuse_tracing.py open_webui/test/util/test_langfuse_router.py -v`
Expected: PASS (7 tests).

- [ ] **Step 2: Frontend build (the deploy artifact)**

Run: `rm -rf build && NODE_OPTIONS="--max-old-space-size=5120" npx vite build`
Expected: completes (~1m15s), `build/` produced.

- [ ] **Step 3: Deploy per HANDOFF.md**

```bash
# rsync build, scp the new + changed backend files, then on the box:
ssh -i ~/.ssh/sentientflow_hetzner root@89.167.86.128 '
  cd /opt/open-webui-corethesis
  export PATH="$HOME/.local/bin:$PATH"
  git fetch origin && git checkout corethesis/langfuse-usage && git reset --hard origin/corethesis/langfuse-usage
  NODE_OPTIONS="--max-old-space-size=5120" UV_HTTP_TIMEOUT=600 uv sync --no-cache   # installs langfuse
  # add LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL to /etc/open-webui.env
  systemctl restart open-webui.service
'
```

Add to `/etc/open-webui.env` (server-side only, never committed):
```
LANGFUSE_PUBLIC_KEY=pk-lf-dc283408-66f7-4709-82b9-106f9e23952c
LANGFUSE_SECRET_KEY=sk-lf-587aaed9-c2ba-467c-b8da-e64f37ebcf73
LANGFUSE_BASE_URL=http://46.224.188.12:3000
```

- [ ] **Step 4: Verify the flag + endpoints (authenticated)**

After restart, confirm `/api/config` returns `features.enable_langfuse: true` for a logged-in user, the sidebar shows "My Usage", and `/usage` renders.

- [ ] **Step 5: Verify a real trace round-trips**

Send a chat turn with a real model (e.g. an OpenAI model), wait ~5–10s, then confirm in the Langfuse UI (or `traces?sessionId=<chat_id>&userId=<your user id>`) that a trace with model + tokens (+ cost where priced) appears, and that the per-chat footer + `/usage` page show it.

- [ ] **Step 6: Final commit / push the branch**

```bash
git push -u origin corethesis/langfuse-usage
```

---

## Self-Review (completed by author)

- **Spec coverage:** tracing (Tasks 2-6), per-user overall page (Tasks 10,13 + proxy 7-9), per-chat footer (Task 14), config/env/flag (Tasks 1,9), deps (Task 4), error-handling guarantees (Tasks 3 swallow/no-op; Task 8 empty-payload degrade), deploy (Task 15). All spec sections map to tasks.
- **Type consistency:** `build_generation_payload` keys (`trace_id_seed`, `usage_details`, `session_id`, `latency`) are consumed verbatim by `_emit`. Proxy returns `{totals:{tokens,cost,requests,avgLatency}, byModel:[{model,tokens,cost,requests}], daily:[{date,tokens,cost,requests}]}` and `{turns,cost,avgLatency}` — matched exactly by the TS types `UsageOverview`/`ChatUsage` and the Dashboard/Footer field reads.
- **Placeholders:** none — every code step contains complete code; every command has expected output.
- **Verified externally:** SDK call surface (langfuse 3.7.0 introspection), end_time-ns latency, cost auto-compute, and all read endpoints were probed live against the v3.163.0 server on 2026-06-03.
```
