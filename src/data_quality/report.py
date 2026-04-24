"""数据质量报告生成。"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from loguru import logger

from src.data_quality.checks import CheckResult

DB_PATH = Path("data/meta/research.duckdb")
REPORT_DIR = Path("reports/data_quality")


def save_to_db(
    results: list[CheckResult],
    symbol: str,
    timeframe: str,
    data_version: str = "",
    start_ts: str = "",
    end_ts: str = "",
) -> None:
    """保存检查结果到 research.duckdb。"""
    if not DB_PATH.exists():
        logger.warning(f"DB not found: {DB_PATH}")
        return

    conn = duckdb.connect(str(DB_PATH))
    now = datetime.now(tz=UTC).isoformat()

    for r in results:
        check_id = f"dq_{uuid.uuid4().hex[:12]}"
        conn.execute(
            """
            INSERT INTO data_quality_checks
            (check_id, data_version, dataset, symbol, timeframe,
             start_ts, end_ts, check_name, status, severity,
             issue_count, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                check_id,
                data_version,
                "ohlcv",
                symbol,
                timeframe,
                start_ts or None,
                end_ts or None,
                r.check_name,
                r.status,
                r.severity,
                r.issue_count,
                json.dumps(r.details, ensure_ascii=False),
                now,
            ],
        )

    conn.close()
    logger.debug(f"Saved {len(results)} check results to DB for {symbol}/{timeframe}")


def save_report_json(
    results: list[CheckResult],
    symbol: str,
    timeframe: str,
) -> Path:
    """保存 JSON 报告。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    report = {
        "symbol": symbol,
        "timeframe": timeframe,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "overall": "fail" if any(r.status == "fail" for r in results) else "pass",
        "checks": [r.to_dict() for r in results],
    }

    filename = f"{symbol}_{timeframe}_{datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')}.json"
    path = REPORT_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    return path
