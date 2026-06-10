"""stock-picker 数据源路径 + 只读连接约定(信号适配层共享)。

铁律:stock-picker 两库(trackrecord.db / tweets.db)与诚实榜 CSV 一律只读;
我方落地数据(快照/水位状态)只写本仓 data/signals/(已 gitignore)。
"""

import sqlite3
from pathlib import Path

# --- stock-picker 侧(只读) ---
STOCK_PICKER_HOME = Path.home() / ".stock-picker-mcp"
TRACKRECORD_DB = STOCK_PICKER_HOME / "trackrecord.db"
TWEETS_DB = STOCK_PICKER_HOME / "tweets.db"

# 诚实榜 CSV 导出目录(按序探测:主发布点 → spm-web 发布点)
LEADERBOARD_EXPORT_DIRS = (
    Path.home() / "stock-picker-mcp" / "exports",
    Path.home() / "spm-web" / "exports",
)

# --- quant-stock 侧(我方可写) ---
SIGNALS_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "signals"

# 只读保护根:stock-picker 侧目录,任何我方写路径不得落在其下(防参数转置类 bug 的纵深防御)
READONLY_ROOTS = (
    STOCK_PICKER_HOME,
    Path.home() / "stock-picker-mcp",
    Path.home() / "spm-web",
)


def assert_writable_path(path: Path) -> Path:
    """写路径守卫:落在 stock-picker 只读侧(READONLY_ROOTS)直接 ValueError,fail loud。"""
    resolved = path.resolve()
    for root in READONLY_ROOTS:
        if resolved.is_relative_to(root.resolve()):
            raise ValueError(f"拒绝写入 stock-picker 只读侧路径: {path}(位于 {root})")
    return path


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """以只读模式打开 sqlite(URI mode=ro,任何写操作直接 OperationalError)。"""
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
