.PHONY: sync format format-check lint typecheck test compose-config check

sync:
	uv sync --all-packages

format:
	uv run --all-packages ruff format .

format-check:
	uv run --all-packages ruff format --check .

lint:
	uv run --all-packages ruff check .

typecheck:
	uv run --all-packages pyright

test:
	uv run --all-packages pytest

compose-config:
	docker compose config --quiet

check: format-check lint typecheck test compose-config
