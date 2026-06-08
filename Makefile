.PHONY: help install install-dev test lint format typecheck deploy destroy clean local-up local-test local-down validate dry-run dlq-status dlq-redrive reconcile release merge-pr docs-properties docs-properties-check

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install production dependencies
	pip install -r requirements.txt

install-dev: ## Install development dependencies
	pip install -r requirements-dev.txt

test: ## Run unit tests
	pytest tests/unit -v

test-all: ## Run all tests including integration
	pytest tests/ -v

lint: ## Run linter
	ruff check src/ tests/

format: ## Auto-format code
	ruff format src/ tests/
	ruff check --fix src/ tests/

typecheck: ## Run type checker
	mypy src/

docs-properties: ## Regenerate docs/reference/hubspot-properties.md from src/hubspot/properties.py
	@.venv/bin/python scripts/generate_hubspot_properties_doc.py

docs-properties-check: ## Fail if docs/reference/hubspot-properties.md is out of date
	@.venv/bin/python scripts/generate_hubspot_properties_doc.py > /dev/null
	@if ! git diff --quiet docs/reference/hubspot-properties.md; then \
		echo "ERROR: docs/reference/hubspot-properties.md is out of date."; \
		echo "Run 'make docs-properties' and commit the result."; \
		git --no-pager diff docs/reference/hubspot-properties.md | head -40; \
		exit 1; \
	fi

deploy: ## Deploy infrastructure with Terraform
	cd terraform && terraform init && terraform apply

plan: ## Preview Terraform changes
	cd terraform && terraform init && terraform plan

destroy: ## Destroy all Terraform-managed infrastructure
	cd terraform && terraform destroy

package: ## Package Lambda functions
	@echo "Cleaning __pycache__ before packaging..."
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "Packaging Lambda functions from hashed lockfile..."
	@if [ ! -f requirements.lock ]; then \
		echo "requirements.lock missing; run: make lock"; exit 1; \
	fi
	pip install --platform manylinux2014_aarch64 --only-binary=:all: --implementation cp --python-version 3.12 --require-hashes -r requirements.lock -t package/python/
	cd package && zip -r ../lambda-layer.zip python/

lock: ## Regenerate requirements.lock with hashes from uv.lock
	uv export --format=requirements-txt --no-dev --no-emit-project 2>/dev/null > requirements.lock
	@echo "requirements.lock regenerated. Commit it together with the requirements.txt change."

audit: ## Audit dependencies for known CVEs
	@command -v pip-audit >/dev/null 2>&1 || uv tool install pip-audit
	pip-audit -r requirements.lock --disable-pip

release: ## Cut a release (VERSION=X.Y.Z required); does not push
	@if [ -z "$(VERSION)" ]; then echo "usage: make release VERSION=2.2.0"; exit 1; fi
	@.venv/bin/python scripts/release.py $(VERSION)

merge-pr: ## Replay a GitHub PR onto GitLab main (PR=<number> required); does not push
	@if [ -z "$(PR)" ]; then echo "usage: make merge-pr PR=42"; exit 1; fi
	@./scripts/merge_github_pr.sh $(PR)

# ---------------------------------------------------------------------------
# DLQ operations
# ---------------------------------------------------------------------------

# Project-wide deployment identifiers. Overridable per target:
#   make <target> PROFILE=ops PREFIX=acme-cosell-stg REGION=us-east-2
PROFILE ?= default
PREFIX  ?= govwin-hubspot-prod
REGION  ?= us-east-1

# Env block threaded into every script target so load_config() resolves
# to the deployed table / secret names instead of the LocalStack-era
# defaults baked into src/config.py. Mirrors the env vars Terraform
# sets on the Lambdas.
SCRIPT_ENV := PYTHONPATH=. \
  AWS_PROFILE=$(PROFILE) \
  AWS_REGION=$(REGION) \
  SYNC_STATE_TABLE=$(PREFIX)-sync-state \
  ENTITY_MAPPINGS_TABLE=$(PREFIX)-entity-mappings \
  GOVWIN_SECRET_NAME=$(PREFIX)/govwin \
  GOVWIN_TOKENS_SECRET_NAME=$(PREFIX)/govwin-tokens \
  HUBSPOT_SECRET_NAME=$(PREFIX)/hubspot \
  HUBSPOT_WEBHOOK_SECRET_NAME=$(PREFIX)/hubspot-webhook

dlq-status: ## Print depth for every project DLQ (PROFILE, PREFIX, REGION overridable)
	@for q in $(PREFIX)-dlq $(PREFIX)-ace-submit-dlq $(PREFIX)-ace-update-dlq $(PREFIX)-govwin-sync-dlq; do \
		url=$$(aws sqs get-queue-url --queue-name $$q --profile $(PROFILE) --region $(REGION) --query QueueUrl --output text 2>/dev/null); \
		if [ -z "$$url" ]; then echo "$$q: (queue not found)"; continue; fi; \
		count=$$(aws sqs get-queue-attributes --queue-url $$url --attribute-names ApproximateNumberOfMessages --profile $(PROFILE) --region $(REGION) --query Attributes.ApproximateNumberOfMessages --output text); \
		echo "$$q: $$count msgs"; \
	done

dlq-redrive: ## Start an SQS redrive task for one DLQ (QUEUE=<dlq-name> required)
	@if [ -z "$(QUEUE)" ]; then echo "usage: make dlq-redrive QUEUE=$(PREFIX)-ace-submit-dlq [PROFILE=...]"; exit 1; fi
	@src_url=$$(aws sqs get-queue-url --queue-name $(QUEUE) --profile $(PROFILE) --region $(REGION) --query QueueUrl --output text); \
	src_arn=$$(aws sqs get-queue-attributes --queue-url $$src_url --attribute-names QueueArn --profile $(PROFILE) --region $(REGION) --query Attributes.QueueArn --output text); \
	echo "Starting redrive from $$src_arn..."; \
	aws sqs start-message-move-task --source-arn $$src_arn --profile $(PROFILE) --region $(REGION); \
	echo "Track progress: aws sqs list-message-move-tasks --source-arn $$src_arn --profile $(PROFILE) --region $(REGION)"

reconcile: ## Walk DDB + HubSpot + AWS for one govwin id (GOVWIN_ID=... required)
	@if [ -z "$(GOVWIN_ID)" ]; then echo "usage: make reconcile GOVWIN_ID=OPP12345 [CATALOG=Sandbox|AWS] [PREFIX=...]"; exit 1; fi
	@$(SCRIPT_ENV) .venv/bin/python scripts/reconcile.py $(if $(CATALOG),--catalog $(CATALOG),) $(GOVWIN_ID)

clean: ## Remove build artifacts
	rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache
	rm -rf dist build *.egg-info
	rm -rf package/ lambda-layer.zip
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ---------------------------------------------------------------------------
# Local Testing (Docker + LocalStack)
# ---------------------------------------------------------------------------

local-up: ## Start LocalStack with DynamoDB, Secrets Manager, SNS, SQS
	docker compose up -d localstack
	@echo "Waiting for LocalStack to be ready..."
	@docker compose exec localstack bash -c 'until curl -sf http://localhost:4566/_localstack/health; do sleep 1; done' > /dev/null 2>&1
	@echo "LocalStack is ready. Resources initialized."

local-test: ## Run tests against LocalStack (via Docker)
	docker compose run --rm test-runner

local-down: ## Stop LocalStack and clean up
	docker compose down -v

# ---------------------------------------------------------------------------
# Validation & Dry Run
# ---------------------------------------------------------------------------

validate: ## Validate credentials and connectivity (GovWin, HubSpot, AWS)
	@if [ ! -f .env ]; then echo "No .env file found. Copy .env.example to .env and fill in credentials."; exit 1; fi
	@set -a; . ./.env; set +a; $(SCRIPT_ENV) .venv/bin/python scripts/validate.py

dry-run: ## Dry-run sync: discover and map opps without writing to HubSpot
	@if [ ! -f .env ]; then echo "No .env file found. Copy .env.example to .env and fill in credentials."; exit 1; fi
	@set -a; . ./.env; set +a; $(SCRIPT_ENV) .venv/bin/python scripts/dry_run.py --limit 5
