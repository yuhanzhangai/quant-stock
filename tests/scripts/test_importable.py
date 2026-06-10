"""脚本冒烟防线：现役脚本逐个 import，抓顶层副作用。

每个现役脚本用 importlib.util.spec_from_file_location 加载，断言：
1. 不抛异常（import 即崩 = 脚本断裂）；
2. 加载耗时 < 5s（顶层若混入计算/网络/IO 重活会在这里暴露）。

2026-06-10 转向手术后:研究/回测层脚本已整体移入 archive/scripts/
(operator 拍板,方式 A 归档),现役清单缩减为跟单时代脚本。
"""

import importlib.util
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

# 加载耗时上限（秒）：抓顶层重副作用（计算/网络/大 IO）
MAX_LOAD_SECONDS = 5.0

# 现役脚本清单(.py;tsay.sh/restart_team.sh 为 bash 不在此列)
ACTIVE_SCRIPTS = [
    "medic",
    "replay_copytrade_rules_v0",
]


def _load_script(name: str) -> float:
    """按文件路径加载脚本模块，返回加载耗时（秒）。异常原样抛出。"""
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
        sys.stdout, sys.stderr = saved_stdout, saved_stderr


@pytest.mark.parametrize("name", ACTIVE_SCRIPTS)
def test_active_script_importable(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """现役脚本 import 不抛异常，且无顶层重副作用（耗时 < 5s）。"""
    monkeypatch.chdir(REPO_ROOT)  # 脚本约定从仓库根运行（.env / data/ 相对路径）
    elapsed = _load_script(name)
    assert elapsed < MAX_LOAD_SECONDS, f"{name} 加载耗时 {elapsed:.2f}s >= {MAX_LOAD_SECONDS}s，疑似顶层副作用"
