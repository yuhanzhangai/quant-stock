.PHONY: install verify-okx-legacy test test-cov lint typecheck quality format dashboard clean

install:
	uv sync --all-extras

# C2 退役:会真连 OKX,勿误触(仅 QuantLab 遗留排障用)
verify-okx-legacy:
	uv run python scripts/verify_okx.py

test:
	uv run pytest -v

# 需 pytest-cov(尚未入 dev 依赖,pyproject 在审冻结);用 uv run --with 临时注入,
# 待批次解冻后正式加入 dev 依赖
test-cov:
	uv run --with pytest-cov pytest --cov=src --cov-report=term

lint:
	uv run ruff check src/ tests/

# 只查 keep 层(34 错待修);strategies/replay/exchange 为 frozen/退役层不查
typecheck:
	uv run pyright src/backtest src/validation src/factors src/storage src/research src/ingestion src/data_quality

quality:
	uv run python scripts/run_data_quality.py --all

format:
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

dashboard:
	uv run streamlit run dashboard/app.py

clean:
	rm -rf __pycache__ .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
