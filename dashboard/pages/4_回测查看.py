"""回测查看页面。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(page_title="回测查看", page_icon="📊", layout="wide")
st.title("📊 回测查看")

reports_dir = Path("reports")


@st.cache_data(ttl=30)
def get_reports() -> list[Path]:
    if not reports_dir.exists():
        return []
    return sorted(reports_dir.glob("*.html"), reverse=True)


reports = get_reports()

if not reports:
    st.warning("暂无回测报告，请先运行 `python scripts/run_backtest.py`")
    st.stop()

# 侧边栏
with st.sidebar:
    selected_report = st.selectbox(
        "选择报告",
        reports,
        format_func=lambda x: x.stem,
    )

# 显示报告
if selected_report:
    st.subheader(selected_report.stem)

    with open(selected_report, encoding="utf-8") as f:
        html_content = f.read()

    st.components.v1.html(html_content, height=950, scrolling=True)

    # 下载按钮
    st.download_button(
        label="下载报告 HTML",
        data=html_content,
        file_name=selected_report.name,
        mime="text/html",
    )
