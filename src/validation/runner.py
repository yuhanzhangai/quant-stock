"""Validation pipeline runner — 一个命令跑完所有 gate。"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from loguru import logger

from src.validation.gates import (
    GateResult,
    gate_baseline_backtest,
    gate_cost_stress,
    gate_data_quality,
    gate_event_backtest,
    gate_monte_carlo,
    gate_oos,
    gate_parameter_stability,
    gate_random_baseline,
    gate_walk_forward,
)

DB_PATH = Path("data/meta/research.duckdb")
REPORT_DIR = Path("data/research/validations")


def run_full_validation(
    df: Any,
    price: pd.Series,
    signal_func,
    params: dict,
    timeframe: str = "5m",
    stability_param: str = "trend_ma",
    stability_variations: list | None = None,
    run_id: str = "",
) -> list[GateResult]:
    """运行完整验证流水线（9 个 gate）。"""
    logger.info("=" * 50)
    logger.info("Starting full validation pipeline")
    logger.info("=" * 50)

    results: list[GateResult] = []

    # Gate 1: Data Quality
    logger.info("\n[Gate 1/9] Data Quality")
    r = gate_data_quality(df, timeframe)
    results.append(r)
    logger.info(f"  → {r.status.upper()}")
    if r.status == "fail":
        logger.error("Data quality FAILED — stopping pipeline")
        return results

    # Gate 2: Baseline Backtest
    logger.info("\n[Gate 2/9] Baseline Backtest")
    r = gate_baseline_backtest(price, signal_func, params)
    results.append(r)
    logger.info(f"  → {r.status.upper()}")

    # Gate 3: Cost Stress
    logger.info("\n[Gate 3/9] Cost Stress")
    r = gate_cost_stress(price, signal_func, params)
    results.append(r)
    logger.info(f"  → {r.status.upper()}")

    # Gate 4: Out of Sample
    logger.info("\n[Gate 4/9] Out of Sample")
    r = gate_oos(price, signal_func, params)
    results.append(r)
    logger.info(f"  → {r.status.upper()}")

    # Gate 5: Walk Forward
    logger.info("\n[Gate 5/9] Walk Forward")
    r = gate_walk_forward(price, signal_func, params)
    results.append(r)
    logger.info(f"  → {r.status.upper()}")

    # Gate 6: Random Baseline
    logger.info("\n[Gate 6/9] Random Baseline")
    r = gate_random_baseline(price, signal_func, params, n_random=50)
    results.append(r)
    logger.info(f"  → {r.status.upper()}")

    # Gate 7: Monte Carlo
    logger.info("\n[Gate 7/9] Monte Carlo")
    r = gate_monte_carlo(price, signal_func, params, n_sims=200)
    results.append(r)
    logger.info(f"  → {r.status.upper()}")

    # Gate 8: Event Backtest
    logger.info("\n[Gate 8/9] Event Backtest")
    r = gate_event_backtest(price, signal_func, params)
    results.append(r)
    logger.info(f"  → {r.status.upper()}")

    # Gate 9: Parameter Stability
    logger.info("\n[Gate 9/9] Parameter Stability")
    if stability_variations is None:
        stability_variations = [120, 150, 180, 210, 240]
    r = gate_parameter_stability(price, signal_func, params, stability_param, stability_variations)
    results.append(r)
    logger.info(f"  → {r.status.upper()}")

    # Summary
    _print_summary(results)

    return results


def save_validation_report(
    results: list[GateResult],
    run_id: str = "",
    strategy_name: str = "",
) -> Path:
    """保存验证报告到 JSON 和 DB。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    total = len(results)

    report = {
        "run_id": run_id,
        "strategy_name": strategy_name,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "summary": {
            "total_gates": total,
            "passed": passed,
            "failed": failed,
            "overall": "PASS" if failed == 0 else "FAIL",
        },
        "gates": [r.to_dict() for r in results],
    }

    # Save JSON
    report_name = run_id if run_id else f"validation_{datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')}"
    report_dir = REPORT_DIR / f"run_id={report_name}"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "validation_report.json"

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info(f"Validation report saved: {report_path}")

    # Save to DB
    _save_to_db(results, run_id)

    return report_path


def _save_to_db(results: list[GateResult], run_id: str) -> None:
    """写入 validation_results 表。"""
    if not DB_PATH.exists():
        return

    conn = duckdb.connect(str(DB_PATH))
    now = datetime.now(tz=UTC).isoformat()

    for r in results:
        vid = f"val_{uuid.uuid4().hex[:12]}"
        conn.execute(
            """
            INSERT INTO validation_results
            (validation_id, run_id, gate_name, status, score, threshold, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [vid, run_id, r.gate_name, r.status, r.score, r.threshold, json.dumps(r.details), now],
        )

    conn.close()
    logger.debug(f"Saved {len(results)} gate results to DB")


def _print_summary(results: list[GateResult]) -> None:
    """打印验证摘要。"""
    logger.info("\n" + "=" * 50)
    logger.info("VALIDATION SUMMARY")
    logger.info("=" * 50)

    for r in results:
        icon = "✓" if r.status == "pass" else ("✗" if r.status == "fail" else "⚠")
        logger.info(f"  {icon} {r.gate_name:25s} | {r.status:7s} | score={r.score:.4f} thr={r.threshold:.4f}")

    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    logger.info(f"\n  RESULT: {passed} passed, {failed} failed out of {len(results)}")

    if failed == 0:
        logger.info("  → STRATEGY PASSES ALL GATES")
    else:
        logger.warning(f"  → STRATEGY FAILS {failed} GATE(S)")
