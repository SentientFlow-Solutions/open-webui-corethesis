# Claude-style processing feedback — design spec

**Date:** 2026-06-11
**Branch:** `corethesis/openclaw-module`
**Status:** Approved; implementing.

## Goal

Replace the bare pulsing-dot wait state with Claude-like visual feedback:
a live "Thinking…" treatment during processing, and for OpenClaw's long agent
turns (60–120s) a real progress narrative that collapses to "Thought for Xs".

## Approved decisions

- OpenClaw wait content: **stage + elapsed time** narration (honest activity log).
- Global: the bare dot becomes a **shimmer "Thinking…"** text for all models.
- Mechanism: ride Open WebUI's **native reasoning UI** — no new UI components.

## Verified grounding (explored 2026-06-11)

- The dot: `Skeleton` rendered at `ResponseMessage.svelte:829` when
  `message.content === '' && !message.done && !message.error && !hasVisibleStatus`.
- Native thinking UI: middleware (`utils/middleware.py:4195-4244`) converts
  `delta.reasoning_content|reasoning|thinking` from ANY provider — including
  external OpenAI connections — into a `{'type':'reasoning'}` output item,
  serialized as `<details type="reasoning" done="false|true" duration="N">`,
  rendered by `Collapsible.svelte` as shimmer "Thinking…" → "Thought for Xs".
  Duration auto-computed when the first normal content delta closes the item.
- OpenClaw adapter (`routers/openclaw.py` stream_body): first chunk carries
  `role:assistant`, then EMPTY delta keepalives every 5s (the dot persists the
  whole wait), then synthetic content chunks. CLI is one-shot — no real
  intermediate reasoning exists, so narration is the honest option.
- `.shimmer` CSS exists in `app.css`; i18n key `Thinking...` exists.

## Changes

### 1. `backend/open_webui/routers/openclaw.py` — narrated wait (streaming branch only)

- New pure helper `_narration_line(tick: int, elapsed: float) -> Optional[str]`:
  - tick 0 → `"Connecting to OpenClaw agent…\n"` (emitted right after the role chunk)
  - tick 1 (≈5s) → `"Agent is working on your request…\n"`
  - then every 3rd tick (≈15s cadence) → `"Still working — {int(elapsed)}s elapsed…\n"`
  - other ticks → `None` (emit the existing empty-delta keepalive instead, so the
    5s keepalive cadence is preserved without flooding the log)
- `_sse_chunk` gains a `reasoning` parameter (or a sibling `_sse_reasoning_chunk`)
  emitting `{"delta": {"reasoning_content": <line>}}`.
- Keepalive loop emits the narration line when non-None, else the empty delta.
- Everything downstream unchanged: when the reply's first content chunk arrives,
  the middleware closes the reasoning item with the true duration. Error/timeout
  paths and the non-streaming branch untouched.

### 2. `src/lib/components/chat/Messages/ResponseMessage.svelte` — global shimmer

- Replace `<Skeleton />` at the empty-pending branch with
  `<div class="shimmer text-base w-fit my-1">{$i18n.t('Thinking...')}</div>`.
- `Skeleton.svelte` itself is untouched (used elsewhere).

## Testing & verification

- Unit test `_narration_line` cadence (new `test_openclaw_narration.py`).
- `svelte-check` (no new errors) + full `vite build`.
- Deploy via push → `docker.yml` CI image → `docker compose pull && up -d`;
  live-verify the adapter's SSE stream contains `reasoning_content` chunks and
  the bundle serves the shimmer markup.

## Out of scope

- Real agent reasoning extraction (CLI is one-shot; no reasoning in envelope).
- Status-event pipelines/filters; varied/theatrical phrase rotation.
