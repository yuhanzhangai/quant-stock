"""初始化 research database (DuckDB)。

一键创建所有研究表：strategy_registry, experiment_runs,
backtest_runs, validation_results, data_manifest, data_quality_checks,
paper_sessions。

Usage:
    python scripts/init_research_db.py
    python scripts/init_research_db.py --reset  # 重建（慎用）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse

import duckdb
from loguru import logger

DB_PATH = Path("data/meta/research.duckdb")
SCHEMA_VERSION = "1.0.0"


TABLES = {
    "strategy_registry": """
        CREATE TABLE IF NOT EXISTS strategy_registry (
            strategy_name   TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            status          TEXT NOT NULL,
            direction       TEXT,
            timeframe       TEXT,
            symbols         TEXT,
            config_path     TEXT,
            code_path       TEXT,
            created_at      TIMESTAMP DEFAULT current_timestamp,
            updated_at      TIMESTAMP DEFAULT current_timestamp,
            notes           TEXT,
            PRIMARY KEY (strategy_name, strategy_version)
        );
    """,
    "experiment_runs": """
        CREATE TABLE IF NOT EXISTS experiment_runs (
            run_id          TEXT PRIMARY KEY,
            experiment_name TEXT NOT NULL,
            strategy_name   TEXT,
            strategy_version TEXT,
            hypothesis      TEXT,
            params_hash     TEXT,
            params_json     TEXT,
            config_path     TEXT,
            code_commit     TEXT,
            data_version    TEXT,
            train_start     TIMESTAMP,
            train_end       TIMESTAMP,
            test_start      TIMESTAMP,
            test_end        TIMESTAMP,
            cost_model      TEXT,
            slippage_model  TEXT,
            status          TEXT DEFAULT 'created',
            conclusion      TEXT,
            created_at      TIMESTAMP DEFAULT current_timestamp,
            notes           TEXT
        );
    """,
    "backtest_runs": """
        CREATE TABLE IF NOT EXISTS backtest_runs (
            backtest_id     TEXT PRIMARY KEY,
            run_id          TEXT,
            strategy_name   TEXT NOT NULL,
            symbol          TEXT NOT NULL,
            timeframe       TEXT NOT NULL,
            start_ts        TIMESTAMP,
            end_ts          TIMESTAMP,
            initial_cash    DOUBLE DEFAULT 50.0,
            fee_model       TEXT,
            slippage_model  TEXT,
            net_return      DOUBLE,
            sharpe          DOUBLE,
            sortino         DOUBLE,
            calmar          DOUBLE,
            max_drawdown    DOUBLE,
            profit_factor   DOUBLE,
            win_rate        DOUBLE,
            expectancy      DOUBLE,
            trade_count     INTEGER,
            avg_trade_return DOUBLE,
            median_trade_return DOUBLE,
            max_consecutive_losses INTEGER,
            created_at      TIMESTAMP DEFAULT current_timestamp
        );
    """,
    "validation_results": """
        CREATE TABLE IF NOT EXISTS validation_results (
            validation_id   TEXT PRIMARY KEY,
            run_id          TEXT,
            gate_name       TEXT NOT NULL,
            status          TEXT NOT NULL,
            score           DOUBLE,
            threshold       DOUBLE,
            details_json    TEXT,
            created_at      TIMESTAMP DEFAULT current_timestamp
        );
    """,
    "data_manifest": """
        CREATE TABLE IF NOT EXISTS data_manifest (
            file_path       TEXT PRIMARY KEY,
            dataset         TEXT,
            venue           TEXT,
            inst_type       TEXT,
            symbol          TEXT,
            timeframe       TEXT,
            start_ts        TIMESTAMP,
            end_ts          TIMESTAMP,
            row_count       INTEGER,
            checksum        TEXT,
            schema_version  TEXT,
            created_at      TIMESTAMP DEFAULT current_timestamp,
            updated_at      TIMESTAMP DEFAULT current_timestamp,
            ingest_run_id   TEXT
        );
    """,
    "data_quality_checks": """
        CREATE TABLE IF NOT EXISTS data_quality_checks (
            check_id        TEXT PRIMARY KEY,
            data_version    TEXT,
            dataset         TEXT,
            symbol          TEXT,
            timeframe       TEXT,
            start_ts        TIMESTAMP,
            end_ts          TIMESTAMP,
            check_name      TEXT NOT NULL,
            status          TEXT NOT NULL,
            severity        TEXT,
            issue_count     INTEGER DEFAULT 0,
            details_json    TEXT,
            created_at      TIMESTAMP DEFAULT current_timestamp
        );
    """,
    "paper_sessions": """
        CREATE TABLE IF NOT EXISTS paper_sessions (
            session_id      TEXT PRIMARY KEY,
            strategy_name   TEXT NOT NULL,
            strategy_version TEXT,
            config_path     TEXT,
            data_version    TEXT,
            start_ts        TIMESTAMP,
            end_ts          TIMESTAMP,
            initial_equity  DOUBLE DEFAULT 50.0,
            final_equity    DOUBLE,
            total_signals   INTEGER DEFAULT 0,
            accepted_trades INTEGER DEFAULT 0,
            rejected_signals INTEGER DEFAULT 0,
            net_pnl         DOUBLE,
            sharpe          DOUBLE,
            max_drawdown    DOUBLE,
            status          TEXT DEFAULT 'active',
            created_at      TIMESTAMP DEFAULT current_timestamp,
            notes           TEXT
        );
    """,
}


def init_db(reset: bool = False) -> None:
    """初始化 research database。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    if reset and DB_PATH.exists():
        logger.warning(f"Resetting database: {DB_PATH}")
        DB_PATH.unlink()

    conn = duckdb.connect(str(DB_PATH))

    for table_name, ddl in TABLES.items():
        conn.execute(ddl)
        count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        logger.info(f"Table '{table_name}' ready ({count} rows)")

    # Write schema version
    version_file = DB_PATH.parent / "schema_version.txt"
    version_file.write_text(SCHEMA_VERSION)
    logger.info(f"Schema version: {SCHEMA_VERSION}")

    # Verify all tables exist
    tables = conn.execute("SHOW TABLES").fetchall()
    table_names = [t[0] for t in tables]
    logger.info(f"Database initialized with {len(table_names)} tables: {table_names}")

    conn.close()
    logger.info(f"Research database ready: {DB_PATH}")


def seed_strategy_registry(db_path: Path = DB_PATH) -> None:
    """从 registry/strategies.yml 导入策略到数据库。"""
    import yaml

    registry_path = Path("registry/strategies.yml")
    if not registry_path.exists():
        logger.warning("registry/strategies.yml not found, skipping seed")
        return

    with open(registry_path, encoding="utf-8") as f:
        registry = yaml.safe_load(f)

    conn = duckdb.connect(str(db_path))

    count = 0
    for status_group in ["production", "candidate", "research"]:
        strategies = registry.get(status_group, [])
        if not strategies:
            continue
        for s in strategies:
            name = s.get("name", "")
            if not name:
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO strategy_registry
                (strategy_name, strategy_version, status, direction, timeframe, symbols, code_path, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    name,
                    "1.0.0",
                    status_group,
                    s.get("direction", ""),
                    s.get("timeframe", ""),
                    str(s.get("symbols", [])),
                    s.get("file", ""),
                    s.get("reason", ""),
                ],
            )
            count += 1

    conn.close()
    logger.info(f"Seeded {count} strategies into strategy_registry")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialize research database")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate all tables")
    parser.add_argument("--seed", action="store_true", help="Import strategies from registry/strategies.yml")
    args = parser.parse_args()

    init_db(reset=args.reset)

    if args.seed:
        seed_strategy_registry()
