.PHONY: help
help: ## Show this help message
	@echo "Development Commands"
	@echo ""
	@echo "Usage:"
	@echo "  make [target]"
	@echo ""
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: init
init: ## Install project dependencies and init precommit hooks
	uv sync
	uvx pre-commit install

.PHONY: upgrade
upgrade: ## Upgrade python version, deps and precommit hooks
	uv python upgrade
	uv lock --upgrade
	uvx pre-commit autoupdate

.PHONY: format
format: ## Run code formatting
# 	https://docs.astral.sh/ruff/formatter/#sorting-imports
	uv run ruff check --select I --fix
	uv run ruff format

.PHONY: test
test: ## Run all tests
	uv run pytest tests -n auto

.PHONY: lint
lint: ## Run code linting checks
	uv run ruff check --fix

.PHONY: type-check
type-check: ## Run type checking
	uv run ty check

.PHONY: check
check: format lint type-check test  ## Run all code quality checks

.PHONY: run
run: ## Run service
	uv run alembic upgrade head
	uv run python -m src

.PHONY: run-reload
run-reload: ## Run service with auto-reload on code/migration changes
	uv run watchfiles "sh -c 'alembic upgrade head && exec python -m src'" src alembic

.PHONY: create_invite
create_invite: ## Create a specialist invite and print the deep-link
	uv run alembic upgrade head
	uv run python -m src.cli.create_invite

.PHONY: clean
clean: ## Clean up generated files and caches
	rm -rf .pytest_cache/
	rm -rf .ruff_cache/
	rm -rf .ty_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	find . -type d -name __pycache__ -delete
	find . -type f -name "*.pyc" -delete
