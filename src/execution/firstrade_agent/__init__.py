"""Firstrade 模拟盘浏览器自动化 agent(Playwright,不走任何券商 API)。

状态标注(诚实原则:不写就别假装写了):
- 安全闸(PAPER_ONLY/kill-switch/审计日志)与人类节奏:已实现并有单测。
- 页面选择器:**未经实盘核验**(config/execution/firstrade_selectors.yaml 全部
  verified: false)。在 operator 首次人工登录并逐个核验前,reader/trader 会
  主动拒跑,不会假装能用。核验流程见 scripts/exec_login.py。
"""
