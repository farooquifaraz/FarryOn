# =====================================================================
# FarryOn — developer convenience targets
#
#   make backend   run the FastAPI backend with autoreload (AI_PROVIDER=mock)
#   make test      run the backend pytest suite (AI_PROVIDER=mock)
#   make up        bring up the local docker stack (backend + pg + obs)
#   make down      tear the local docker stack down
#   make mobile    run the Flutter app on the default device
#   make fmt       format + lint backend (ruff) and mobile (dart format)
#
# These mirror the commands used in CI (.github/workflows/ci.yml) and the
# deployment runbook (docs/DEPLOYMENT.md). Keep them in sync.
# =====================================================================

# Default provider for local dev / tests: no external API keys required.
export AI_PROVIDER ?= mock

# Pick `docker compose` (v2 plugin) when available, else legacy `docker-compose`.
COMPOSE := $(shell command -v docker-compose >/dev/null 2>&1 && echo docker-compose || echo "docker compose")

.DEFAULT_GOAL := help
.PHONY: help backend test up down logs mobile mobile-get fmt lint clean

help: ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

## ---------------------------------------------------------------- backend

backend: ## Run the FastAPI backend with autoreload on :8000.
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test: ## Run the backend test suite (mock provider, offline).
	cd backend && AI_PROVIDER=mock pytest -q

## ---------------------------------------------------------------- docker

up: ## Start the local stack: backend + Postgres + Prometheus + Grafana.
	$(COMPOSE) up -d --build

down: ## Stop the local stack (keeps named volumes).
	$(COMPOSE) down

logs: ## Tail logs from all services.
	$(COMPOSE) logs -f

## ---------------------------------------------------------------- mobile

mobile-get: ## Fetch Flutter package dependencies.
	cd mobile && flutter pub get

mobile: mobile-get ## Run the Flutter app on the default device.
	cd mobile && flutter run

## ---------------------------------------------------------------- quality

fmt: ## Format + lint backend (ruff) and mobile (dart format).
	cd backend && ruff format . && ruff check --fix .
	cd mobile && dart format . && flutter analyze

lint: ## Lint-only (no writes) — what CI enforces.
	cd backend && ruff check . && ruff format --check .
	cd mobile && flutter analyze

clean: ## Remove Python/Dart build & cache artifacts.
	find backend -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/.pytest_cache backend/.ruff_cache backend/.mypy_cache backend/htmlcov backend/.coverage
	cd mobile && flutter clean 2>/dev/null || true
