#!/usr/bin/env bash
# =====================================================================
# FarryOn — deploy/update script for a Hostinger VPS (single Docker host)
#
# First run and every update are the same command, executed at the repo
# root on the VPS (CI does exactly this over SSH):
#
#   ./deploy/hostinger/deploy.sh
#
# Modes (PROXY_MODE in .env):
#   standalone    (default) FarryOn's Caddy owns 80/443 + Let's Encrypt
#   behind-proxy  an existing proxy (e.g. Nginx Proxy Manager) owns 80/443;
#                 FarryOn serves plain HTTP on 172.17.0.1:${FARRYON_HTTP_PORT}
#
# What it does:
#   1. sanity-checks .env (DOMAIN, POSTGRES_PASSWORD, JWT_SECRET)
#   2. preflight-checks the ports the chosen mode needs
#   3. pulls the latest code (unless NO_PULL=1 — CI rsyncs instead)
#   4. builds images and starts the stack (migrate -> backend -> caddy)
#   5. seeds/promotes the first super-admin if FIRST_SUPER_ADMIN_* are set
#   6. waits for the health endpoint
#
# Full runbook: docs/HOSTINGER_DEPLOYMENT.md
# =====================================================================
set -euo pipefail

cd "$(dirname "$0")/../.."                      # repo root

# ---- 1. environment sanity ------------------------------------------------
if [[ ! -f .env ]]; then
    echo "ERROR: no .env at the repo root."
    echo "  cp deploy/hostinger/env.production.example .env  &&  edit it"
    exit 1
fi
# shellcheck disable=SC1091
set -a; source .env; set +a

fail=0
[[ -z "${DOMAIN:-}" || "${DOMAIN}" == "app.example.com" ]] \
    && { echo "ERROR: set DOMAIN in .env to your real domain."; fail=1; }
[[ -z "${POSTGRES_PASSWORD:-}" ]] \
    && { echo "ERROR: set POSTGRES_PASSWORD in .env (openssl rand -hex 24)."; fail=1; }
[[ -z "${JWT_SECRET:-}" || "${JWT_SECRET}" == "dev-insecure-change-me" ]] \
    && { echo "ERROR: set JWT_SECRET in .env (openssl rand -hex 32) — auth stays OFF otherwise."; fail=1; }
(( fail )) && exit 1

MODE="${PROXY_MODE:-standalone}"
case "$MODE" in
    standalone)   OVERLAY="deploy/hostinger/compose.standalone.yml" ;;
    behind-proxy) OVERLAY="deploy/hostinger/compose.behind-proxy.yml" ;;
    *) echo "ERROR: PROXY_MODE must be 'standalone' or 'behind-proxy' (got '$MODE')."; exit 1 ;;
esac
COMPOSE="docker compose -f docker-compose.prod.yml -f $OVERLAY"
BIND="${FARRYON_BIND:-172.17.0.1}"
HTTP_PORT="${FARRYON_HTTP_PORT:-8080}"
echo "==> mode: $MODE"

# ---- 2. preflight: are the ports this mode needs free (or already ours)? --
# Detect conflicts BEFORE compose tries to bind and fails half-deployed, and
# print exactly what is listening so the fix is obvious from CI logs alone.
if command -v ss >/dev/null 2>&1; then
    if [[ "$MODE" == "standalone" ]]; then
        our_caddy=$(docker ps --filter "name=farryon" --filter "publish=443" -q 2>/dev/null || true)
        listeners=$(ss -tlnp 2>/dev/null | awk '$4 ~ /:(80|443)$/' || true)
        needed="80/443"
    else
        our_caddy=$(docker ps --filter "name=farryon" --filter "publish=${HTTP_PORT}" -q 2>/dev/null || true)
        listeners=$(ss -tlnp 2>/dev/null | awk -v p=":${HTTP_PORT}\$" '$4 ~ p' || true)
        needed="$HTTP_PORT"
    fi
    if [[ -n "$listeners" && -z "$our_caddy" ]]; then
        echo "ERROR: port(s) $needed already in use by another service on this VPS:"
        echo "$listeners"
        echo
        echo "Running containers:"
        docker ps --format '  {{.Names}}  ->  {{.Ports}}' 2>/dev/null || true
        echo
        if [[ "$MODE" == "standalone" ]]; then
            echo "Another app owns the web ports. Set PROXY_MODE=behind-proxy in .env to"
            echo "run FarryOn behind it instead (docs/HOSTINGER_DEPLOYMENT.md §'Shared VPS')."
        else
            echo "Pick a free port via FARRYON_HTTP_PORT in .env."
        fi
        exit 1
    fi
fi

# ---- 3. update code -------------------------------------------------------
if [[ "${NO_PULL:-0}" != "1" ]]; then
    echo "==> git pull (branch: $(git rev-parse --abbrev-ref HEAD))"
    git pull --ff-only
fi

# ---- 4. build + start (migrate -> backend -> caddy) -----------------------
echo "==> building images and starting the stack"
$COMPOSE up -d --build --remove-orphans

# ---- 5. seed first super-admin (idempotent) -------------------------------
if [[ -n "${FIRST_SUPER_ADMIN_EMAIL:-}" && -n "${FIRST_SUPER_ADMIN_PASSWORD:-}" ]]; then
    echo "==> seeding super-admin ${FIRST_SUPER_ADMIN_EMAIL}"
    $COMPOSE exec -T backend python -m scripts.seed_admin
fi

# ---- 6. wait for health ---------------------------------------------------
if [[ "$MODE" == "standalone" ]]; then
    HEALTH_URL="https://${DOMAIN}/healthz"
else
    HEALTH_URL="http://${BIND}:${HTTP_PORT}/healthz"
fi
echo "==> waiting for ${HEALTH_URL}"
for i in $(seq 1 30); do
    if curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
        echo "==> deploy OK — FarryOn is up"
        $COMPOSE ps
        if [[ "$MODE" == "behind-proxy" ]]; then
            echo
            echo "Reminder: the public URL works once your reverse proxy forwards"
            echo "  https://${DOMAIN}  ->  http://${BIND}:${HTTP_PORT}"
            echo "with WebSocket support enabled (Nginx Proxy Manager: Proxy Host ->"
            echo "Websockets Support ON, SSL -> Request a new certificate + Force SSL)."
        fi
        exit 0
    fi
    sleep 5
done

echo "WARNING: ${HEALTH_URL} not reachable yet."
echo "  - Gateway logs:  $COMPOSE logs caddy"
echo "  - Backend logs:  $COMPOSE logs backend"
if [[ "$MODE" == "standalone" ]]; then
    echo "  - First TLS issuance can take a minute; DNS: 'dig +short ${DOMAIN}' must return this VPS's IP."
fi
exit 1
