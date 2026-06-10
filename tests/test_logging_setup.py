"""src.logging_setup 测试 — sink 配置 + 幂等（重复调用不叠 sink）。"""

import sys

from loguru import logger

import src.logging_setup as logging_setup


def _handler_count() -> int:
    return len(logger._core.handlers)  # noqa: SLF001 — loguru 无公开 API，测试专用


def test_setup_logging_idempotent(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # 日志文件写进临时目录，不污染仓库
    monkeypatch.setattr(logging_setup, "_configured", False)
    try:
        logging_setup.setup_logging("testunit")
        first = _handler_count()
        assert first == 2  # stderr + 文件，各一个

        # 重复调用：sink 数量不变
        logging_setup.setup_logging("testunit")
        logging_setup.setup_logging()
        assert _handler_count() == first

        # 文件 sink 落在 logs/ 下且带 name 前缀
        log_files = list((tmp_path / "logs").glob("testunit_*.log"))
        assert len(log_files) == 1
    finally:
        # 还原全局 logger，避免影响其他测试（_configured 由 monkeypatch 自动还原）
        logger.remove()
        logger.add(sys.stderr)
