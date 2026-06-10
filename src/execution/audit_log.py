"""执行层审计日志:append-only JSONL,每个动作意图/结果一行,operator 可逐单审计。

宪法要求(MIGRATION_PLAN 安全闸):每单 operator 可审计。
约定:绝不记录任何凭据/密码/cookie 值;只记录动作语义与结果。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from src.execution.safety import PROJECT_ROOT

DEFAULT_AUDIT_LOG: Path = PROJECT_ROOT / "data/execution/audit.jsonl"

# 字段名里出现这些子串的一律拒绝写入,防止误把敏感信息记进审计日志。
# 注:这是 backstop,不是主防线 —— 主防线是凭据根本不进自动化代码路径。
_FORBIDDEN_KEY_PARTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "cookie",
    "token",
    "credential",
    "pin",
    "otp",
    "2fa",
    "mfa",
    "passphrase",
)


class AuditLog:
    """append-only JSONL 审计日志。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_AUDIT_LOG

    def record(self, event: str, **fields: Any) -> dict[str, Any]:
        """追加一条审计记录并返回该记录。敏感字段名直接拒绝。"""
        for key in fields:
            lowered = key.lower()
            if any(part in lowered for part in _FORBIDDEN_KEY_PARTS):
                raise ValueError(f"审计日志拒绝疑似敏感字段: {key}")
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            **fields,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        logger.info("audit: {} {}", event, fields if fields else "")
        return entry
