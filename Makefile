.PHONY: install test test-cov lint typecheck format dashboard clean

install:
	uv sync --all-extras

test:
	uv run pytest -v

# 需 pytest-cov(尚未入 dev 依赖,pyproject 在审冻结);用 uv run --with 临时注入,
# 待批次解冻后正式加入 dev 依赖
test-cov:
	uv run --with pytest-cov pytest --cov=src --cov-report=term

lint:
	uv run ruff check src/ tests/

# 转向手术(2026-06-10)后只剩跟单时代保留层;archive/ 不查
typecheck:
	uv run pyright src/storage src/research src/news src/notify src/logging_setup.py

format:
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

dashboard:
	uv run streamlit run dashboard/app.py

clean:
	rm -rf __pycache__ .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
