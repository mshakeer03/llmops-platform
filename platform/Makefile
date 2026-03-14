.PHONY: dev test lint format migrate seed clean \
        redeploy-ui redeploy-api redeploy-all deploy-ui deploy-api

# ─── Local dev stack ──────────────────────────────────────────────────────────
dev:
	docker compose -f infra/docker-compose.yml up --build

dev-bg:
	docker compose -f infra/docker-compose.yml up --build -d

down:
	docker compose -f infra/docker-compose.yml down

# ─── Database migrations ──────────────────────────────────────────────────────
migrate:
	cd api && alembic upgrade head

migrate-down:
	cd api && alembic downgrade -1

migrate-new:
	@read -p "Migration name: " name; cd api && alembic revision --autogenerate -m "$$name"

# ─── Tests ────────────────────────────────────────────────────────────────────
test:
	cd api && python -m pytest tests/ -v --tb=short

test-cov:
	cd api && python -m pytest tests/ -v --cov=app --cov-report=term-missing

# ─── Code quality ─────────────────────────────────────────────────────────────
lint:
	cd api && python -m ruff check app/ tests/

format:
	cd api && python -m ruff format app/ tests/

typecheck:
	cd api && python -m mypy app/

pre-commit:
	pre-commit run --all-files

# ─── Clean ────────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

# ─── Hot redeploy helpers ─────────────────────────────────────────────────────
# deploy-ui: build → rebuild image → recreate container (≈2 min)
#   Use after any ui/src change.
deploy-ui:
	@echo "==> Building UI bundle..."
	cd ui && npm run build
	@echo "==> Rebuilding container image..."
	podman-compose build ui
	@echo "==> Recreating UI container..."
	podman rm -f llmops-ui 2>/dev/null || true
	podman-compose up -d ui
	@echo "✓ UI redeployed"

# deploy-api: copy changed Python files → run migrations → restart (≈10 s)
#   Use after any api/app change. Does NOT rebuild the image.
deploy-api:
	@echo "==> Copying API files to container..."
	podman cp api/app llmops-api:/app/
	podman cp api/alembic llmops-api:/app/
	@echo "==> Applying migrations..."
	podman exec llmops-api alembic upgrade head
	@echo "==> Restarting API container..."
	podman stop llmops-api && podman start llmops-api
	@echo "✓ API redeployed"

# redeploy-all: full rebuild of both API image and UI image + recreate
#   Use after dependency changes (requirements.txt, package.json) or first deploy.
redeploy-all:
	@echo "==> Full rebuild of all services..."
	podman-compose build api ui
	@echo "==> Recreating containers..."
	podman rm -f llmops-api llmops-ui 2>/dev/null || true
	podman-compose up -d api ui
	@echo "==> Applying migrations..."
	sleep 5
	podman exec llmops-api alembic upgrade head
	@echo "✓ Full redeploy complete"
