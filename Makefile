SHELL := /bin/sh
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))

.PHONY: db-up db-down migrate downgrade test lint demo demo-live

db-up:
	docker compose up -d

db-down:
	docker compose down

migrate:
	uv run alembic upgrade head

downgrade:
	uv run alembic downgrade base

test:
	uv run pytest -q

lint:
	uv run ruff check .

demo:
	@command -v uv >/dev/null 2>&1 || { \
		echo "ERROR: uv is required; install uv and rerun make demo" >&2; exit 2; \
	}
	@cd "$(ROOT)" && uv sync --frozen --all-groups
	@cd "$(ROOT)" && uv run python -m aegis.release.demo offline

demo-live:
	@command -v uv >/dev/null 2>&1 || { \
		echo "ERROR: uv is required; install uv and rerun make demo-live" >&2; exit 2; \
	}
	@cd "$(ROOT)" && uv sync --frozen --all-groups
	@cd "$(ROOT)" && uv run python -m aegis.release.demo live
