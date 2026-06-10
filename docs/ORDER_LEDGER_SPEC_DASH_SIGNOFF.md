# %Dash 会签意见 — ORDER_LEDGER_SPEC r2(§9 会签项)

> 出具:Dash(quant:3.0),2026-06-10。结论:**有条件 PASS** —— 视图层够用,但 1 项必须修订 + 2 项增表提案 + 2 项小改。
> 依据材料:`dashboard/pages_draft/`(untracked 草稿:`11_模拟盘监控.py`、`12_订单留档.py`、`ledger_mock.py`,schema 已对齐 r2)+ 下述锁实测。
> §1 锁实测为 Dash 自行复现(2026-06-10 两次独立运行同结果),**未经独立复核**(审核制度同日废止);文内附复现法,任何人可重跑核对。
> **Lead 裁决(2026-06-10,三项全采纳)**:①§2 读写分离(Exec writer 每循环收尾导出 parquet+export_meta);②account_daily+agent_runs 增表;③recon 结构化(与 Valid recon_runs/findings 对齐口径)。r2 冻结,批次落地后 r3 一次性吸收。

## 1. 【必须修订】Dash 读取方式:spec §2 "read_only 只读连接" 不可行,改读 parquet 导出

**实测**(DuckDB 1.5.3,2026-06-10 复现):进程 A 持普通写连接(模拟 Exec 常驻 writer)期间,进程 B `duckdb.connect(path, read_only=True)` 失败:
`IOException: IO Error: Could not set lock on file "...ledger.duckdb": Conflicting lock is held in ... (PID ...)`

复现(任何人可重跑,`uv run --with duckdb python repro.py`):

```python
import duckdb, subprocess, sys, tempfile, os, textwrap
db = os.path.join(tempfile.mkdtemp(), "ledger.duckdb")
w = duckdb.connect(db)            # 写连接不关(模拟 Exec 常驻 writer)
w.execute("create table t(i int)")
child = textwrap.dedent(f"""
    import duckdb
    try:
        c = duckdb.connect(r"{db}", read_only=True)
        print("READ_ONLY_OK", c.execute("select count(*) from t").fetchone())
    except Exception as e:
        print("READ_ONLY_FAIL:", type(e).__name__, str(e)[:200])
""")
r = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True)
print(r.stdout.strip(), "| duckdb", duckdb.__version__)
# 实测输出:READ_ONLY_FAIL: IOException ... Could not set lock ... | duckdb 1.5.3
```

即:只要 Exec writer 在线,Dash 任何直连(含 read_only)都拿不到锁;反之 Dash 若先占只读锁也会挡 writer。**结论:Dash 永不直连 `ledger.duckdb`。**

**提案**:复用 §2 已有的"每日全表导出 parquet 备份"作为 Dash 唯一数据面,并:
- 加 `export_meta`(导出时间戳 + 各表行数),面板自证数据新鲜度(草稿页已按此设计,顶栏显示"数据导出于 …")。
- 请 Lead/Exec 定导出频率:仅 EOD 一次,则盘中"agent 健康/订单现状"是隔夜数据,监控价值打折;建议**每个执行循环收尾顺手导出**(全表量级很小,秒级)。

## 2. 【增表提案】两个监控刚需数据源,r2 无落点

- **`account_daily`**(账户级 EOD 快照:`snapshot_date, snapshot_ts, total_value, cash, raw_text`):
  `positions_daily` 是逐票的,现金与已平仓的已实现盈亏无落点,**账户总权益/盈亏曲线拼不出来**。Firstrade Balances 页有 Total Account Value,Exec 抓持仓页时顺手可采。
- **`agent_runs`**(执行循环留档:`run_id, started_ts, finished_ts, outcome, kill_switch_state, error_text, note`):
  r2 中 `kill_switch_engaged` 只随订单事件快照,**无订单流动时 agent 死活、kill-switch 当前开关读不到**。这是 C8 监控页"agent 健康"的事实源,也是红线 6(出错先停、可一键停)的可观测性落点。

两表均 append-only,与 ledger 纪律一致。草稿页已按此 mock 并标注 `[PENDING-会签]`,定稿即换实现,页面代码不动。

## 3. 【小改建议】

- **recon 结果结构化**:§7 把 `recon=ok` 写在 `pdt_ledger.eod_snapshot` 的 `note`(自由文本)里,面板靠解析字符串判"对账异常→停新单",脆弱。建议 r3 给 eod_snapshot 加结构化字段(如 `recon_status TEXT CHECK (... IN ('ok','mismatch'))`)或独立原因码。
- **留档页读 orders 全事件流**:按 `order_id` 取全部 seq 展示状态机轨迹,表直读即可,无需新视图(确认即可,非改动)。

## 4. 【确认遵守】视图够用性 + blocked 纪律

- `v_orders_current / v_fills_effective / v_order_filled / v_positions_eod / v_pdt_latest / ingest_watermark` 满足监控+留档两页需求,无另起视图诉求。
- `tweet_blocked=TRUE` 的原帖文本面板**不对外展示**(§9 Dash 会签项):草稿已落实,展示层占位替换。

— 以上。1 项修订 + 2 表定稿后,Dash 侧对 r3 即可无保留 PASS。
