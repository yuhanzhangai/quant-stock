"""执行层安全闸:PAPER_ONLY 硬钉 + 文件式 kill-switch(一键停)。

铁律(CLAUDE.md 红线 2):执行层只对 Firstrade 模拟盘,绝不写真金下单代码。
本模块是所有执行动作的唯一前置闸门,设计原则:

1. ``PAPER_ONLY`` 是模块级 Final 常量 —— 不读配置、不读环境变量、没有任何运行时
   开关能把它关掉。改这一行等于改宪法,A1+A2 审计必查。
2. kill-switch 是文件信号:文件存在即全停。任何人(operator / Medic / 其它 pane)
   跑 ``make exec-kill`` 或 ``scripts/exec_kill.sh``(正典停机入口,自动锚定仓库根)
   就能一键停,不依赖 Python 进程是否还健康。手工 touch 也行,但必须 touch
   仓库根下的 ``data/execution/KILL``(本模块的 DEFAULT_KILL_FILE 绝对路径)。
3. agent 永不自动解除 kill-switch,只有人工 ``release()``。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from loguru import logger

# 硬钉:执行层只允许模拟盘。没有任何代码路径可以在运行时改变它。
PAPER_ONLY: Final[bool] = True

# 防御性黑名单:这些环境变量在本项目中不应存在;一旦出现且为真值,
# 视为有人试图引入真金开关,直接拒绝执行(宁可误杀)。
_LIVE_TRADING_ENV_VARS: Final[tuple[str, ...]] = (
    "FT_LIVE_TRADING",
    "EXEC_LIVE",
    "EXEC_LIVE_TRADING",
    "LIVE_TRADING",
)

_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})

# 仓库根(src/execution/safety.py 向上两级)。执行层所有默认路径都锚定到这里,
# 不随进程 cwd 漂移。
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

# kill 文件路径是安全机制的一部分,**故意不进 ExecSettings、不可被 .env 覆盖**:
# KillSwitch 默认值、scripts/exec_kill.sh、Makefile exec-resume 三方必须永远指向
# 同一个文件,否则"一键停"会静默失效(A1+A2 审计确认过的 fail-deadly 模式)。
DEFAULT_KILL_FILE: Final[Path] = PROJECT_ROOT / "data" / "execution" / "KILL"


class ExecutionHalted(RuntimeError):
    """安全闸不通过(kill-switch 触发 / PAPER_ONLY 被破坏),必须立刻停止一切执行。"""


def assert_paper_only() -> None:
    """校验 PAPER_ONLY 硬钉与环境,任何疑似真金信号都抛 ExecutionHalted。"""
    if PAPER_ONLY is not True:
        raise ExecutionHalted("PAPER_ONLY 硬钉被改动,按宪法拒绝执行任何动作")
    for var in _LIVE_TRADING_ENV_VARS:
        if os.environ.get(var, "").strip().lower() in _TRUTHY:
            raise ExecutionHalted(f"检测到疑似真金开关环境变量 {var},按宪法拒绝执行")


class KillSwitch:
    """文件式一键停:kill 文件存在 => 所有执行动作抛 ExecutionHalted。

    每个浏览器动作(导航/点击/输入/提交)之前都必须调用 :meth:`check`。
    """

    def __init__(self, kill_file: Path | None = None) -> None:
        self.kill_file = Path(kill_file) if kill_file is not None else DEFAULT_KILL_FILE

    @property
    def engaged(self) -> bool:
        return self.kill_file.exists()

    def engage(self, reason: str) -> None:
        """触发 kill-switch 并追加记录原因(append-only,保留历史)。"""
        self.kill_file.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).isoformat()
        with self.kill_file.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {reason}\n")
        logger.warning("kill-switch 已触发: {} ({})", reason, self.kill_file)

    def release(self) -> None:
        """解除 kill-switch。只允许人工调用;agent 代码路径里绝不调用。"""
        if self.kill_file.exists():
            self.kill_file.unlink()
            logger.warning("kill-switch 已人工解除 ({})", self.kill_file)

    def check(self) -> None:
        """统一安全闸:PAPER_ONLY 校验 + kill 文件检查,不通过抛 ExecutionHalted。"""
        assert_paper_only()
        if self.engaged:
            raise ExecutionHalted(f"kill-switch 已触发({self.kill_file}),停止一切执行")
