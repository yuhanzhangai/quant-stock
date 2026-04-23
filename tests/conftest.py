"""全局测试配置和 fixtures。"""

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_dir() -> Path:
    """返回 fixtures 目录路径。"""
    return FIXTURES_DIR


def load_fixture(name: str) -> dict:
    """加载 fixture JSON 文件。"""
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)
