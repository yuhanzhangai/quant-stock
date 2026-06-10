"""执行层数据模型:下单意图 / 执行结果 / 账户快照(全部只针对模拟盘)。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(StrEnum):
    DRY_RUN = "dry_run"  # 表单已填但未提交(默认安全档)
    SUBMITTED = "submitted"  # 已在模拟盘提交,等待回采对账
    REJECTED = "rejected"  # 模拟盘拒单(预留:C6 选择器核验后接确认页解析 / C7 回采对账)
    HALTED = "halted"  # 安全闸拦截,未触达页面


class OrderIntent(BaseModel):
    """策略信号 → 模拟盘下单意图。只描述'想下什么单',不含任何执行细节。"""

    symbol: str
    side: Side
    qty: int = Field(gt=0)
    order_type: OrderType = OrderType.LIMIT
    limit_price: Decimal | None = Field(default=None, gt=0)
    note: str = ""  # 信号来源/策略名,进审计日志

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, v: str) -> str:
        # 当前只收 1-5 位纯字母 ticker;BRK.B 之类带点/横杠的类股暂拒
        # (宁可误杀,等 universe 真需要时再放开并补测试)。
        v = v.strip().upper()
        if not v.isalpha() or not (1 <= len(v) <= 5):
            raise ValueError(f"非法美股 ticker: {v!r}")
        return v

    @model_validator(mode="after")
    def _limit_needs_price(self) -> OrderIntent:
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("限价单必须给 limit_price")
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ValueError("市价单不应带 limit_price")
        return self


class OrderResult(BaseModel):
    """一次下单尝试的结果(与审计日志一一对应)。"""

    intent: OrderIntent
    status: OrderStatus
    detail: str = ""  # 页面确认文案 / 拒单原因 / 拦截原因
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Position(BaseModel):
    symbol: str
    qty: Decimal
    avg_price: Decimal | None = None
    market_value: Decimal | None = None


class AccountSnapshot(BaseModel):
    """模拟盘账户快照(C5 读层产物,用于回采对账)。"""

    source: str = "firstrade_paper"
    cash: Decimal | None = None
    buying_power: Decimal | None = None
    total_value: Decimal | None = None
    positions: list[Position] = Field(default_factory=list)
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
