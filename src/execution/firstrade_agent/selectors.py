"""页面选择器注册表:YAML 维护,逐个标注是否经过实盘核验。

诚实原则:Firstrade 页面结构未经实地核验前,所有选择器 verified: false。
``require()`` 对未核验选择器直接抛错 —— 代码不假装能用,逼着先走核验流程
(operator 跑 scripts/exec_login.py 人工登录,逐页核验后把 YAML 标 verified: true)。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class UnverifiedSelectorError(RuntimeError):
    """选择器未经实盘核验,拒绝在自动化流程中使用。"""


@dataclass(frozen=True)
class Selector:
    name: str
    css: str
    verified: bool
    note: str = ""


class SelectorRegistry:
    def __init__(self, selectors: dict[str, Selector]) -> None:
        self._selectors = selectors

    @classmethod
    def load(cls, path: Path) -> SelectorRegistry:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        selectors: dict[str, Selector] = {}
        for name, spec in raw.get("selectors", {}).items():
            selectors[name] = Selector(
                name=name,
                css=spec["css"],
                verified=bool(spec.get("verified", False)),
                note=spec.get("note", ""),
            )
        return cls(selectors)

    def get(self, name: str) -> Selector:
        if name not in self._selectors:
            raise KeyError(f"选择器未登记: {name}")
        return self._selectors[name]

    def require(self, name: str) -> Selector:
        """取一个**必须已核验**的选择器;未核验直接拒绝(不假装能用)。"""
        sel = self.get(name)
        if not sel.verified:
            raise UnverifiedSelectorError(
                f"选择器 {name!r} 未经实盘核验(verified: false)。"
                "先由 operator 跑 scripts/exec_login.py 人工核验页面,"
                "确认后在 config/execution/firstrade_selectors.yaml 标 verified: true。"
            )
        return sel

    def unverified_names(self) -> list[str]:
        return sorted(name for name, s in self._selectors.items() if not s.verified)
