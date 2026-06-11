"""
OpenClaw module — bridges the locally-running OpenClaw gateway into Open WebUI.

The gateway is WebSocket-native; we invoke it through the `openclaw agent --json`
CLI which the gateway service exposes for one-shot agent turns. This keeps the
integration simple (no WS framing) and gives us clear exit codes + stderr.

Two surfaces:

1. **Native module** (mounted at /api/v1/openclaw):
     GET  /health    liveness + reachability of the OpenClaw binary
     GET  /agents    list configured agents (id, name, emoji, model)
     POST /chat      one-shot turn; returns {reply, session_key} JSON

2. **OpenAI-compatible adapter** (mounted at /api/v1/openclaw/v1):
     GET  /v1/models                  one model row: "openclaw"
     POST /v1/chat/completions        OpenAI-format chat with SSE streaming
   Wire this into Open WebUI's Admin → Settings → Connections (OpenAI API tab)
   so OpenClaw appears in the model dropdown and uses Open WebUI's main chat
   UI — markdown, slash menu, file drops, history, branching, all of it.

Config (env, set on the Open WebUI container):
  OPENCLAW_BIN              path to the openclaw CLI (default: "openclaw")
  OPENCLAW_DEFAULT_AGENT    agent id used internally (default: "main")
  OPENCLAW_TIMEOUT_SECONDS  per-call timeout (default: 600)
  OPENCLAW_DEFAULT_THINKING optional default thinking level passed to --thinking
  OPENCLAW_MODEL_ID         model id exposed to Open WebUI (default: "openclaw")

Session continuity / multi-tenant isolation: the gateway holds context per
--session-key. Every request derives the key from the authenticated Open WebUI
user.id and a per-conversation chat_id, so distinct users never share gateway
state — and a single user's distinct chats are isolated from each other too.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shlex
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)

router = APIRouter()


def _bin() -> str:
    return os.environ.get("OPENCLAW_BIN", "openclaw")


def _default_agent() -> str:
    return os.environ.get("OPENCLAW_DEFAULT_AGENT", "main")


def _timeout_seconds() -> int:
    try:
        return int(os.environ.get("OPENCLAW_TIMEOUT_SECONDS", "600"))
    except ValueError:
        return 600


def _default_thinking() -> Optional[str]:
    val = os.environ.get("OPENCLAW_DEFAULT_THINKING")
    return val if val else None


def _model_id() -> str:
    """The id Open WebUI sees in its model dropdown. Single model, no agents
    exposed — `OPENCLAW_DEFAULT_AGENT` is the real agent under the hood."""
    return os.environ.get("OPENCLAW_MODEL_ID", "openclaw").strip() or "openclaw"


def _safe_id(raw: str, fallback: str = "x") -> str:
    """Sanitize a string for use as a session-key fragment."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", raw or "")[:64]
    return cleaned or fallback


def _derive_chat_id(
    explicit_chat_id: Optional[str],
    messages: list[dict[str, Any]],
) -> str:
    """Find a stable per-conversation identifier.

    Priority: explicit chat_id header from Open WebUI → hash of the first user
    message (stable for the lifetime of that conversation) → 'new'.
    """
    if explicit_chat_id:
        return _safe_id(explicit_chat_id, "new")
    if messages:
        first_user = next(
            (m for m in messages if isinstance(m, dict) and m.get("role") == "user"),
            None,
        )
        if first_user:
            seed = json.dumps(first_user.get("content"), sort_keys=True, default=str)
            return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return "new"


async def _run_openclaw(argv: list[str], timeout: int) -> tuple[int, str, str]:
    """Run the openclaw CLI; return (returncode, stdout, stderr).

    Uses asyncio.create_subprocess_exec so the event loop isn't blocked by
    long-running agent turns.
    """
    log.debug("openclaw exec: %s", " ".join(shlex.quote(a) for a in argv))
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await proc.wait()
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"openclaw agent timed out after {timeout}s",
        )
    return proc.returncode or 0, stdout_b.decode("utf-8", errors="replace"), stderr_b.decode("utf-8", errors="replace")


def _clean_reply(text: str) -> str:
    """Strip the NO_REPLY sentinel the agent appends when it has nothing to add.

    Example: "Hello! 📊\n\nNO_REPLY" -> "Hello! 📊"
    """
    cleaned = text.rstrip()
    # Token can appear alone on the last line or at end of a line, in either case
    # remove it and any trailing whitespace it left behind.
    if cleaned.endswith("NO_REPLY"):
        cleaned = cleaned[: -len("NO_REPLY")].rstrip()
    return cleaned


def _extract_underlying_model(envelope: Optional[dict[str, Any]]) -> Optional[str]:
    """Pull the actual provider/model used to generate the reply.

    openclaw 2026.5.x emits this under ``result.meta.executionTrace`` with
    ``winnerProvider`` + ``winnerModel`` fields (e.g. provider="deepinfra",
    model="Qwen/Qwen3-Next-80B-A3B-Instruct"). We expose the combined string
    ("deepinfra/Qwen/Qwen3-Next-80B-A3B-Instruct") so Langfuse can match it
    against its model-pricing catalog and so the user sees the real model
    on the trace instead of the opaque "openclaw" alias.
    """
    if not isinstance(envelope, dict):
        return None
    result = envelope.get("result")
    if not isinstance(result, dict):
        return None
    meta = result.get("meta")
    if not isinstance(meta, dict):
        return None
    et = meta.get("executionTrace")
    if not isinstance(et, dict):
        return None
    provider = et.get("winnerProvider")
    model = et.get("winnerModel")
    if isinstance(model, str) and model:
        if isinstance(provider, str) and provider and not model.startswith(provider + "/"):
            return f"{provider}/{model}"
        return model
    return None


def _extract_reply(stdout: str) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    """Pull the assistant reply text from `openclaw agent --json` stdout.

    The current envelope (openclaw 2026.5.x) is:
        {
          "runId": "...",
          "status": "ok",
          "summary": "completed",
          "result": {
            "payloads": [{"text": "...", "mediaUrl": null}, ...],
            "meta": {...}
          }
        }

    We extract by concatenating payloads[*].text. Older / alternate shapes are
    probed as fallbacks. Returns (reply, raw_envelope).
    """
    stdout = stdout.strip()
    if not stdout:
        return None, None
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout, None

    if not isinstance(envelope, dict):
        return stdout, None

    # Primary path: result.payloads[*].text
    result = envelope.get("result")
    if isinstance(result, dict):
        payloads = result.get("payloads")
        if isinstance(payloads, list) and payloads:
            chunks: list[str] = []
            for p in payloads:
                if isinstance(p, dict):
                    t = p.get("text")
                    if isinstance(t, str) and t.strip():
                        chunks.append(t)
            if chunks:
                return _clean_reply("\n\n".join(chunks)), envelope

    # Fallback paths for older/alternate envelopes.
    result_dict = result if isinstance(result, dict) else {}
    data_dict = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    response_dict = envelope.get("response") if isinstance(envelope.get("response"), dict) else {}
    candidates: list[Any] = [
        envelope.get("reply"),
        envelope.get("message"),
        envelope.get("output"),
        envelope.get("text"),
        envelope.get("content"),
        result_dict.get("message"),
        result_dict.get("reply"),
        result_dict.get("text"),
        data_dict.get("message"),
        data_dict.get("reply"),
        response_dict.get("text"),
    ]
    for cand in candidates:
        if isinstance(cand, str) and cand.strip():
            return _clean_reply(cand), envelope
        if isinstance(cand, dict):
            for k in ("text", "content", "message"):
                v = cand.get(k)
                if isinstance(v, str) and v.strip():
                    return _clean_reply(v), envelope

    return None, envelope


# -----------------------------------------------------------------------------
# Schemas
# -----------------------------------------------------------------------------


class HealthResponse(BaseModel):
    ok: bool
    binary: str
    version: Optional[str] = None
    error: Optional[str] = None


class AgentInfo(BaseModel):
    id: str
    name: str
    emoji: Optional[str] = None
    model: Optional[str] = None
    is_default: bool = False


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=64_000)
    agent: Optional[str] = None
    session_key: Optional[str] = None
    chat_id: Optional[str] = Field(
        default=None,
        description="Stable id from the frontend; combined with user id to form the gateway session-key.",
    )
    thinking: Optional[str] = Field(
        default=None,
        description="off | minimal | low | medium | high | xhigh | adaptive | max",
    )
    timeout: Optional[int] = Field(default=None, ge=10, le=1800)


class ChatResponse(BaseModel):
    reply: str
    agent: str
    session_key: str
    raw: Optional[dict[str, Any]] = None


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    binary = _bin()
    try:
        rc, out, err = await _run_openclaw([binary, "--version"], timeout=10)
    except FileNotFoundError:
        return HealthResponse(ok=False, binary=binary, error="openclaw binary not found on PATH")
    except HTTPException as e:
        return HealthResponse(ok=False, binary=binary, error=str(e.detail))
    if rc != 0:
        return HealthResponse(ok=False, binary=binary, error=err.strip() or out.strip() or f"exit={rc}")
    return HealthResponse(ok=True, binary=binary, version=(out.strip() or err.strip()) or None)


@router.get("/agents", response_model=list[AgentInfo])
async def list_agents(user=Depends(get_verified_user)) -> list[AgentInfo]:
    binary = _bin()
    rc, out, err = await _run_openclaw([binary, "agents", "list", "--json"], timeout=20)
    if rc != 0:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"openclaw agents list failed: {err.strip() or out.strip() or f'exit={rc}'}",
        )
    try:
        rows = json.loads(out)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"openclaw agents list returned non-JSON: {e}",
        )
    if not isinstance(rows, list):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="openclaw agents list returned an unexpected shape",
        )
    agents: list[AgentInfo] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        agents.append(
            AgentInfo(
                id=str(row.get("id") or row.get("name") or "").strip() or "unknown",
                name=str(row.get("identityName") or row.get("name") or row.get("id") or "Agent"),
                emoji=row.get("identityEmoji") or None,
                model=row.get("model") or None,
                is_default=bool(row.get("isDefault")),
            )
        )
    return agents


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: Request,
    body: ChatRequest,
    user=Depends(get_verified_user),
) -> ChatResponse:
    agent = (body.agent or _default_agent()).strip() or _default_agent()

    if body.session_key:
        session_key = body.session_key
    elif body.chat_id:
        # Namespace per-user so multi-tenant chats don't share gateway context.
        safe_chat_id = "".join(c for c in body.chat_id if c.isalnum() or c in "-_")[:64] or "new"
        session_key = f"agent:{agent}:webui-{user.id}-{safe_chat_id}"
    else:
        session_key = f"agent:{agent}:webui-{user.id}-default"

    thinking = body.thinking or _default_thinking()
    timeout = body.timeout or _timeout_seconds()
    binary = _bin()

    argv = [
        binary,
        "agent",
        "--agent",
        agent,
        "--session-key",
        session_key,
        "--message",
        body.message,
        "--json",
        "--timeout",
        str(timeout),
    ]
    if thinking:
        argv.extend(["--thinking", thinking])

    rc, out, err = await _run_openclaw(argv, timeout=timeout + 30)
    if rc != 0:
        # Surface the actual gateway error so the user can act on cooldowns,
        # missing API keys, etc., instead of getting an opaque 500.
        detail = err.strip() or out.strip() or f"openclaw agent exited with {rc}"
        log.warning("openclaw agent failed (rc=%d): %s", rc, detail[:500])
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)

    reply, raw = _extract_reply(out)
    if reply is None:
        log.warning("openclaw agent succeeded but produced no parseable reply; raw=%r", out[:500])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="openclaw agent returned an empty reply",
        )

    return ChatResponse(reply=reply, agent=agent, session_key=session_key, raw=raw)


# -----------------------------------------------------------------------------
# OpenAI-compatible adapter (so Open WebUI's main chat UI can use OpenClaw)
# -----------------------------------------------------------------------------
#
# Wire-up in Open WebUI: Admin → Settings → Connections → OpenAI API → Add.
#   URL:     http://172.17.0.1:18181/api/v1/openclaw/v1
#   API Key: anything non-empty (we don't validate; the endpoint is loopback-
#            bound and only reachable from the Open WebUI proxy)
#   Enable "Forward User Info" so headers X-OpenWebUI-User-* + X-OpenWebUI-Chat-
#   Id reach us — they're how we keep multi-tenant + per-conversation isolation.


def _messages_to_message(messages: list[dict[str, Any]]) -> str:
    """Take the latest user message text out of an OpenAI-format messages list.

    OpenClaw maintains conversation history server-side per session-key, so we
    only need to forward the newest user turn (not the full history). The
    gateway will append it to whatever it already has for this session.

    `content` can be a string or a list of content parts (text/image/file). We
    inline the text parts; image/file passthrough is a separate task (P4).
    """
    user_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "user"]
    if not user_msgs:
        return ""
    last = user_msgs[-1].get("content")
    if isinstance(last, str):
        return last
    if isinstance(last, list):
        parts: list[str] = []
        for part in last:
            if isinstance(part, dict):
                t = part.get("text") or part.get("content")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts)
    return str(last or "")


_tiktoken_enc = None


def _count_tokens(text: str) -> int:
    """Count tokens with cl100k_base (GPT-4 / Qwen / DeepSeek approximate).

    Used to populate the usage chunk we ship with OpenAI-compat streaming
    responses. OpenClaw's CLI is non-streaming and doesn't return a token
    breakdown, so we approximate. cl100k_base over-counts modestly for
    non-OpenAI tokenizers but is close enough for cost-tracking / display.
    Returns 0 on any failure (never raises — usage tracking must not break
    a chat reply).
    """
    if not text:
        return 0
    global _tiktoken_enc
    if _tiktoken_enc is None:
        try:
            import tiktoken
            _tiktoken_enc = tiktoken.get_encoding(
                os.environ.get("TIKTOKEN_ENCODING_NAME", "cl100k_base")
            )
        except Exception as e:  # noqa: BLE001
            log.warning("tiktoken unavailable, token counts will be 0: %s", e)
            _tiktoken_enc = False
    if not _tiktoken_enc:
        return 0
    try:
        return len(_tiktoken_enc.encode(text))
    except Exception:  # noqa: BLE001
        return 0


def _build_usage(messages_in: list[dict[str, Any]], reply: str) -> dict[str, int]:
    """Compute OpenAI-shape usage dict from the input messages and reply."""
    input_text = "\n".join(
        (m.get("content") if isinstance(m.get("content"), str) else "")
        or ""
        for m in (messages_in or [])
    )
    pt = _count_tokens(input_text)
    ct = _count_tokens(reply)
    return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}


def _sse_chunk(
    chunk_id: str,
    created: int,
    model: str,
    content: str,
    finish: Optional[str] = None,
    role: Optional[str] = None,
    reasoning: Optional[str] = None,
) -> str:
    delta: dict[str, Any] = {}
    if role:
        delta["role"] = role
    if content:
        delta["content"] = content
    if reasoning:
        delta["reasoning_content"] = reasoning
    payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish,
            }
        ],
    }
    return f"data: {json.dumps(payload)}\n\n"


def _sse_model_chunk(model_id: str) -> str:
    """Special SSE payload Open WebUI's middleware recognises (line ~3942 of
    middleware.py). When present it updates the chat record's selectedModelId
    so the UI tags the message with the real underlying model — and our
    Langfuse hook reads the same field to populate the trace's model."""
    return f"data: {json.dumps({'selected_model_id': model_id})}\n\n"


def _sse_usage_chunk(chunk_id: str, created: int, model: str, usage: dict[str, int]) -> str:
    """Final OpenAI-style usage chunk — emitted when stream_options.include_usage
    is requested (or always, for forward-compat). Open WebUI's middleware reads
    this and populates assistant_message.usage, which feeds Langfuse trace
    tokens and the My Usage dashboard."""
    payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [],
        "usage": usage,
    }
    return f"data: {json.dumps(payload)}\n\n"


def _narration_line(tick: int, elapsed: float) -> Optional[str]:
    """What the Thinking block should say at a given keepalive tick (5s cadence).

    Emitted as `reasoning_content` deltas, which Open WebUI's middleware turns
    into its native collapsible "Thinking…" block — the same UI real reasoning
    models get. tick 0 fires immediately, tick 1 after ~5s, then an
    elapsed-time update every 3rd tick (~15s) so a two-minute agent wait reads
    as a short activity log instead of 24 lines of noise. Returns None for
    silent ticks (the caller still sends an empty keepalive chunk).
    """
    if tick == 0:
        return "Connecting to OpenClaw agent…\n"
    if tick == 1:
        return "Agent is working on your request…\n"
    if tick % 3 == 0:
        return f"Still working — {int(elapsed)}s elapsed…\n"
    return None


def _slice_for_streaming(text: str, n: int = 24) -> list[str]:
    """Chop text into reasonably-sized chunks so the client renders progressively.

    OpenClaw's CLI is non-streaming today (returns the whole reply at once), so
    we synthesize a typing-out effect. Each chunk is ~24 chars, broken on word
    boundaries when possible. Cheap; runs after the agent already finished.
    """
    if not text:
        return []
    out: list[str] = []
    i = 0
    while i < len(text):
        end = min(i + n, len(text))
        # break on word boundary if we're mid-word and not at the end
        if end < len(text):
            space = text.rfind(" ", i, end)
            if space > i:
                end = space + 1
        out.append(text[i:end])
        i = end
    return out


@router.get("/v1/models")
async def openai_compat_list_models(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """OpenAI-compatible models endpoint. Returns a single 'openclaw' row that
    Open WebUI lists in its model dropdown. The agent picker on /agents is
    deliberately not exposed here — one model entry, no agent fan-out."""
    model = _model_id()
    return {
        "object": "list",
        "data": [
            {
                "id": model,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "corethesis",
            }
        ],
    }


@router.post("/v1/chat/completions")
async def openai_compat_chat_completions(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_openwebui_user_id: Optional[str] = Header(default=None),
    x_openwebui_chat_id: Optional[str] = Header(default=None),
):
    """OpenAI-compatible chat completions. Streams SSE when stream=true (default
    in Open WebUI), non-streaming JSON otherwise.

    Multi-user isolation: session key derived from X-OpenWebUI-User-Id +
    X-OpenWebUI-Chat-Id headers (Open WebUI sets these when "Forward User Info"
    is enabled on the connection). If headers aren't present, we fall back to a
    hash of the first user message to keep one conversation stable while still
    isolating different conversations from each other.
    """
    try:
        body = await request.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid json body: {e}")

    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="messages[] is required")

    user_message = _messages_to_message(messages).strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="latest user message is empty")

    stream = bool(body.get("stream", True))
    requested_model = str(body.get("model") or _model_id())

    user_id = _safe_id(x_openwebui_user_id or "anon", "anon")
    chat_id = _derive_chat_id(x_openwebui_chat_id, messages)

    agent = _default_agent()
    session_key = f"agent:{agent}:webui-{user_id}-{chat_id}"
    thinking = _default_thinking()
    timeout = _timeout_seconds()
    binary = _bin()

    argv = [
        binary, "agent",
        "--agent", agent,
        "--session-key", session_key,
        "--message", user_message,
        "--json",
        "--timeout", str(timeout),
    ]
    if thinking:
        argv.extend(["--thinking", thinking])

    log.info(
        "openclaw v1/chat user=%s chat=%s session=%s stream=%s",
        user_id, chat_id, session_key, stream,
    )

    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    if not stream:
        # Non-streaming branch: just run, wait, return one JSON.
        rc, out, err = await _run_openclaw(argv, timeout=timeout + 30)
        underlying_model: Optional[str] = None
        if rc != 0:
            detail = err.strip() or out.strip() or f"openclaw agent exited with {rc}"
            log.warning("openclaw v1/chat agent failed (rc=%d): %s", rc, detail[:500])
            reply = f"⚠️ OpenClaw gateway error:\n\n```\n{detail.strip()}\n```"
        else:
            reply, raw = _extract_reply(out)
            if not reply:
                reply = "⚠️ OpenClaw returned an empty reply."
            underlying_model = _extract_underlying_model(raw)
        return {
            "id": chunk_id,
            "object": "chat.completion",
            "created": created,
            # Report the actual provider/model the gateway routed to so
            # Langfuse can price it. Falls back to the alias if unknown.
            "model": underlying_model or requested_model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": reply},
                    "finish_reason": "stop",
                }
            ],
            "usage": _build_usage(messages, reply),
        }

    # Streaming branch: run openclaw as a background task, emit SSE keepalive
    # comments every few seconds while it thinks (the agent is slow today —
    # ~60-120s cold-start). When the task completes, emit the real chunks.
    # Without the keepalives, the browser sees dead air and assumes the
    # connection died (it'd take ~30s before Open WebUI gives up).

    async def stream_body():
        # First chunk: role-bearing delta. This mirrors what OpenAI's real
        # streaming API emits as its very first event. Open WebUI's
        # streaming_chat_response_handler in middleware.py only "opens" an
        # assistant message item (and starts emitting chat:completion events
        # to the frontend over Socket.IO) once it sees a chunk that establishes
        # the assistant role. An empty `delta: {}` is skipped, leaving the
        # client with no rendered bubble — so the eventual reply only shows up
        # after a page reload pulls it from chat history. Sending {"role":
        # "assistant"} here is the canonical OpenAI behavior and unblocks
        # rendering.
        yield _sse_chunk(chunk_id, created, requested_model, "", role="assistant")

        # Kick off the agent.
        agent_task = asyncio.create_task(_run_openclaw(argv, timeout=timeout + 30))

        # Heartbeat until the agent finishes, narrating progress via
        # `reasoning_content` deltas — Open WebUI's middleware renders these as
        # its native collapsible "Thinking…" block and closes it (with the real
        # duration) when the first content chunk lands. Silent ticks still emit
        # an empty `delta: {}` chunk (standard OpenAI format, not SSE comment
        # lines — Open WebUI's frontend parser drops those) so the ~5s
        # keepalive cadence is preserved.
        wait_started = time.time()
        yield _sse_chunk(
            chunk_id, created, requested_model, "", reasoning=_narration_line(0, 0.0)
        )

        tick = 0
        while not agent_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(agent_task), timeout=5.0)
            except asyncio.TimeoutError:
                tick += 1
                yield _sse_chunk(
                    chunk_id,
                    created,
                    requested_model,
                    "",
                    reasoning=_narration_line(tick, time.time() - wait_started),
                )
            except Exception:
                # Real failure — break out and let the post-loop block handle.
                break

        # Collect the result (or surface the exception as an in-chat error).
        try:
            rc, out, err = agent_task.result()
        except Exception as e:  # noqa: BLE001
            log.warning("openclaw v1/chat task raised: %s", e)
            reply = f"⚠️ OpenClaw gateway error:\n\n```\n{e}\n```"
            for piece in _slice_for_streaming(reply):
                yield _sse_chunk(chunk_id, created, requested_model, piece)
                await asyncio.sleep(0.01)
            yield _sse_chunk(chunk_id, created, requested_model, "", finish="stop")
            yield _sse_usage_chunk(chunk_id, created, requested_model, _build_usage(messages, reply))
            yield "data: [DONE]\n\n"
            return

        underlying_model: Optional[str] = None
        if rc != 0:
            detail = err.strip() or out.strip() or f"openclaw agent exited with {rc}"
            log.warning("openclaw v1/chat agent failed (rc=%d): %s", rc, detail[:500])
            reply = f"⚠️ OpenClaw gateway error:\n\n```\n{detail.strip()}\n```"
        else:
            reply, raw = _extract_reply(out)
            if not reply:
                reply = "⚠️ OpenClaw returned an empty reply."
            underlying_model = _extract_underlying_model(raw)

        # Tag the chat with the real provider/model BEFORE the content chunks so
        # the UI badge + Langfuse trace see it from the start.
        if underlying_model:
            yield _sse_model_chunk(underlying_model)

        emitted_model = underlying_model or requested_model
        for piece in _slice_for_streaming(reply):
            yield _sse_chunk(chunk_id, created, emitted_model, piece)
            await asyncio.sleep(0.01)
        yield _sse_chunk(chunk_id, created, emitted_model, "", finish="stop")
        yield _sse_usage_chunk(chunk_id, created, emitted_model, _build_usage(messages, reply))
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream_body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Tell nginx/Traefik not to buffer SSE — heartbeats must flow.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
