# AirBrief — developer shortcuts. Every target is a thin wrapper around raw commands, so on a
# machine without `make` (plain Windows) you can copy the command lines straight from here.
#
# Native-first: Python 3.11+ and Node 20. No Docker needed (see DOCKER.md for the optional path).

PYTHON ?= python
PIP ?= $(PYTHON) -m pip
NPM ?= npm
API_HOST ?= 127.0.0.1
API_PORT ?= 8000
WEB_HOST ?= 127.0.0.1
WEB_PORT ?= 3000
REPO_NAME ?= AirBrief

.DEFAULT_GOAL := help

.PHONY: install install-native samples datapack-demo datapack-real migrate api worker web dev \
        test test-native test-network lint typecheck contracts web-check e2e demo clean help

install: ## Install the core + API + dev tools (editable) and the frontend packages
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[api,dev]"
	cd frontend && $(NPM) install

install-native: ## Build and install the optional C++ kernel (needs CMake 3.18+ and a C++17 compiler)
	$(PIP) install ./native
	$(PYTHON) -c "import airbrief_core._native as n; print('native backend:', n.BACKEND)"

samples: ## Generate every sample file into samples/
	$(PYTHON) -m seed.samples --dest samples/

datapack-demo: ## Build the deterministic, offline synthetic data pack named "demo"
	$(PYTHON) -m seed.datapack --synthetic --name demo

datapack-real: ## Download the real Bengaluru data pack (~50 km clip; needs internet)
	$(PYTHON) -m seed.datapack --region bengaluru

migrate: ## Apply database migrations (the API also does this on start when AUTO_MIGRATE=true)
	alembic upgrade head

api: ## Run the API with auto-reload on http://localhost:8000
	uvicorn api.main:app --reload --host $(API_HOST) --port $(API_PORT)

worker: ## Run the optional standalone job worker (only needed with JOB_MODE=external)
	$(PYTHON) -m api.worker

web: ## Run the frontend dev server on http://localhost:3000
	cd frontend && $(NPM) run dev -- -H $(WEB_HOST) --port $(WEB_PORT)

dev: ## Run API and frontend together (Ctrl-C stops both)
	$(MAKE) -j2 api web

test: ## Python tests without the native kernel or network, with coverage
	pytest -m "not native and not network" --cov=airbrief_core --cov=api --cov-report=term-missing

test-native: ## C++ <-> Python parity tests (skipped if the kernel is not built)
	pytest -m native

test-network: ## Tests that touch the network (data sources, FlightMD fetch); never run in CI
	pytest -m network --run-network

lint: ## Ruff lint (rules E, F, W, B)
	ruff check .

typecheck: ## mypy over the core, the API and the seed scripts
	mypy airbrief_core api seed

contracts: ## Lint the protobuf contracts (needs the buf CLI)
	cd contracts/proto && buf lint

web-check: ## Frontend lint, type check and production build
	cd frontend && $(NPM) run lint
	cd frontend && $(NPM) run typecheck
	cd frontend && $(NPM) run build

e2e: ## Playwright + axe end-to-end tests (API on :8000 and web on :3000 must be running)
	cd frontend && PLAYWRIGHT_BROWSERS_PATH=0 ./node_modules/.bin/playwright install chromium
	cd frontend && PLAYWRIGHT_BROWSERS_PATH=0 $(NPM) run e2e

demo: ## Build the static GitHub Pages demo into frontend/out/
	$(PYTHON) -m seed.datapack --synthetic --name demo
	$(PYTHON) -m seed.demo_export --out frontend/public/demo
	cd frontend && NEXT_OUTPUT=export NEXT_PUBLIC_DEMO=1 NEXT_PUBLIC_BASE_PATH=/$(REPO_NAME) $(NPM) run build

clean: ## Remove build output, caches and generated demo data (keeps data/ and .env)
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov
	rm -rf native/build native/dist native/*.egg-info
	rm -rf frontend/.next frontend/out frontend/public/demo frontend/test-results frontend/playwright-report
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +

help: ## List the targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'
