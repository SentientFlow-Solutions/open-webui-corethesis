"""
OpenClaw module — bridges the locally-running OpenClaw gateway into Open WebUI
as a first-class sidebar module.

The gateway is WebSocket-native; we invoke it through the `openclaw agent --json`
CLI which the gateway service exposes for one-shot agent turns. This keeps the
integration simple (no WS framing) and gives us clear exit codes + stderr.

Endpoints (mounted at /api/v1/openclaw):
  GET  /health     liveness + reachability of the OpenClaw binary
  GET  /agents     list configured agents (id, name, emoji, model)
  POST /chat       one-shot turn against an agent; returns {reply, session_key}

Config (env, set on the Open WebUI container):
  OPENCLAW_BIN              path to the openclaw CLI (default: "openclaw")
  OPENCLAW_DEFAULT_AGENT    agent id used when client omits it (default: "main")
  OPENCLAW_TIMEOUT_SECONDS  per-call timeout (default: 600)
  OPENCLAW_DEFAULT_THINKING optional default thinking level passed to --thinking

Session continuity: the gateway holds context per --session-key. We namespace
keys by Open WebUI user id so multi-tenant chats don't bleed:
    openclaw:webui-<user_id>-<chat_id>
The frontend creates a fresh <chat_id> per conversation and keeps using it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
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


def _extract_reply(stdout: str) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    """Pull the assistant reply text from `openclaw agent --json` stdout.

    The envelope shape is mildly version-dependent; we probe likely paths and
    fall back to the raw stdout if none match. Returns (reply, raw_envelope).
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

    candidates: list[Any] = [
        envelope.get("reply"),
        envelope.get("message"),
        envelope.get("output"),
        envelope.get("text"),
        envelope.get("content"),
        (envelope.get("result") or {}).get("message") if isinstance(envelope.get("result"), dict) else None,
        (envelope.get("result") or {}).get("reply") if isinstance(envelope.get("result"), dict) else None,
        (envelope.get("result") or {}).get("text") if isinstance(envelope.get("result"), dict) else None,
        (envelope.get("data") or {}).get("message") if isinstance(envelope.get("data"), dict) else None,
        (envelope.get("data") or {}).get("reply") if isinstance(envelope.get("data"), dict) else None,
        (envelope.get("response") or {}).get("text") if isinstance(envelope.get("response"), dict) else None,
    ]
    for cand in candidates:
        if isinstance(cand, str) and cand.strip():
            return cand, envelope
        if isinstance(cand, dict):
            for k in ("text", "content", "message"):
                v = cand.get(k)
                if isinstance(v, str) and v.strip():
                    return v, envelope

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
