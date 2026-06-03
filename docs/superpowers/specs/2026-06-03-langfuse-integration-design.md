# Langfuse integration — design spec

**Date:** 2026-06-03
**Branch:** `corethesis/langfuse-usage` (off `corethesis/openclaw-module`)
**Status:** Approved (design), proceeding to implementation plan.

## Goal

Integrate Langfuse into the Open WebUI fork so that:

1. **Every chat turn is traced** to the self-hosted Langfuse (proper LLM *generations* with
   model, input/output, token usage, latency, grouped per-conversation and per-user).
2. **Each user can see their own usage** — an overall "My Usage" page reached from a
   sidebar entry, plus a compact **per-chat usage footer** inside each conversation.

It must feel built-in: matching the existing design language, never blocking or breaking a
chat, and cleanly self-disabling when Langfuse is not configured.

## Approved decisions

| Decision | Choice |
|----------|--------|
| Metrics surfaced | Tokens + cost + request/turn counts + latency (cost auto-hides if Langfuse has no model pricing) |
| Overall-usage scope | **Per-user** — every signed-in user sees *their own* aggregate (filtered by their `userId`) |
| Per-chat surface | **Inline footer bar** under the conversation |
| Tracing mechanism | **Langfuse Python SDK**, hooked in `utils/middleware.py` after usage normalization (Approach ①) |

Rejected: built-in OTEL→Langfuse OTLP (emits infra spans, not LLM generations — cannot
power usage views); external pipeline/filter (needs a separate pipelines server, not built-in).

## Verified environment facts (probed 2026-06-03)

- Langfuse server: `http://46.224.188.12:3000`, version **3.163.0**, project `corethesis-ai-proj`.
  - ≥3.125 → Langfuse Python SDK v3 supported. ≥3.22 → OTLP supported.
- Keys valid via HTTP Basic auth `base64(public_key:secret_key)`.
- **v2 query API is absent** on this version (`/api/public/v2/metrics` → 404). Build on **v1**:
  - `GET /api/public/metrics/daily?userId=&limit=` → per-day, per-model `{inputUsage, outputUsage, totalUsage, totalCost, countTraces, countObservations}`. **userId filter works (200).**
  - `GET /api/public/metrics` (v1 query, JSON in `query` param) → flexible aggregation (`view=observations`, measures `count`/`totalTokens`/`totalCost`/`latency`, group by `model`, filter by `userId`/`sessionId`). Returns 200.
  - `GET /api/public/observations` → rich per-observation fields: `model`, `promptTokens`, `completionTokens`, `totalTokens`, `calculatedTotalCost`, `costDetails`, `latency`, `timeToFirstToken`, `traceId`, `type`, `startTime/endTime`, `usageDetails`.
  - `GET /api/public/traces?sessionId=&userId=` and `GET /api/public/sessions` → 200.
- Existing data: ~10 traces/day from an unrelated `news_synthesis_pipeline` (OTEL, `userId`/`sessionId` null, `model: null`, `cost: 0`). Our Open WebUI traces will be cleanly separate, with `userId` + `sessionId` + `model` + usage set.

## Architecture

### A. Tracing module — `backend/open_webui/utils/telemetry/langfuse.py`

- Lazy singleton `get_langfuse()` reading `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` /
  `LANGFUSE_BASE_URL` from env. If any key is unset → disabled, every entry point is a no-op.
- `trace_chat_turn(*, metadata, model, input_messages, output_text, usage, latency, error=None)`:
  creates one Langfuse **trace** with a nested **generation** observation.
  - `session_id = metadata.chat_id` (groups turns by conversation).
  - `user_id = metadata.user_id` (drives the per-user views).
  - generation `model`, `input` (messages), `output` (assistant text),
    `usage_details` mapped from `normalize_usage()` output
    (`input_tokens`→input/prompt, `output_tokens`→completion, `total_tokens`),
    `metadata` (message_id, model base id, source).
  - trace id seeded deterministically from `message_id`
    (`Langfuse.create_trace_id(seed=...)`) so retries dedupe instead of duplicating.
  - `level`/`status_message` set on error.
- **Robustness:** every call wrapped in try/except (swallowed + logged) and dispatched as a
  fire-and-forget background task — Langfuse latency or downtime can never delay or break a
  reply. SDK background flushing + an explicit `flush()` on app shutdown (lifespan/atexit).
- Skips pseudo-chats whose `chat_id` is empty or starts with `local:` / `channel:`.

**Hook points (verified):**
- `backend/open_webui/utils/middleware.py` — `non_streaming_chat_response_handler` (~:3402)
  after `normalize_usage()` (~:3497), and `streaming_chat_response_handler` (~:3549) at
  stream completion where final usage is known. Both have `ctx['metadata']`
  (`user_id`, `chat_id`, `message_id`, `model`, `session_id`), model id, tokens, content.
- Usage is normalized to `{input_tokens, output_tokens, total_tokens}` by
  `backend/open_webui/utils/response.py:normalize_usage()` (:11-49) — trace consumes the
  normalized shape so OpenAI, Ollama, and OpenClaw turns are uniform.
- Captures the OpenClaw model too: it is registered as an OpenAI connection, so its turns
  flow through this same middleware.

### B. Usage proxy router — `backend/open_webui/routers/langfuse.py`, mounted `/api/v1/langfuse`

Keys never leave the server; the browser only talks to this proxy. Mounted in `main.py`
alongside the other routers (follow the `openclaw` mount pattern).

- `GET /usage/overview?days=30` — **`userId` forced from `get_verified_user`** (never a
  client param). Queries v1 metrics + daily-metrics filtered by that userId. Returns:
  `{ totals: {tokens, cost, requests, avgLatency}, byModel: [{model, tokens, cost, requests}], daily: [{date, tokens, cost, requests}] }`.
- `GET /usage/chat/{chat_id}` — `userId` forced from session; scoped to
  `sessionId = chat_id` (and userId, defense-in-depth). Returns
  `{tokens, cost, turns, avgLatency, byModel?}`.
- `GET /config` — `{enabled: bool}` so the frontend renders only when keys are present.
- All endpoints require `get_verified_user`. **A user can never read another user's usage** —
  the id is taken from the authenticated session, not the request.
- Short server-side cache (~20s TTL) to absorb polling and respect Langfuse rate limits.
  Langfuse errors → return an empty/zeroed payload (HTTP 200 with `enabled`/empty), never 5xx,
  so the UI degrades gracefully.

### C. Overall usage — sidebar entry → `/usage` page

- `src/lib/components/layout/Sidebar.svelte` — add `usage` across the 5 touch points
  (matching the `openclaw` pattern): `DEFAULT_PINNED_ITEMS` (:79), `isMenuItemVisible`
  (:109-145, visible to all signed-in users, gated on the `enable_langfuse` feature flag),
  `getMenuItemMeta` (:147-157, `{label:'Usage', href:'/usage', iconType:'usage'}`), and the
  two icon SVG blocks (mini :884-948, full :1146-1210). Chart/bar-style icon.
- Route `src/routes/(app)/usage/+page.svelte` (+ `+layout.svelte`) rendering
  `src/lib/components/usage/Dashboard.svelte`: summary cards (tokens, cost, requests, avg
  latency), tokens-by-model bar, cost/usage-over-N-days line. Uses **chart.js 4.5.0**
  (already installed) following `src/lib/components/admin/Analytics/Dashboard.svelte` and the
  existing `ChartLine.svelte`.
- API client `src/lib/apis/langfuse/index.ts` (pattern from `apis/openclaw/index.ts` /
  `apis/analytics/index.ts`): `WEBUI_API_BASE_URL` + Bearer token, try/catch with `.detail`.
- i18n keys ("Usage", "Tokens", "Cost", "Requests", "Latency", …) added to
  `src/lib/i18n/locales/en-US/translation.json`.

### D. Per-chat inline footer — `src/lib/components/chat/LangfuseUsageBar.svelte`

- Rendered under the conversation (below the Messages list / above MessageInput) in
  `Chat.svelte`, where `$chatId` is in scope.
- **Hybrid data, to mask Langfuse's few-second ingestion lag:**
  - **Instant**: tokens + turn count summed locally from `message.usage` already present in
    the chat history (`message.usage` is captured during streaming, `Chat.svelte` ~:1831).
  - **Reconciled**: cost + latency (and authoritative tokens) from
    `GET /api/v1/langfuse/usage/chat/{chat_id}`, fetched on load and re-fetched ~4s after
    each completed turn (tolerates ingestion lag).
- Hidden when: Langfuse disabled, `$chatId` empty, or temporary chat (`$temporaryChatEnabled`).
- Compact: `📊 18,402 tokens · $0.07 · 6 turns · 1.9s avg`. Responsive; never shifts the
  message layout when absent.

### E. Config, env, feature flag

- `backend/open_webui/env.py`: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
  `LANGFUSE_BASE_URL`, and `ENABLE_LANGFUSE` (auto-true when keys present, overridable).
- Surface `enable_langfuse` in the frontend `$config.features` via the existing
  `/api/config` payload (so sidebar + footer render only when enabled).
- Python dep `langfuse` (v3.x, compatible with server 3.163.0) added to `pyproject.toml`;
  installed on the box via `uv sync`.
- Keys live **only** in `/etc/open-webui.env` on the server — never committed, never sent to
  the browser.

## Error-handling guarantees ("no errors / feels built-in")

- Tracing: fire-and-forget + exception-swallowed; a Langfuse outage adds 0ms to chat latency
  and surfaces nothing to the user.
- Proxy: Langfuse failures return empty/zeroed 200 payloads; the page/footer show a clean
  empty state, never an error toast that blocks the chat.
- Auto-disable: with keys absent, the tracing module is a no-op and the UI surfaces don't render.

## Testing strategy

- Backend unit tests: `trace_chat_turn` builds the correct trace/generation payload from a
  representative `ctx` (OpenAI + Ollama + OpenClaw shapes); no-op when disabled; never raises
  on Langfuse client error. Proxy: `userId` is always taken from the session (a forged
  client userId cannot widen scope); Langfuse error → empty 200.
- Live verification against the real server (read-only): confirm a turn produces a trace with
  populated model + tokens (and cost when the model has pricing); confirm `/usage/overview`
  and `/usage/chat/{id}` return correctly-scoped data.
- Frontend: dashboard renders cards/charts from a mocked payload; footer shows instant local
  tokens then reconciles; both hidden when disabled.

## Risks & notes

- **Cost may read $0** for models without pricing configured in this Langfuse instance
  (custom models like `openclaw`). The UI handles `cost: 0`/null gracefully; documented, not
  a bug.
- **Ingestion lag** (~seconds) is masked on the footer by the local-first hybrid; the overall
  page is inherently slightly behind real-time (acceptable).
- **SDK surface**: pin the exact `langfuse` v3 minor and confirm the trace+generation call
  surface with a tiny spike before wiring `trace_chat_turn` (the v3 low-level API for
  after-the-fact emission).
- Streaming flush: ensure the background trace task is scheduled after the stream closes so
  final usage is captured (a known Langfuse+streaming gotcha).

## Out of scope

- Instance-wide/admin aggregate views (decision was per-user).
- Editing Langfuse model pricing, prompt management, evals, scores.
- Migrating the existing `news_synthesis_pipeline` traces.
- Real-time websocket push of usage (polling/post-turn refetch is sufficient).

## Deployment

Per `HANDOFF.md`: build frontend on the Mac → rsync `build/`; `scp` backend Python; on the
box `uv sync` (for the new `langfuse` dep) + add `LANGFUSE_*` to `/etc/open-webui.env` +
`systemctl restart open-webui.service`.
