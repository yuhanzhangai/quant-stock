"""reader 测试:金额解析 + 账户快照组装(FakeSession,不碰浏览器)。"""

from decimal import Decimal
from pathlib import Path

import pytest

from src.execution.audit_log import AuditLog
from src.execution.firstrade_agent.reader import _parse_money, read_account_snapshot
from src.execution.human import HumanPacer
from src.execution.safety import ExecutionHalted, KillSwitch


class FakeReadSession:
    def __init__(self, tmp_path: Path):
        self.kill = KillSwitch(tmp_path / "KILL")
        self.audit = AuditLog(tmp_path / "audit.jsonl")
        self.pacer = HumanPacer(seed=1, sleep_fn=lambda _: None)
        self.texts = {"account_cash": "$10,000.00", "account_buying_power": "$20,000.00"}
        self.table = [
            ["NVDA", "10", "$100.50", "$1,200.00"],
            ["aapl", "5", "—", ""],
            ["", "3"],  # 脏行:无 symbol,应跳过
            ["TSLA", "n/a"],  # 脏行:无数量,应跳过
        ]

    def read_text(self, name: str) -> str:
        self.kill.check()
        return self.texts[name]

    def read_table(self, name: str) -> list[list[str]]:
        self.kill.check()
        return self.table


class TestParseMoney:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("$12,345.67", Decimal("12345.67")),
            ("100", Decimal("100")),
            ("(123.45)", Decimal("-123.45")),
            ("—", None),
            ("", None),
            ("n/a", None),
            ("garbage", None),
        ],
    )
    def test_parse(self, raw, expected):
        assert _parse_money(raw) == expected


class TestReadAccountSnapshot:
    def test_snapshot_assembled_and_dirty_rows_skipped(self, tmp_path):
        session = FakeReadSession(tmp_path)
        snap = read_account_snapshot(session)  # type: ignore[arg-type]
        assert snap.cash == Decimal("10000.00")
        assert snap.buying_power == Decimal("20000.00")
        assert [p.symbol for p in snap.positions] == ["NVDA", "AAPL"]
        assert snap.positions[0].avg_price == Decimal("100.50")
        assert snap.positions[1].avg_price is None
        assert snap.source == "firstrade_paper"

    def test_kill_engaged_blocks_read(self, tmp_path):
        session = FakeReadSession(tmp_path)
        session.kill.engage("停")
        with pytest.raises(ExecutionHalted):
            read_account_snapshot(session)  # type: ignore[arg-type]
