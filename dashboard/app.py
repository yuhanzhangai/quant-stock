"""Streamlit 主入口 - 加密货币量化研究面板。"""

import sys
from pathlib import Path

# 让 import 能找到项目根目录
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

st.set_page_config(
    page_title="Crypto Research",
    page_icon="📊",
    layout="wide",
)

st.title("Crypto Research - 量化研究面板")

st.markdown("""
### 功能导航

使用左侧导航栏切换页面：

- **市场总览** - 当日涨跌排行、成交额排名
- **资金费监控** - 永续合约资金费率排行与历史
- **因子表现** - 因子时序图与统计
- **回测查看** - 回测结果展示

---

*数据源: OKX V5 API | 仅供研究分析，不构成投资建议*
""")
