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
    # Accept both naming conventions:
    # - OpenAI / Chat Completions API: prompt_tokens / completion_tokens
    # - Anthropic / Responses API: input_tokens / output_tokens
    # Open WebUI's middleware forwards the upstream provider's shape verbatim,
    # so we have to read both. Without this, every OpenAI / DeepInfra /
    # OpenRouter trace lands in Langfuse with 0 tokens and $0 cost.
    input_tokens = usage.get("input_tokens")
    if input_tokens is None:
        input_tokens = usage.get("prompt_tokens")
    output_tokens = usage.get("output_tokens")
    if output_tokens is None:
        output_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")

    if input_tokens is not None:
        usage_details["input"] = int(input_tokens)
    if output_tokens is not None:
        usage_details["output"] = int(output_tokens)
    if total_tokens is not None:
        usage_details["total"] = int(total_tokens)

    # Prefer `selected_model_id` — providers that route to a different
    # underlying model at request time (e.g. OpenClaw resolving to a specific
    # DeepInfra model, Arena random-pick) emit this via streaming so Langfuse
    # can match the real model against its pricing catalog. Falls back to the
    # alias the user originally picked.
    model = (
        metadata.get("selected_model_id")
        or metadata.get("model")
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
        "metadata": {
            "message_id": message_id,
            "chat_id": chat_id,
            "source": "open-webui",
        },
        "user_id": user_id,
        "session_id": chat_id,
        "latency": latency,
    }


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
    """Fire-and-forget: emit a Langfuse trace for this chat turn. Never raises.

    Emits the trace even when usage_details is missing — some providers (and
    some streaming responses without `stream_options.include_usage=true`) don't
    return token counts in the response payload. We'd rather have a trace
    without cost numbers than no trace at all; the cost data can be back-filled
    by Langfuse from the model name + input/output via its model registry.
    """
    if not ENABLE_LANGFUSE:
        log.info("langfuse trace skipped: ENABLE_LANGFUSE=false")
        return
    try:
        payload = build_generation_payload(ctx)
        if payload is None:
            metadata = ctx.get("metadata") or {}
            log.info(
                "langfuse trace skipped: not traceable chat_id=%r",
                metadata.get("chat_id"),
            )
            return
        if not payload.get("usage_details"):
            log.info(
                "langfuse trace EMITTED without usage_details — provider didn't "
                "return token counts (model=%r chat=%r)",
                payload.get("model"),
                payload.get("session_id"),
            )
        asyncio.get_running_loop().run_in_executor(None, _emit, payload)
    except Exception as e:  # pragma: no cover - defensive
        log.warning(f"trace_chat_turn skipped (ignored): {e}")
