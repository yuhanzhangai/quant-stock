"""扫描 data/parquet/ 并生成数据清单 (data manifest)。

功能：
- 扫描所有 parquet 文件
- 解析路径结构 (dataset/inst_type/symbol/timeframe/year)
- 统计 row_count, start_ts, end_ts
- 生成 checksum (MD5)
- 写入 research.duckdb 的 data_manifest 表
- 输出 JSON/CSV 报告
- 生成 data_version 标识

Usage:
    python scripts/build_data_manifest.py
    python scripts/build_data_manifest.py --report  # 只输出报告不写数据库
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hashlib
import json
from datetime import UTC, datetime

import duckdb
import polars as pl
from loguru import logger

DATA_DIR = Path("data/parquet")
DB_PATH = Path("data/meta/research.duckdb")
REPORT_DIR = Path("reports/data_manifest")


def compute_checksum(file_path: Path) -> str:
    """计算文件 MD5 校验和。"""
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            md5.update(chunk)
    return md5.hexdigest()


def parse_ohlcv_path(file_path: Path) -> dict:
    """解析 OHLCV parquet 路径。

    路径格式: data/parquet/ohlcv/spot/BTC-USDT/5m/2026.parquet
    """
    parts = file_path.parts
    # Find 'ohlcv' index
    try:
        idx = list(parts).index("ohlcv")
    except ValueError:
        return {}

    inst_type = parts[idx + 1] if idx + 1 < len(parts) else ""
    symbol = parts[idx + 2] if idx + 2 < len(parts) else ""
    # Handle both formats:
    #   .../symbol/timeframe/year.parquet  (spot with year partition)
    #   .../symbol/timeframe.parquet       (swap without year partition)
    raw_tf = parts[idx + 3] if idx + 3 < len(parts) else ""
    if raw_tf.endswith(".parquet"):
        raw_tf = Path(raw_tf).stem

    return {
        "dataset": "ohlcv",
        "inst_type": inst_type,
        "symbol": symbol,
        "timeframe": raw_tf,
    }


def parse_funding_path(file_path: Path) -> dict:
    """解析 funding parquet 路径。

    路径格式: data/parquet/funding/BTC-USDT-SWAP.parquet
    """
    return {
        "dataset": "funding",
        "inst_type": "swap",
        "symbol": file_path.stem,
        "timeframe": "",
    }


def scan_parquet_file(file_path: Path) -> dict | None:
    """扫描单个 parquet 文件，提取元数据。"""
    try:
        df = pl.read_parquet(file_path)
        row_count = len(df)

        if row_count == 0:
            logger.warning(f"Empty file: {file_path}")
            return None

        # 确定时间列
        ts_col = None
        for col_name in ["timestamp", "funding_time"]:
            if col_name in df.columns:
                ts_col = col_name
                break

        start_ts = None
        end_ts = None
        if ts_col:
            ts_series = df[ts_col].cast(pl.Int64)
            start_ms = ts_series.min()
            end_ms = ts_series.max()
            if start_ms is not None:
                start_ts = datetime.fromtimestamp(start_ms / 1000, tz=UTC).isoformat()
            if end_ms is not None:
                end_ts = datetime.fromtimestamp(end_ms / 1000, tz=UTC).isoformat()

        # 解析路径
        rel_path = str(file_path)
        if "ohlcv" in rel_path:
            meta = parse_ohlcv_path(file_path)
        elif "funding" in rel_path:
            meta = parse_funding_path(file_path)
        else:
            meta = {"dataset": "unknown", "inst_type": "", "symbol": "", "timeframe": ""}

        checksum = compute_checksum(file_path)

        return {
            "file_path": str(file_path),
            "dataset": meta.get("dataset", ""),
            "venue": "okx",
            "inst_type": meta.get("inst_type", ""),
            "symbol": meta.get("symbol", ""),
            "timeframe": meta.get("timeframe", ""),
            "start_ts": start_ts,
            "end_ts": end_ts,
            "row_count": row_count,
            "checksum": checksum,
            "schema_version": "1.0.0",
        }
    except Exception as e:
        logger.error(f"Failed to scan {file_path}: {e}")
        return None


def build_manifest() -> list[dict]:
    """扫描所有 parquet 文件并构建 manifest。"""
    files = sorted(DATA_DIR.rglob("*.parquet"))
    logger.info(f"Found {len(files)} parquet files in {DATA_DIR}")

    manifest = []
    for f in files:
        result = scan_parquet_file(f)
        if result:
            manifest.append(result)
            logger.debug(
                f"  {result['symbol']}/{result['timeframe']} | "
                f"rows={result['row_count']} | "
                f"{result['start_ts'][:10] if result['start_ts'] else '?'} → "
                f"{result['end_ts'][:10] if result['end_ts'] else '?'}"
            )

    logger.info(f"Scanned {len(manifest)} valid files")
    return manifest


def generate_data_version() -> str:
    """生成数据版本号。"""
    now = datetime.now(tz=UTC)
    return f"manifest_{now.strftime('%Y%m%d_%H%M%S')}"


def write_to_db(manifest: list[dict], data_version: str) -> None:
    """写入 research.duckdb。"""
    if not DB_PATH.exists():
        logger.error(f"Database not found: {DB_PATH}. Run init_research_db.py first.")
        return

    conn = duckdb.connect(str(DB_PATH))
    now = datetime.now(tz=UTC).isoformat()

    # Clear existing manifest
    conn.execute("DELETE FROM data_manifest")

    for entry in manifest:
        conn.execute(
            """
            INSERT INTO data_manifest
            (file_path, dataset, venue, inst_type, symbol, timeframe,
             start_ts, end_ts, row_count, checksum, schema_version,
             created_at, updated_at, ingest_run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                entry["file_path"],
                entry["dataset"],
                entry["venue"],
                entry["inst_type"],
                entry["symbol"],
                entry["timeframe"],
                entry["start_ts"],
                entry["end_ts"],
                entry["row_count"],
                entry["checksum"],
                entry["schema_version"],
                now,
                now,
                data_version,
            ],
        )

    conn.close()
    logger.info(f"Written {len(manifest)} entries to data_manifest table")


def write_version_file(data_version: str) -> None:
    """写入最新数据版本号。"""
    version_file = Path("data/meta/latest_data_version.txt")
    version_file.parent.mkdir(parents=True, exist_ok=True)
    version_file.write_text(data_version)
    logger.info(f"Data version: {data_version}")


def write_reports(manifest: list[dict], data_version: str) -> None:
    """输出 manifest 报告。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # Summary stats
    total_files = len(manifest)
    total_rows = sum(e["row_count"] for e in manifest)
    symbols = sorted(set(e["symbol"] for e in manifest))
    timeframes = sorted(set(e["timeframe"] for e in manifest if e["timeframe"]))
    datasets = sorted(set(e["dataset"] for e in manifest))

    # Per-symbol coverage
    symbol_coverage = {}
    for e in manifest:
        key = f"{e['symbol']}/{e['timeframe']}" if e["timeframe"] else e["symbol"]
        if key not in symbol_coverage:
            symbol_coverage[key] = {
                "symbol": e["symbol"],
                "timeframe": e["timeframe"],
                "files": 0,
                "total_rows": 0,
                "start": e["start_ts"],
                "end": e["end_ts"],
            }
        sc = symbol_coverage[key]
        sc["files"] += 1
        sc["total_rows"] += e["row_count"]
        if e["start_ts"] and (not sc["start"] or e["start_ts"] < sc["start"]):
            sc["start"] = e["start_ts"]
        if e["end_ts"] and (not sc["end"] or e["end_ts"] > sc["end"]):
            sc["end"] = e["end_ts"]

    # Anomaly detection
    anomalies = []
    for e in manifest:
        if e["row_count"] < 10:
            anomalies.append({"file": e["file_path"], "issue": f"very few rows ({e['row_count']})"})

    report = {
        "data_version": data_version,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "summary": {
            "total_files": total_files,
            "total_rows": total_rows,
            "symbols": len(symbols),
            "timeframes": len(timeframes),
            "datasets": datasets,
        },
        "symbol_list": symbols,
        "timeframe_list": timeframes,
        "coverage": list(symbol_coverage.values()),
        "anomalies": anomalies,
        "files": manifest,
    }

    # JSON
    json_path = REPORT_DIR / f"{data_version}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    logger.info(f"Report written: {json_path}")

    # CSV summary
    csv_path = REPORT_DIR / f"{data_version}.csv"
    df = pl.DataFrame(manifest)
    df.write_csv(str(csv_path))
    logger.info(f"CSV written: {csv_path}")

    # Print summary
    logger.info("=== Data Manifest Summary ===")
    logger.info(f"  Files: {total_files}")
    logger.info(f"  Total rows: {total_rows:,}")
    logger.info(f"  Symbols: {len(symbols)}")
    logger.info(f"  Timeframes: {timeframes}")
    logger.info(f"  Datasets: {datasets}")
    if anomalies:
        logger.warning(f"  Anomalies: {len(anomalies)}")
        for a in anomalies:
            logger.warning(f"    {a['file']}: {a['issue']}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build data manifest")
    parser.add_argument("--report", action="store_true", help="Only generate report, skip DB write")
    args = parser.parse_args()

    manifest = build_manifest()
    data_version = generate_data_version()

    if not args.report:
        write_to_db(manifest, data_version)

    write_version_file(data_version)
    write_reports(manifest, data_version)
