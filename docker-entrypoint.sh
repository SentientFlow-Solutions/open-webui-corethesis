#!/usr/bin/env bash
# Container entrypoint for the CoreThesis Open WebUI fork.
#
# Writes /root/.openclaw/openclaw.json so the bundled openclaw CLI connects to
# the gateway running on the *host* (not inside this container). Required env:
#   OPENCLAW_GATEWAY_URL    e.g. ws://host.docker.internal:18789
#   OPENCLAW_GATEWAY_TOKEN  the gateway's auth token
# Then execs the upstream backend start.sh.

set -e

mkdir -p /root/.openclaw

if [[ -n "${OPENCLAW_GATEWAY_URL:-}" && -n "${OPENCLAW_GATEWAY_TOKEN:-}" ]]; then
  python3 - <<'PY'
import json, os, pathlib
cfg_path = pathlib.Path("/root/.openclaw/openclaw.json")
cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
gw = cfg.setdefault("gateway", {})
gw["mode"] = "remote"
gw["remote"] = {"url": os.environ["OPENCLAW_GATEWAY_URL"]}
auth = gw.setdefault("auth", {})
auth["mode"] = "token"
auth["token"] = os.environ["OPENCLAW_GATEWAY_TOKEN"]
cfg_path.write_text(json.dumps(cfg, indent=2))
PY
  echo "[openclaw] configured remote gateway: ${OPENCLAW_GATEWAY_URL}"
else
  echo "[openclaw] OPENCLAW_GATEWAY_URL/TOKEN not set — /api/v1/openclaw/* will return errors until configured"
fi

cd /app/backend
exec bash start.sh
