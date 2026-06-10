"""Research database connection — single entry point, fail-fast.

All formal research writes MUST use connect_research_db(required=True).
If the DB doesn't exist, it raises immediately — no silent skips.
"""

import duckdb
from loguru import logger

from config.settings import get_settings


class ResearchDBUnavailable(RuntimeError):
    """Raised when research.duckdb is required but missing."""


def connect_research_db(required: bool = True) -> duckdb.DuckDBPyConnection | None:
    """Connect to research database.

    Args:
        required: If True, raises ResearchDBUnavailable when DB is missing.
                  If False, returns None (for optional/diagnostic contexts only).

    Returns:
        DuckDB connection or None.
    """
    db_path = get_settings().research_ledger_path
    if not db_path.exists():
        if required:
            raise ResearchDBUnavailable(
                f"research.duckdb not found at {db_path}. "
                "Run `python scripts/init_research_db.py --seed` before research runs."
            )
        logger.warning(f"research.duckdb not found at {db_path}, skipping DB operation")
        return None
    return duckdb.connect(str(db_path))
