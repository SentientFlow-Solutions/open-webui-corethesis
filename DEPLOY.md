# OpenClaw module — deployment

This fork adds a self-contained **OpenClaw** sidebar module that bridges Open
WebUI to a self-hosted OpenClaw gateway via the `openclaw agent --json` CLI.

Recommended deploy target: the same Hetzner host running the OpenClaw gateway
(`89.167.86.128`). The module shells out to the local `openclaw` CLI, so the
Open WebUI process needs that binary on its PATH. The simplest way is to run
Open WebUI **natively** (systemd unit) alongside the gateway. Docker is
possible but requires extra work to expose `openclaw` inside the container —
see "Docker option" below.

## Surface

| Path                          | Method | Notes                                          |
|-------------------------------|--------|------------------------------------------------|
| `/api/v1/openclaw/health`     | GET    | Reports CLI reachability + version             |
| `/api/v1/openclaw/agents`     | GET    | Lists agents from `openclaw agents list --json`|
| `/api/v1/openclaw/chat`       | POST   | One-shot agent turn; gateway holds context     |
| `/openclaw` (UI)              | —      | Agent picker (new sidebar module)              |
| `/openclaw/:agentId` (UI)     | —      | Chat with the selected agent                   |

## Required environment

The router reads these from the process environment:

| Var                          | Default     | Purpose                                      |
|------------------------------|-------------|----------------------------------------------|
| `OPENCLAW_BIN`               | `openclaw`  | Path to the openclaw CLI                     |
| `OPENCLAW_DEFAULT_AGENT`     | `main`      | Used if the request omits `agent`            |
| `OPENCLAW_TIMEOUT_SECONDS`   | `600`       | Per-call timeout passed to `openclaw agent`  |
| `OPENCLAW_DEFAULT_THINKING`  | (unset)     | Optional `--thinking` level                  |

Session keys are namespaced per-user as `agent:<id>:webui-<userId>-<chatId>`
so multi-tenant chats can't bleed into each other on the gateway side.

## Recommended deploy: native systemd unit

This matches how the gateway is already deployed on the box.

```bash
# On 89.167.86.128 ----------------------------------------------------------
# 1. Clone (or pull) this fork
cd /opt
git clone https://github.com/SentientFlow-Solutions/open-webui-corethesis.git
cd open-webui-corethesis

# 2. Install deps (uses uv per upstream)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync

# 3. Build frontend
npm ci
npm run build  # output lands in build/

# 4. systemd unit
cat >/etc/systemd/system/open-webui.service <<'UNIT'
[Unit]
Description=Open WebUI (CoreThesis fork)
After=network-online.target openclaw-gateway.service
Wants=openclaw-gateway.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/open-webui-corethesis
EnvironmentFile=/etc/open-webui.env
ExecStart=/opt/open-webui-corethesis/.venv/bin/uvicorn open_webui.main:app \
  --host 127.0.0.1 --port 18181 --workers 2
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

# 5. /etc/open-webui.env  --- secrets + module config
cat >/etc/open-webui.env <<'ENV'
WEBUI_SECRET_KEY=<long random string>
DATABASE_URL=sqlite:////opt/open-webui-corethesis/data/webui.db
# OpenClaw module
OPENCLAW_BIN=/usr/bin/openclaw
OPENCLAW_DEFAULT_AGENT=main
OPENCLAW_TIMEOUT_SECONDS=600
ENV

systemctl daemon-reload
systemctl enable --now open-webui.service
journalctl -u open-webui.service -f   # watch startup
```

### Traefik route

Mirror of the existing `openclaw-gateway` rule. Create
`/data/coolify/proxy/dynamic/open-webui.yaml`:

```yaml
http:
  routers:
    open-webui:
      rule: "Host(`chat.sentientflowsolutions.com`)"
      entryPoints: [https]
      service: open-webui
      tls:
        certResolver: letsencrypt
  services:
    open-webui:
      loadBalancer:
        servers:
          - url: "http://host.docker.internal:18181"
```

Make sure DNS `A` record for `chat.sentientflowsolutions.com` points at the
box. SSL is handled by the Coolify Traefik with Let's Encrypt.

### First-boot checks

```bash
# Backend health
curl -fsSL https://chat.sentientflowsolutions.com/api/v1/openclaw/health
# Expected: {"ok": true, "binary": "/usr/bin/openclaw", "version": "OpenClaw 2026.5.22..."}

# Agents list (needs a logged-in admin token; easier to test from the UI)
```

Then open the UI, sign in, click the **OpenClaw** entry in the sidebar, pick
an agent, send a message. The reply is the agent's `--json` output parsed for
its reply field.

## Docker option (NOT recommended for this host)

If you must run Open WebUI in Docker, the container needs `openclaw` on its
PATH **with all of its Node.js dependencies**. The easiest variants:

1. **Sidecar adapter.** Run a small Node container that hosts the gateway CLI
   behind an HTTP endpoint. Point `OPENCLAW_BIN` at a wrapper that POSTs to
   the sidecar. Keeps the Open WebUI image vanilla.
2. **Custom image.** Fork the Open WebUI Dockerfile, `RUN npm i -g openclaw`,
   set `OPENCLAW_BIN=openclaw`. The gateway URL/token can be passed in via
   env so the embedded CLI talks to the gateway service.

Either path also needs `host.docker.internal:18789` (or a Docker network) so
the in-container `openclaw` can reach the gateway.

## Rebasing on upstream

The OpenClaw module is intentionally scoped to **one new router + one new
route folder + one sidebar diff**, so upstream rebases stay quiet:

```bash
git fetch upstream
git rebase upstream/main         # or a newer release tag
# resolve conflicts (typically only in Sidebar.svelte if upstream reshuffles
# the menu icons; the rest of the diff is in new files)
```

Track upstream at: <https://github.com/open-webui/open-webui>
