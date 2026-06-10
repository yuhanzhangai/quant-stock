"""C5 读层:从 Firstrade 模拟盘页面读取账户/持仓快照(回采对账的数据源)。

⚠️ 依赖已核验选择器;在 operator 实盘核验前,SelectorRegistry.require 会直接
拒跑(UnverifiedSelectorError),不假装能读。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from src.execution.firstrade_agent.models import AccountSnapshot, Position
from src.execution.firstrade_agent.session import FirstradeSession

_EMPTY_MARKS = {"", "-", "--", "—", "n/a", "N/A"}


def _parse_money(text: str) -> Decimal | None:
    """'$12,345.67' → Decimal('12345.67');空/占位符 → None;解析失败 → None。"""
    cleaned = text.strip().replace("$", "").replace(",", "")
    if cleaned in _EMPTY_MARKS:
        return None
    if cleaned.startswith("(") and cleaned.endswith(")"):  # 会计负数 (123.45)
        cleaned = "-" + cleaned[1:-1]
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def read_account_snapshot(session: FirstradeSession) -> AccountSnapshot:
    """读取现金/购买力/持仓表,产出账户快照并进审计日志。"""
    session.kill.check()
    cash = _parse_money(session.read_text("account_cash"))
    buying_power = _parse_money(session.read_text("account_buying_power"))

    positions: list[Position] = []
    for row in session.read_table("positions_table"):
        if len(row) < 2:
            continue
        symbol = row[0].strip().upper()
        qty = _parse_money(row[1])
        if not symbol or qty is None:
            continue
        avg_price = _parse_money(row[2]) if len(row) > 2 else None
        market_value = _parse_money(row[3]) if len(row) > 3 else None
        positions.append(Position(symbol=symbol, qty=qty, avg_price=avg_price, market_value=market_value))

    snapshot = AccountSnapshot(cash=cash, buying_power=buying_power, positions=positions)
    session.audit.record(
        "account_snapshot",
        cash=str(cash) if cash is not None else None,
        buying_power=str(buying_power) if buying_power is not None else None,
        n_positions=len(positions),
    )
    return snapshot
