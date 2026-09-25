# Cotorra — common tasks. `make help` lists them.
.DEFAULT_GOAL := help
.PHONY: help install dev serve test lint links fmt docker up down scale cost doctor vertex clean

PY ?= python

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install with the Gemini engine
	$(PY) -m pip install -e ".[gemini]"

dev: ## Install everything needed to develop and test
	$(PY) -m pip install -e ".[dev,gemini]"

serve: ## Run the gateway locally
	cotorra serve

test: ## Run the test suite (no network, no API key)
	$(PY) -m pytest -q

lint: ## Static checks
	$(PY) -m ruff check src tests scripts

links: ## Verify every internal doc link resolves
	$(PY) scripts/check_links.py

fmt: ## Auto-fix what ruff can
	$(PY) -m ruff check --fix src tests scripts
	$(PY) -m ruff format src tests scripts

docker: ## Build the container image
	docker build -t cotorra:local .

up: ## Start the whole thing with Docker Compose
	docker compose up --build

down: ## Stop it and remove the containers
	docker compose down

scale: ## Load test: 10 stages in parallel against a running gateway
	$(PY) scripts/scale_test.py --sessions 10 --seconds 60

cost: ## Estimate the cost of a 30-talk event
	$(PY) scripts/cost_model.py --talks 30 --hours 1 --languages 2

vertex: ## Point Cotorra at Vertex AI (needed to spend Google Cloud credits)
	$(PY) scripts/setup_vertex.py

doctor: ## Check this machine before an event
	cotorra doctor

clean: ## Remove transcripts, exports and caches
	rm -rf data/transcripts data/sessions.json .pytest_cache .ruff_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
