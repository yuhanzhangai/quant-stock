.PHONY: install verify test lint format dashboard clean

install:
	uv sync --all-extras

verify:
	uv run python scripts/verify_okx.py

test:
	uv run pytest -v

lint:
	uv run ruff check src/ tests/
	uv run pyright src/

format:
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

dashboard:
	uv run streamlit run dashboard/app.py

clean:
	rm -rf __pycache__ .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
