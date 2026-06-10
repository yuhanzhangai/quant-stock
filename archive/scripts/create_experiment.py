"""创建新实验。

从模板生成实验配置，写入 experiments/active/ 并注册到 research.duckdb。

Usage:
    python scripts/create_experiment.py --name trailing_stop_test --template strategy_experiment
    python scripts/create_experiment.py --name trailing_stop_test --strategy minswing_v3
    python scripts/create_experiment.py --list          # 列出所有实验
    python scripts/create_experiment.py --conclude <name> --status rejected --reason "OOS failed"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import shutil
import subprocess
from datetime import UTC, datetime

import duckdb
import yaml
from loguru import logger

EXPERIMENTS_DIR = Path("experiments")
TEMPLATES_DIR = EXPERIMENTS_DIR / "templates"
DB_PATH = Path("data/meta/research.duckdb")


def create_experiment(name: str, template: str = "strategy_experiment", strategy: str = "") -> Path:
    """创建新实验。"""
    template_path = TEMPLATES_DIR / f"{template}.yml"
    if not template_path.exists():
        logger.error(f"Template not found: {template_path}")
        raise FileNotFoundError(f"Template not found: {template_path}")

    date = datetime.now(tz=UTC).strftime("%Y%m%d")
    filename = f"{date}_{name}.yml"
    dest = EXPERIMENTS_DIR / "active" / filename

    if dest.exists():
        logger.warning(f"Experiment already exists: {dest}")
        return dest

    shutil.copy(template_path, dest)

    # Pre-fill experiment_name and strategy
    with open(dest, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    config["experiment_name"] = name
    if strategy:
        config["strategy_name"] = strategy

    with open(dest, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

    # Register in DB
    _register_in_db(name, strategy)

    logger.info(f"Experiment created: {dest}")
    logger.info("Next steps:")
    logger.info("  1. Edit the file and write your hypothesis")
    logger.info("  2. Define success criteria")
    logger.info("  3. Run the experiment")
    logger.info("  4. Write conclusion")

    return dest


def conclude_experiment(name: str, status: str, reason: str) -> None:
    """结束实验：移动到 completed/ 或 rejected/。"""
    valid_statuses = {"accepted", "rejected", "inconclusive"}
    if status not in valid_statuses:
        logger.error(f"Invalid status: {status}. Must be one of: {valid_statuses}")
        return

    # Find the experiment file
    active_dir = EXPERIMENTS_DIR / "active"
    matches = list(active_dir.glob(f"*_{name}.yml"))
    if not matches:
        logger.error(f"No active experiment found matching: {name}")
        return

    source = matches[0]

    # Update conclusion in the file
    with open(source, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    config["conclusion"] = {
        "status": status,
        "reason": reason,
        "concluded_at": datetime.now(tz=UTC).isoformat(),
    }

    with open(source, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

    # Move to appropriate directory
    dest_dir = EXPERIMENTS_DIR / ("completed" if status == "accepted" else "rejected")
    dest = dest_dir / source.name
    source.rename(dest)

    # Update DB
    _update_db_status(name, status, reason)

    logger.info(f"Experiment concluded: {status}")
    logger.info(f"Moved to: {dest}")


def list_experiments() -> None:
    """列出所有实验。"""
    logger.info("=== Active Experiments ===")
    _list_dir(EXPERIMENTS_DIR / "active")

    logger.info("\n=== Completed Experiments ===")
    _list_dir(EXPERIMENTS_DIR / "completed")

    logger.info("\n=== Rejected Experiments ===")
    _list_dir(EXPERIMENTS_DIR / "rejected")

    # Also show DB summary
    if DB_PATH.exists():
        conn = duckdb.connect(str(DB_PATH))
        rows = conn.execute(
            "SELECT experiment_name, strategy_name, status, created_at "
            "FROM experiment_runs ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        conn.close()
        if rows:
            logger.info("\n=== DB Records ===")
            for r in rows:
                logger.info(f"  {r[0]:30s} | {r[1]:15s} | {r[2]:12s} | {str(r[3])[:10]}")


def _list_dir(path: Path) -> None:
    """列出目录中的 YAML 文件。"""
    if not path.exists():
        return
    files = sorted(path.glob("*.yml"))
    for f in files:
        with open(f, encoding="utf-8") as fh:
            config = yaml.safe_load(fh)
        exp_name = config.get("experiment_name", f.stem)
        strategy = config.get("strategy_name", "")
        hypothesis = config.get("hypothesis", "")[:60]
        logger.info(f"  {exp_name:40s} | {strategy:15s} | {hypothesis}")


def _register_in_db(name: str, strategy: str) -> None:
    """注册实验到 research.duckdb。"""
    if not DB_PATH.exists():
        return

    conn = duckdb.connect(str(DB_PATH))
    run_id = f"exp_{datetime.now(tz=UTC).strftime('%Y%m%d')}_{name}"
    commit = _get_git_commit()

    conn.execute(
        """
        INSERT INTO experiment_runs
        (run_id, experiment_name, strategy_name, code_commit, status, created_at)
        VALUES (?, ?, ?, ?, 'created', current_timestamp)
        """,
        [run_id, name, strategy, commit],
    )
    conn.close()
    logger.debug(f"Registered in DB: {run_id}")


def _update_db_status(name: str, status: str, reason: str) -> None:
    """更新实验状态。"""
    if not DB_PATH.exists():
        return

    conn = duckdb.connect(str(DB_PATH))
    conn.execute(
        """
        UPDATE experiment_runs
        SET status = ?, conclusion = ?
        WHERE experiment_name = ?
        """,
        [status, reason, name],
    )
    conn.close()


def _get_git_commit() -> str:
    """获取当前 git commit hash。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage experiments")
    parser.add_argument("--name", type=str, help="Experiment name")
    parser.add_argument("--template", type=str, default="strategy_experiment", help="Template name")
    parser.add_argument("--strategy", type=str, default="", help="Strategy name")
    parser.add_argument("--list", action="store_true", help="List all experiments")
    parser.add_argument("--conclude", type=str, help="Conclude an experiment by name")
    parser.add_argument("--status", type=str, help="Conclusion status: accepted/rejected/inconclusive")
    parser.add_argument("--reason", type=str, default="", help="Conclusion reason")
    args = parser.parse_args()

    if args.list:
        list_experiments()
    elif args.conclude:
        if not args.status:
            logger.error("--status is required when concluding")
        else:
            conclude_experiment(args.conclude, args.status, args.reason)
    elif args.name:
        create_experiment(args.name, args.template, args.strategy)
    else:
        parser.print_help()
