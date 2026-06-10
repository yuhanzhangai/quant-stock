"""模拟真人节奏:随机延时、击键节奏、提交前停顿。

封号灰区对策(CLAUDE.md 红线 6):单账号、人类节奏、可停。
所有延时统一经 :class:`HumanPacer` 产生,测试时注入 seed + 假 sleep 即可复现且零等待。
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class PacingProfile:
    """人类节奏参数(秒)。默认值偏保守:像一个不着急的散户。"""

    action_min: float = 0.8  # 相邻页面动作(点击/聚焦)之间
    action_max: float = 2.5
    type_char_min: float = 0.05  # 单字符击键间隔
    type_char_max: float = 0.18
    think_min: float = 2.0  # 重大动作(如提交订单)前的"想一想"
    think_max: float = 6.0
    page_settle_min: float = 1.5  # 页面跳转后等内容"看完"
    page_settle_max: float = 4.0


class HumanPacer:
    """产生类人随机延时。所有等待都从这里走,方便统一审计与测试。"""

    def __init__(
        self,
        profile: PacingProfile | None = None,
        seed: int | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.profile = profile or PacingProfile()
        self._rng = random.Random(seed)
        self._sleep = sleep_fn

    def _wait(self, lo: float, hi: float) -> float:
        # 三角分布:多数落在中间,偶尔偏快/偏慢,比均匀分布更像人。
        delay = self._rng.triangular(lo, hi)
        self._sleep(delay)
        return delay

    def between_actions(self) -> float:
        return self._wait(self.profile.action_min, self.profile.action_max)

    def before_commit(self) -> float:
        """提交订单等重大动作前的犹豫停顿。"""
        return self._wait(self.profile.think_min, self.profile.think_max)

    def after_navigation(self) -> float:
        return self._wait(self.profile.page_settle_min, self.profile.page_settle_max)

    def keystroke_delays(self, text: str) -> list[float]:
        """为一段文本生成逐字符击键间隔(秒),长度 == len(text)。"""
        p = self.profile
        return [self._rng.triangular(p.type_char_min, p.type_char_max) for _ in text]

    def pause_keystroke(self, delay: float) -> None:
        """执行单次击键间隔等待(由 session 在逐字符输入时调用)。"""
        self._sleep(delay)
