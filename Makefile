.PHONY: install test test-cov lint typecheck format dashboard clean exec-kill exec-resume exec-login

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

# ── 执行层(Firstrade 模拟盘)安全操作 ──
exec-kill:  # 一键停:触发 kill-switch,agent 在下一个动作前必停
	bash scripts/exec_kill.sh

exec-resume:  # 解除 kill-switch(仅人工;agent 永不自动解除)
	rm -f $(CURDIR)/data/execution/KILL
	@echo "kill-switch released"

exec-login:  # 首次人工登录 Firstrade 留存登录态(凭据只人工输入)
	uv run python scripts/exec_login.py
