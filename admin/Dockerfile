# FarryOn edge gateway image: builds the admin SPA and serves it with Caddy,
# which also terminates TLS (automatic Let's Encrypt) and reverse-proxies the
# API + WebSocket paths to the backend container.
#
# Build context is ./admin (see docker-compose.prod.yml). The Caddyfile lives
# next to this Dockerfile so the gateway is fully self-contained.

# ---- stage 1: build the SPA -------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /admin

# Dependency layer first for caching.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

# ---- stage 2: Caddy edge ----------------------------------------------------
FROM caddy:2-alpine

COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /admin/dist /srv/admin

# 80/443 are published by compose; Caddy redirects HTTP -> HTTPS itself.
EXPOSE 80 443
