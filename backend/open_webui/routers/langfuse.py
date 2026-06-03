"""Per-user Langfuse usage proxy.

Queries the self-hosted Langfuse v1 public API server-side (keys never reach the
browser) and always scopes results to the authenticated user. Failures degrade
to empty/zeroed payloads so the UI never breaks.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends

from open_webui.env import (
    ENABLE_LANGFUSE,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_SECRET_KEY,
    LANGFUSE_BASE_URL,
)
from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)

router = APIRouter()


# -----------------------------------------------------------------------------
# Pure aggregators (unit-tested without network)
# -----------------------------------------------------------------------------


def _avg_latency(rows: list[dict]) -> float:
    lats = [
        r.get("latency")
        for r in rows
        if isinstance(r.get("latency"), (int, float)) and r.get("latency")
    ]
    return round(sum(lats) / len(lats), 3) if lats else 0.0


def aggregate_overview(daily: list[dict], observations: list[dict]) -> dict:
    """Build the overall usage payload from daily-metrics + observations rows."""
    tokens = 0
    cost = 0.0
    requests = 0
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
            agg = by_model.setdefault(
                m, {"model": m, "tokens": 0, "cost": 0.0, "requests": 0}
            )
            agg["tokens"] += t
            agg["cost"] += c
            agg["requests"] += r
        tokens += day_tokens
        cost += float(day.get("totalCost") or 0)
        requests += int(day.get("countTraces") or 0)
        series.append(
            {
                "date": day.get("date"),
                "tokens": day_tokens,
                "cost": float(day.get("totalCost") or 0),
                "requests": int(day.get("countTraces") or 0),
            }
        )
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


# -----------------------------------------------------------------------------
# Langfuse HTTP access (server-side; Basic auth; small TTL cache)
# -----------------------------------------------------------------------------

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


# -----------------------------------------------------------------------------
# Endpoints (userId is always taken from the authenticated session)
# -----------------------------------------------------------------------------


@router.get("/config")
async def langfuse_config(user=Depends(get_verified_user)):
    return {"enabled": bool(ENABLE_LANGFUSE)}


@router.get("/usage/overview")
async def usage_overview(days: int = 30, user=Depends(get_verified_user)):
    if not ENABLE_LANGFUSE:
        return {
            "enabled": False,
            "totals": {"tokens": 0, "cost": 0, "requests": 0, "avgLatency": 0},
            "byModel": [],
            "daily": [],
        }
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
