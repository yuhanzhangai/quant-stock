"""审计日志测试:append-only JSONL + 敏感字段拒写。"""

import json

import pytest

from src.execution.audit_log import AuditLog


class TestAuditLog:
    def test_appends_jsonl_with_ts_and_event(self, tmp_path):
        log = AuditLog(tmp_path / "audit.jsonl")
        log.record("order_intent", symbol="NVDA", qty=10)
        log.record("order_dry_run", symbol="NVDA")
        lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["event"] == "order_intent" and first["symbol"] == "NVDA"
        assert "ts" in first

    @pytest.mark.parametrize(
        "bad_key",
        [
            "password",
            "user_password",
            "cookie_value",
            "api_secret",
            "auth_token",
            "credentials",
            "account_pin",
            "otp_code",
            "code_2fa",
            "mfa_secret",
            "passphrase",
        ],
    )
    def test_sensitive_field_names_rejected(self, tmp_path, bad_key):
        log = AuditLog(tmp_path / "audit.jsonl")
        with pytest.raises(ValueError, match="敏感"):
            log.record("login", **{bad_key: "x"})
        assert not (tmp_path / "audit.jsonl").exists()  # 拒绝时整条不落盘

    def test_non_serializable_falls_back_to_str(self, tmp_path):
        from decimal import Decimal

        log = AuditLog(tmp_path / "audit.jsonl")
        log.record("order_intent", price=Decimal("100.5"))
        entry = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8"))
        assert entry["price"] == "100.5"
