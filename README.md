# quant-stock — 博主跟单 + 下单留档(Firstrade 模拟盘)

> 由 QuantLab(加密货币量化研究)fork 而来;**2026-06-10 转向:不再自研策略/回测,跟 stock-picker 诚实榜 PROVEN 博主的喊单**,全自动模拟盘执行 + 每单留档 + 绩效跟踪。
> **跟单执行 + 模拟盘验证工具,绝非投资建议。模拟盘无真金,edge 未经证明,不预设盈利。**

## 流水线

```
stock-picker 诚实榜 PROVEN 喊单(只读)
  → 信号适配层(src/signals:水位轮询/去重/冲突过滤/原帖快照)
  → 跟单规则引擎(入场/仓位/止损/21d 持有/翻空退出,rule_version 版本化)
  → Firstrade 模拟盘自动下单(Playwright 模拟真人,PAPER_ONLY + kill-switch)
  → 下单留档 ledger(append-only:每单附下单依据原帖全文快照)
  → 对账 + 三套账绩效(诚实榜口径 / 实际成交 / 延迟归因)→ Dashboard/Telegram
```

## 快速入口

| 想了解 | 看哪里 |
|---|---|
| 项目宪法 / 红线 / 团队 | `CLAUDE.md` |
| 现行路线(P1–P4) | `docs/ROADMAP.md` |
| 下单留档 ledger 设计(+双会签) | `docs/ORDER_LEDGER_SPEC.md` |
| stock-picker 对接口径 | `docs/INTEGRATION_NOTES.md` |
| 对账 / 绩效口径 | `docs/RECON_DESIGN_V0.md` / `docs/FOLLOW_PERF_SPEC.md` |
| 团队进度 | `team/PROGRESS_LOG.md` |
| QuantLab 研究时代(策略/回测/实验) | `archive/` + `docs/legacy/`(只读存证,不复活) |

## 5 分钟启动

前置:Python 3.11+ · [uv](https://docs.astral.sh/uv/)

```bash
make install     # uv sync --all-extras
make test        # pytest(基线:全部通过)
make dashboard   # Streamlit 面板(跟单监控页在 P4 转正)
```

## 项目结构

```
├── src/
│   ├── signals/       # 信号适配层(track/data 分支开发中)
│   ├── execution/     # Firstrade agent + ledger(track/exec 分支开发中)
│   ├── storage/       # DuckDB + Parquet
│   ├── news/          # Google News RSS + 情绪(美股原生)
│   ├── notify/        # Telegram
│   └── logging_setup.py
├── scripts/           # medic 守护 / tsay 通信 / 离线回放 / 团队重启
├── dashboard/         # 面板(pages/ 现役两页;reader/mock 数据层)
├── docs/              # 现行设计文档 + legacy/
├── archive/           # QuantLab 研究时代代码/配置/实验 + 停用研究页(git 历史可溯源)
├── team/              # 团队协作(roster / 进度 / 通信规范)
└── tests/             # 测试
```

## 技术栈

uv · pydantic-settings · loguru · Polars · DuckDB+Parquet(ledger append-only,读写分离)· Streamlit · Playwright(执行层)

## 红线(详见 CLAUDE.md)

- 绝不写真金下单代码,执行层只对 Firstrade **模拟盘**,可一键停
- stock-picker 库一律只读;原帖快照复制进本库留档
- 归档不复活;跟单规则变更必须走 rule_version
- 绩效诚实:PIT 局限/跟单延迟成本必须可见,离线回放只是 sane check
- 密钥/登录态/数据库一律不入 git

## License

MIT
