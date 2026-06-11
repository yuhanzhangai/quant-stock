"""Streamlit 主入口 - quant-stock 博主跟单面板。"""

import sys
from pathlib import Path

# 让 import 能找到项目根目录
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

st.set_page_config(
    page_title="quant-stock 跟单面板",
    page_icon="📊",
    layout="wide",
)

st.title("quant-stock — 博主跟单 · 模拟盘")

st.markdown("""
### 功能导航

使用左侧导航栏切换页面：

- **模拟盘监控** - 持仓 / 权益 / 今日成交 / agent 健康 / kill-switch 状态
- **订单留档** - 每笔订单可点开查看下单依据(博主原帖全文快照)

---

*信号源: stock-picker 诚实榜 PROVEN 喊单(只读)| Firstrade 模拟盘 PAPER_ONLY | 仅供研究分析，不构成投资建议*
""")
