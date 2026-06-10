"""脚本冒烟防线：现役脚本逐个 import，抓顶层副作用。

每个现役脚本用 importlib.util.spec_from_file_location 加载，断言：
1. 不抛异常（import 即崩 = 脚本断裂）；
2. 加载耗时 < 5s（顶层若混入计算/网络/IO 重活会在这里暴露）。

实测说明（2026-06-10 逐个验证）：
- 全部现役脚本 import 时不连网络：bootstrap_data 顶层只建 rich Console，
  CCXT/OKX client 均在函数内构造，故无需降级为 AST 检查。
- 各脚本顶层 mkdir/logger.add 等文件副作用均在函数内，import 安全。
- 脚本依赖 .env / data/ 相对路径时全部是惰性（函数内才解析），
  import 阶段不需要临时 env；测试统一 chdir 到仓库根以与脚本约定一致。
- tsla_factor_iterate 顶层用 TextIOWrapper 重包 sys.stdout/stderr（UTF-8 兜底），
  会包住 pytest 的 capture 文件并在 GC 时连带关闭，故加载前后保存/恢复
  stdio，并对脚本新建的 wrapper 先 detach 再丢弃。
- out_of_sample_test / walk_forward / event_backtest / run_backtest 四个
  import 扁平策略模块（src.strategies.aggressive_momentum / trend_ma），
  自 fork 前 3500d92 起断裂，标记 xfail。
"""

import contextlib
import importlib.util
import io
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

# 加载耗时上限（秒）：抓顶层重副作用（计算/网络/大 IO）
MAX_LOAD_SECONDS = 5.0

# 现役脚本清单
ACTIVE_SCRIPTS = [
    "init_research_db",
    "create_experiment",
    "run_data_quality",
    "validate_strategy",
    "bootstrap_data",
    "build_data_manifest",
    "monte_carlo",
    "paper_runner",
    "top50_paper_monitor",
    "tsla_factor_iterate",
]

# 已知断裂：扁平策略 import 自 fork 前 3500d92 断裂，转向后随研究层处置
BROKEN_XFAIL_REASON = "扁平策略 import 自 fork 前 3500d92 断裂,转向后随研究层处置"
BROKEN_SCRIPTS = [
    "out_of_sample_test",
    "walk_forward",
    "event_backtest",
    "run_backtest",
]


def _load_script(name: str) -> float:
    """按文件路径加载脚本模块，返回加载耗时（秒）。异常原样抛出。

    加载期间守护 sys.stdout/stderr：脚本若在顶层重包 stdio（如 tsla_factor_iterate
    的 UTF-8 TextIOWrapper），先 detach 防止其 GC 时连带关闭 pytest capture 文件，
    再恢复原对象。
    """
    script_path = SCRIPTS_DIR / f"{name}.py"
    assert script_path.exists(), f"脚本不存在: {script_path}"

    saved_stdout, saved_stderr = sys.stdout, sys.stderr
    try:
        start = time.monotonic()
        spec = importlib.util.spec_from_file_location(f"_smoke_{name}", script_path)
        assert spec is not None and spec.loader is not None, f"无法创建 spec: {script_path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return time.monotonic() - start
    finally:
        for current, original in ((sys.stdout, saved_stdout), (sys.stderr, saved_stderr)):
            if current is not original and isinstance(current, io.TextIOWrapper):
                with contextlib.suppress(Exception):
                    current.detach()  # 与底层 buffer 解绑，GC 不再关闭原始流
        sys.stdout, sys.stderr = saved_stdout, saved_stderr


@pytest.mark.parametrize("name", ACTIVE_SCRIPTS)
def test_active_script_importable(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """现役脚本 import 不抛异常，且无顶层重副作用（耗时 < 5s）。"""
    monkeypatch.chdir(REPO_ROOT)  # 脚本约定从仓库根运行（.env / data/ 相对路径）
    elapsed = _load_script(name)
    assert elapsed < MAX_LOAD_SECONDS, f"{name} 加载耗时 {elapsed:.2f}s >= {MAX_LOAD_SECONDS}s，疑似顶层副作用"


@pytest.mark.parametrize(
    "name",
    [pytest.param(n, marks=pytest.mark.xfail(reason=BROKEN_XFAIL_REASON)) for n in BROKEN_SCRIPTS],
)
def test_broken_script_importable(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """已知断裂脚本：当前 import 必失败（ModuleNotFoundError），修复后会变 XPASS 提示移出清单。"""
    monkeypatch.chdir(REPO_ROOT)
    elapsed = _load_script(name)
    assert elapsed < MAX_LOAD_SECONDS
