#!/usr/bin/env bash
# =====================================================================
# FarryOn — deploy/update script for a Hostinger VPS (single Docker host)
#
# First run and every update are the same command, executed at the repo
# root on the VPS:
#
#   ./deploy/hostinger/deploy.sh
#
# What it does:
#   1. sanity-checks .env (DOMAIN, POSTGRES_PASSWORD, JWT_SECRET)
#   2. pulls the latest code on the current branch (unless NO_PULL=1)
#   3. builds images and starts the stack (migrations run first, then the
#      backend, then Caddy — ordering enforced by docker-compose.prod.yml)
#   4. seeds/promotes the first super-admin if FIRST_SUPER_ADMIN_* are set
#   5. waits for the public health endpoint to come up
#
# Full runbook: docs/HOSTINGER_DEPLOYMENT.md
# =====================================================================
set -euo pipefail

cd "$(dirname "$0")/../.."                      # repo root
COMPOSE="docker compose -f docker-compose.prod.yml"

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

# ---- 1b. preflight: are ports 80/443 free (or already ours)? --------------
# On a shared VPS another app may already own the web ports. Detect that
# BEFORE compose tries to bind and fails half-deployed, and print exactly
# what is listening so the fix is obvious from CI logs alone.
if command -v ss >/dev/null 2>&1; then
    our_caddy=$(docker ps --filter "name=farryon" --filter "expose=443" -q 2>/dev/null || true)
    listeners=$(ss -tlnp 2>/dev/null | awk '$4 ~ /:(80|443)$/' || true)
    if [[ -n "$listeners" && -z "$our_caddy" ]]; then
        echo "ERROR: ports 80/443 are already in use by another service on this VPS:"
        echo "$listeners"
        echo
        echo "Running containers:"
        docker ps --format '  {{.Names}}  ->  {{.Ports}}' 2>/dev/null || true
        echo
        echo "FarryOn's Caddy needs 80+443 for TLS. Either stop/move that service,"
        echo "or integrate FarryOn behind it instead (share this output with your"
        echo "deployment assistant to generate the right reverse-proxy config)."
        exit 1
    fi
fi

# ---- 2. update code -------------------------------------------------------
if [[ "${NO_PULL:-0}" != "1" ]]; then
    echo "==> git pull (branch: $(git rev-parse --abbrev-ref HEAD))"
    git pull --ff-only
fi

# ---- 3. build + start (migrate -> backend -> caddy) -----------------------
echo "==> building images and starting the stack"
$COMPOSE up -d --build --remove-orphans

# ---- 4. seed first super-admin (idempotent) -------------------------------
if [[ -n "${FIRST_SUPER_ADMIN_EMAIL:-}" && -n "${FIRST_SUPER_ADMIN_PASSWORD:-}" ]]; then
    echo "==> seeding super-admin ${FIRST_SUPER_ADMIN_EMAIL}"
    $COMPOSE exec -T backend python -m scripts.seed_admin
fi

# ---- 5. wait for health ---------------------------------------------------
echo "==> waiting for https://${DOMAIN}/healthz"
for i in $(seq 1 30); do
    if curl -fsS --max-time 5 "https://${DOMAIN}/healthz" >/dev/null 2>&1; then
        echo "==> deploy OK — https://${DOMAIN} is live"
        $COMPOSE ps
        exit 0
    fi
    sleep 5
done

echo "WARNING: https://${DOMAIN}/healthz not reachable yet."
echo "  - First TLS issuance can take a minute; check: $COMPOSE logs caddy"
echo "  - Backend logs:                              $COMPOSE logs backend"
echo "  - DNS: 'dig +short ${DOMAIN}' must return this VPS's IP."
exit 1
