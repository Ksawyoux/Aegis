.PHONY: db-up db-down migrate downgrade test lint

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
