# ORDER_LEDGER_SPEC — 博主跟单下单留档 Ledger(r3,准予实施)

> **状态:r3 定稿——%Exec/%Dash 双会签完成(`docs/ORDER_LEDGER_SPEC_DASH_SIGNOFF.md`、track/exec `docs/reviews/`),全部会签修订已吸收(Exec 4+3 / Dash 3 / 金额配置化)。DDL 实施落 `src/execution/ledger/`(P1)。**
> r3 变更摘要:§2 读写分离(Dash 永不直连,writer 每循环导出 parquet+export_meta)· orders +`broker_order_ref`/+`expired` 终态/+更正行语义 · fills 幂等纪律 · 新增 `account_daily`/`agent_runs` 两表 · 单仓金额配置化。
> 事实源:`docs/INTEGRATION_NOTES.md`(stock-picker 真实字段,2026-06-10 对方 Lead 核实)。
> 本 ledger 是博主跟单系统的**核心审计件**:每一笔模拟盘订单都必须能回答——
> **"何时、跟谁的哪条帖、依据什么规则、实际成交多少、现在状态。"**

## 0. 设计目标与留档纪律

| 审计五问 | 落点 |
|---|---|
| 何时 | `signals.call_ts`(博主发帖)/ `signals.ingested_ts`(我们收录)/ `orders.submitted_ts`(下单)+ `orders.call_to_submit_ms` 延迟 |
| 跟谁的哪条帖 | `signals.handle` + `signals.tweet_id` + `signals.tweet_url` + **`signals.tweet_text` 原帖全文快照(我们自己的副本,不依赖 stock-picker 库长期不变)** |
| 依据什么规则 | `signals.tier`(收录当日诚实榜 status 快照)+ `signals.decision`/`decision_reason` + `orders.rule_version` |
| 实际成交多少 | `fills`(逐笔,含 Firstrade 页面回采原始文本)→ `v_order_filled` 聚合 |
| 现在状态 | `orders` 事件流最新行(`v_orders_current`)+ `exit_reason` + `positions_daily` 对账锚点 |

**三条硬纪律:**
1. **append-only**:任何状态变化是**新行**,不是 UPDATE。本库不存在 UPDATE/DELETE 语义;修正=追加修正行(见 §5.3)。审计件不可改写。
2. **PAPER_ONLY**:本 ledger 只记 Firstrade **模拟盘**订单(红线 2)。kill-switch 状态随单快照。
3. **stock-picker 侧只读**:`trackrecord.db`/`tweets.db`/诚实榜 CSV 只读,绝不写(INTEGRATION_NOTES 铁律)。留档所需原帖内容**复制进本库**。

## 1. 信号链与各环节留档点

```text
stock-picker(只读)                          quant-stock(本库,append-only)
┌──────────────────────────────┐
│ trackrecord.db/analyst_calls │── call_ts > last_seen 水位轮询 ──→ [ingest_watermark] 每轮水位
│   is_call=1 & direction=     │
│   bullish & handle 在最新     │
│   诚实榜 CSV horizon=21d &    │
│   status=PROVEN(strip \r!)  │
│ tweets.db/tweets             │── tweet_id JOIN 取 text/url ────→ [signals] 喊单收录 + 原帖快照
└──────────────────────────────┘              │
                                              ▼
                                  跟单规则引擎(rule_version)
                                  decision: followed / skipped(原因码)──→ [signals.decision]
                                              │ followed
                                              ▼
                                  B4 闸门:PDT 计数 + settled-cash ──→ [pdt_ledger] 读最新快照
                                              │ 放行
                                              ▼
                                  Firstrade 模拟盘下单(PAPER_ONLY,
                                  Playwright 人类节奏,kill-switch 可停)──→ [orders] 事件流
                                              │
                                              ▼
                                  成交回采(页面原始文本快照)────────→ [fills]
                                              │
                                              ▼
                                  每日收盘持仓快照(对账锚点)────────→ [positions_daily]
                                              ▼
                                  对账:fills 累计 vs positions_daily(§7)
```

字段名以 INTEGRATION_NOTES 为准(均为对方 Lead 核实的真实 schema):
- `analyst_calls`:`call_ts`(ISO8601 UTC)、`tweet_id`(snowflake,时序单调)、`ticker`、`direction`(`bullish`/`bearish`/`neutral`)、`is_call`、`confidence`(0-1)、`conviction`(low/medium/high)、`handle`/`author_id`。**没有目标价/止损字段,不假设有。**
- `tweets`:`id`(=tweet_id)、`text`(原帖全文)、`url`、`created_at`、`blocked`(合规屏蔽标记,对外展示禁用)。
- 诚实榜 CSV `leaderboard_honest_<date>.csv`:`status` 列判 tier;**CRLF 文件,匹配前必须 `strip()`**;每天 13:30 PT 刷新,取最新日期文件。
- v1 入口只放行 **is_call=1 & 21d PROVEN & bullish**(最高质量子集);另放行"持仓中同 handle×ticker 的反向喊单"作退出触发(§6),收录为 `decision='skipped'`、`decision_reason='exit_trigger'`。

## 2. 存储与路径

- **引擎:DuckDB**,复用 `src/storage` 栈(`DuckDBClient` 封装风格)。本 ledger 与研究库 `research.duckdb` **分库**——审计件与研究数据不混写。
- **路径走 settings**(实施批次在 `config/settings.py` 增加,本 spec 不动代码):

```python
# 提案:config/settings.py 新增 property(实施批次落地)
@property
def execution_ledger_path(self) -> Path:
    return self.data_dir / "execution" / "ledger.duckdb"
```

- `data/`、`*.duckdb`、`*.duckdb.wal` 已在 `.gitignore`(红线 5),ledger 永不入 git。
- **单写者 + 读写分离(r3,Dash 会签实测修订)**:唯一连接方是 %Exec 的 ledger writer。**%Dash 永不直连 ledger.duckdb**——实测 DuckDB 1.5.3 跨进程锁:writer 持普通连接时另一进程 `read_only=True` connect 即抛 `IOException: Could not set lock`(复现脚本见 Dash 会签 §1)。读取面 = parquet 导出:
  - writer **每个执行循环收尾**把全表导出 parquet(paper 量级,全量导出,不做增量)+ 最后写 `export_meta`(导出时间戳 + 各表行数);
  - **原子性(Exec 实施要点)**:每个 parquet 先写临时文件再原子 rename;`export_meta` 在全部表成功后**最后**原子落——Dash 以 meta 新鲜度判快照集完整一致,meta 旧 = exec 离线/HALT,降级显示"数据陈旧"而非报错;
  - **导出与 kill-switch**:导出同记账一样不被 kill 阻断(kill 只停下单动作),HALT 后末轮快照仍落;导出失败不阻断落账(记账优先,loguru 告警)。
- **append-only 的工程落法**:writer 封装只暴露 `insert_*` API,不提供 UPDATE/DELETE;parquet 导出行数只增不减,审计可对照。

## 3. 表设计总览

| 表 | 性质 | 主键 | 一行的含义 |
|---|---|---|---|
| `signals` | 不可变,一信号一行 | `signal_id` | 一条收录的喊单 + 原帖快照 + 跟/不跟决定 |
| `orders` | **事件溯源**,一状态变化一行 | `(order_id, seq)` | 一个订单的一次状态事件;最新行=现状 |
| `fills` | 不可变追加 | `fill_id` | 一笔成交回采(含页面原始文本) |
| `positions_daily` | 快照追加 | `(snapshot_date, ticker, snapshot_ts)` | 某日收盘某票持仓快照(对账锚点) |
| `pdt_ledger` | 不可变追加 | `entry_id` | 一次 day-trade/资金事件或日终簿记快照(B4) |
| `account_daily` | 快照追加(r3,Dash) | `(snapshot_date, snapshot_ts)` | 每日账户总权益/现金快照(权益曲线落点) |
| `agent_runs` | 追加(r3,Dash) | `run_id` | 每轮执行循环心跳:起止/kill 状态/动作计数(无订单流动时的可观测性,红线 6) |
| `ingest_watermark` | 追加 | —(按 poll_ts 取 max) | 一轮 `call_ts` 水位轮询记录 |
| `ledger_meta` | 追加 | — | schema 版本演进记录 |

关系(逻辑外键以 `→` 标注):
- `orders.signal_id` → `signals.signal_id`(**物理 FK**,signals 主键唯一可引用)。
- `orders.exit_trigger_signal_id` → `signals.signal_id`(物理 FK,可空;仅 direction_flip 平仓单填)。
- `fills.order_id` → `orders.order_id`(**逻辑 FK**:orders 主键是复合 `(order_id, seq)`,DuckDB 无法对非唯一列建物理 FK;由 writer 写入前校验)。
- `pdt_ledger.order_id` → `orders.order_id`(逻辑 FK,同上)。

ID 约定:`signal_id = 'sig_' || tweet_id || '_' || ticker`(幂等防重;**必须含 ticker**——上游 `analyst_calls` 复合主键是 `(tweet_id, ticker)`,同一条帖子喊多只票是真实场景,只用 tweet_id 会撞主键;r2 修正 by %Valid,见其 RECON_DESIGN_V0 §8);`order_id`/`fill_id`/`entry_id` 为应用侧生成的 ULID(前缀 `ord_`/`fil_`/`pdt_`)。

## 4. DDL(完整,DuckDB 可直接执行)

> 以下 SQL 按序执行即可建库(已在 DuckDB 1.5.2 内存库实跑验证)。实施时落 `src/execution/ledger/schema.sql` + 建库脚本,**不在本批次写**。

### 4.1 signals — 喊单收录(一信号一行,不可变)

```sql
CREATE TABLE IF NOT EXISTS signals (
    signal_id        TEXT PRIMARY KEY,      -- 'sig_' || tweet_id || '_' || ticker(同帖多 ticker 不撞键,r2)
    tweet_id         TEXT NOT NULL,         -- analyst_calls.tweet_id(X snowflake;同帖多 ticker 可重复,r2 撤销 UNIQUE)
    handle           TEXT NOT NULL,         -- analyst_calls.handle
    author_id        TEXT,                  -- analyst_calls.author_id(可空)
    tier             TEXT NOT NULL CHECK (tier IN (
                         'PROVEN', 'TRACKING', 'FADE', 'INSUFFICIENT',
                         'PROVEN_1REGIME', 'PROVEN_BAD_1REGIME'
                     )),                    -- 收录当日诚实榜 status 快照(21d 口径,strip 后)
    tier_csv_date    DATE NOT NULL,         -- 用的哪个日期的诚实榜 CSV(溯源)
    ticker           TEXT NOT NULL,
    direction        TEXT NOT NULL CHECK (direction IN ('bullish', 'bearish', 'neutral')),
    call_ts          TIMESTAMPTZ NOT NULL,  -- analyst_calls.call_ts(博主发帖时间,UTC)
    ingested_ts      TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 我们收录的时间
    tweet_text       TEXT NOT NULL,         -- 原帖全文快照(tweets.text 的本地副本)
    tweet_url        TEXT NOT NULL,         -- tweets.url
    tweet_created_at TIMESTAMPTZ,           -- tweets.created_at 快照
    tweet_blocked    BOOLEAN NOT NULL DEFAULT FALSE,  -- tweets.blocked 快照;TRUE 则 Dash 对外展示禁用
    conviction       TEXT CHECK (conviction IS NULL OR conviction IN ('low', 'medium', 'high')),
    confidence       DOUBLE CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    decision         TEXT NOT NULL CHECK (decision IN ('followed', 'skipped')),
    decision_reason  TEXT NOT NULL,         -- 原因码,字典见 §5.1
    rule_version     TEXT NOT NULL          -- 做此决定时的规则引擎版本
);
```

注:tier/direction 的 CHECK 不写死 `PROVEN`/`bullish`——v1 入口过滤在引擎层(可复现:水位 + 过滤规则 + tier_csv_date 共同决定),表结构对未来扩口(如 FADE 反指、退出触发收录)向前兼容,以 `rule_version` 区分行为版本。

### 4.2 orders — 订单事件流(一状态变化一行)

```sql
CREATE TABLE IF NOT EXISTS orders (
    order_id          TEXT NOT NULL,        -- 'ord_' + ULID,应用侧生成
    seq               INTEGER NOT NULL CHECK (seq >= 0),  -- 事件序号,0 起单调 +1
    event_ts          TIMESTAMPTZ NOT NULL DEFAULT now(), -- 本事件落账时间
    signal_id         TEXT NOT NULL REFERENCES signals (signal_id),
    ticker            TEXT NOT NULL,
    side              TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    qty               DECIMAL(18, 4) NOT NULL CHECK (qty > 0),  -- 委托数量
    order_type        TEXT NOT NULL CHECK (order_type IN ('market', 'limit')),
    limit_price       DECIMAL(18, 4) CHECK (
                          (order_type = 'market' AND limit_price IS NULL)
                          OR (order_type = 'limit' AND limit_price > 0)
                      ),
    submitted_ts      TIMESTAMPTZ NOT NULL, -- 在 Firstrade 页面提交的时间(seq=0 落定,后续行复制)
    call_to_submit_ms BIGINT,               -- submitted_ts - signals.call_ts,毫秒(跟单延迟)
    broker_order_ref  TEXT,                 -- r3(Exec①):Firstrade 页面订单号(可空)——同票多单并存时
                                            -- fills↔orders 匹配的唯一可靠锚,缺它只能时间窗+qty 猜配
    status            TEXT NOT NULL CHECK (status IN (
                          'submitted', 'partial', 'filled', 'cancelled', 'rejected', 'expired'
                      )),                    -- r3(Exec③):expired=日内限价单收盘未成交,真实终态
    corrects_seq      INTEGER,              -- r3(Exec④):NULL=正常事件;非 NULL=更正行,引用本订单
                                            -- 被更正的 seq(终态封锁豁免,见 §5.2/5.3;note 必填缘由)
    rule_version      TEXT NOT NULL,        -- 下单时规则引擎版本
    kill_switch_engaged BOOLEAN NOT NULL DEFAULT FALSE,  -- 本事件发生时 kill-switch 状态快照
    exit_reason       TEXT CHECK (exit_reason IS NULL OR exit_reason IN (
                          'hold_21d', 'stop_loss', 'direction_flip', 'manual', 'kill_switch'
                      )),                   -- 仅平仓单填,字典见 §6
    exit_trigger_signal_id TEXT REFERENCES signals (signal_id),
                                            -- direction_flip 平仓时,指向触发的反向喊单
    note              TEXT,
    PRIMARY KEY (order_id, seq)
);
```

**状态机**(每次迁移=追加新行,`seq+1`,不可变字段原样复制以保证单行自含可读):

```text
submitted ──→ partial ──→ filled      (终态)
    │            │
    │            ├──────→ cancelled   (终态:余量撤单)
    │            └──────→ expired     (终态:余量收盘失效,r3)
    ├──────────────────→ filled       (终态:一次全成)
    ├──────────────────→ cancelled    (终态:成交前撤,含 kill-switch 触发)
    ├──────────────────→ rejected     (终态:Firstrade 拒单)
    └──────────────────→ expired      (终态:日内限价单收盘未成交,r3)
```

合法迁移仅上图所列;writer 在追加前校验"当前最新 status → 新 status"在白名单内,非法迁移拒写并告警(不静默落账)。**唯一豁免:更正行(`corrects_seq` 非空)可在终态后追加**(误记终态的出口,§5.3),writer 校验白名单时将 correction 迁移单列处理。
单仓金额(等额 $X/单)**不写死在代码**:来自 rule_version 配置(r3;数额待 C5/P2 首登核验账户实际资金后定,与 Strat spec 同源)。

### 4.3 fills — 成交回采(不可变追加)

```sql
CREATE TABLE IF NOT EXISTS fills (
    fill_id       TEXT PRIMARY KEY,         -- 'fil_' + ULID
    order_id      TEXT NOT NULL,            -- 逻辑外键 → orders.order_id
    fill_ts       TIMESTAMPTZ NOT NULL,     -- Firstrade 显示的成交时间
    qty           DECIMAL(18, 4) NOT NULL CHECK (qty > 0),
    price         DECIMAL(18, 4) NOT NULL CHECK (price > 0),
    raw_text      TEXT NOT NULL,            -- Firstrade 页面回采的原始文本快照(审计原件)
    scraped_ts    TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 回采时间
    voids_fill_id TEXT,                     -- 修正机制:指向被作废的 fill(见 §5.3),正常为 NULL
    note          TEXT
);
```

**幂等纪律(r3,Exec②——回采是轮询型,必须防同笔成交重复插行)**:writer 插入前按自然键 `(order_id, fill_ts, qty, price)` 对 `v_fills_effective` 查重,已存在则跳过(debug 日志);否则同笔成交反复插行,对账永久不平。

### 4.4 positions_daily — 每日收盘持仓快照(对账锚点)

```sql
CREATE TABLE IF NOT EXISTS positions_daily (
    snapshot_date  DATE NOT NULL,           -- 美东交易日
    snapshot_ts    TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 抓取时刻;同日重抓=新行
    ticker         TEXT NOT NULL,
    qty            DECIMAL(18, 4) NOT NULL, -- Firstrade 持仓页显示数量
    avg_cost       DECIMAL(18, 6),          -- Firstrade 显示均价成本
    close          DECIMAL(18, 4),          -- 当日收盘价
    unrealized_pnl DECIMAL(18, 2),          -- 未实现盈亏(页面值)
    raw_text       TEXT,                    -- 持仓页原始文本快照(可选但建议)
    PRIMARY KEY (snapshot_date, ticker, snapshot_ts)
);
```

### 4.5 pdt_ledger — PDT 计数与 settled-cash 簿记(B4 约束落点)

```sql
CREATE TABLE IF NOT EXISTS pdt_ledger (
    entry_id      TEXT PRIMARY KEY,         -- 'pdt_' + ULID
    event_ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
    trade_date    DATE NOT NULL,            -- 美东交易日
    event_type    TEXT NOT NULL CHECK (event_type IN (
                      'day_trade',          -- 判定一次 day trade(同票同日先买后卖,挂平仓腿)
                      'cash_debit',         -- 买入占用资金(cash_delta < 0)
                      'cash_credit',        -- 卖出回笼资金(cash_delta > 0,settle_date 才可用)
                      'cash_settled',       -- 资金到结算日转入 settled
                      'eod_snapshot'        -- 日终簿记快照(对账/取数锚点)
                  )),
    ticker        TEXT,                     -- day_trade / 现金事件关联票(快照行可空)
    order_id      TEXT,                     -- 逻辑外键 → orders.order_id(快照行可空)
    cash_delta    DECIMAL(18, 2),           -- 现金变动,买负卖正(day_trade/快照行可空)
    settle_date   DATE,                     -- 该笔资金的结算日(T+1)
    day_trades_5d INTEGER NOT NULL,         -- 写入时滚动 5 交易日 day-trade 计数快照
    settled_cash  DECIMAL(18, 2) NOT NULL,  -- 写入时已结算可用资金快照
    note          TEXT
);
```

簿记规则(B4):
- **day-trade 判定**:同一美东交易日内、同一 ticker 先开仓后平仓 = 1 次 day trade,事件挂在平仓腿 `order_id` 上。
- **滚动窗口**:"滚动 5 交易日"按 NYSE 交易日历在**应用侧**计算(SQL 内无日历),写入时把结果快照进 `day_trades_5d`;审计可用原始 `day_trade` 事件行重算核对快照(两套账互验)。
- **settled-cash**:卖出回笼资金 `settle_date = trade_date + 1 交易日`(T+1,现行美股结算),到期落 `cash_settled` 事件;`settled_cash` 快照随每个事件更新。
- **闸门(引擎层,阈值随 rule_version 配置,ledger 只记账不裁决)**:下单前读最新快照,要求 `day_trades_5d < 3` 且 `settled_cash ≥ 预估订单金额`,否则 skip(原因码 `pdt_limit_reached` / `insufficient_settled_cash`)。模拟盘无真金,但**按真实约束演练**,否则模拟结果对真实迁移无参考价值。

### 4.5b account_daily 与 agent_runs(r3 新增,Dash 会签:监控刚需)

```sql
-- 账户总权益曲线落点(此前无处可放)
CREATE TABLE IF NOT EXISTS account_daily (
    snapshot_date  DATE NOT NULL,            -- 美东交易日
    snapshot_ts    TIMESTAMPTZ NOT NULL DEFAULT now(),
    total_equity   DECIMAL(18, 2) NOT NULL,  -- Firstrade 账户页总权益
    cash           DECIMAL(18, 2),           -- 现金(页面值)
    buying_power   DECIMAL(18, 2),           -- 购买力(页面值,可空)
    raw_text       TEXT,                     -- 账户页原始文本快照
    PRIMARY KEY (snapshot_date, snapshot_ts)
);

-- 每轮执行循环心跳(无订单流动时 kill-switch/agent 死活的唯一可观测点,红线 6)
-- ⚠ r3.1 显式例外(Lead 裁决,Exec 实施申报):本表是【运维心跳表】非资金审计表,
-- 允许全库唯一的窄口径 UPDATE——仅限对"未收尾行"(finished_ts IS NULL)定向回填
-- finished_ts/计数/export_ok/error。资金链路(signals/orders/fills/positions_daily/
-- pdt_ledger/account_daily)严格零 UPDATE 不受影响。崩溃语义保持:回填永远到不了 = NULL 即证据。
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id          TEXT PRIMARY KEY,        -- 'run_' + ULID
    started_ts      TIMESTAMPTZ NOT NULL,
    finished_ts     TIMESTAMPTZ,             -- 循环结束落定(崩溃则为 NULL,本身即证据)
    kill_switch     BOOLEAN NOT NULL,        -- 本轮开始时 kill-switch 状态
    signals_seen    INTEGER NOT NULL DEFAULT 0,
    orders_placed   INTEGER NOT NULL DEFAULT 0,
    fills_scraped   INTEGER NOT NULL DEFAULT 0,
    export_ok       BOOLEAN,                 -- 本轮 parquet 导出是否成功(失败不阻断记账,§2)
    error           TEXT,                    -- 本轮异常摘要(无则 NULL)
    note            TEXT
);
```

### 4.6 辅助表 — 水位与 schema 版本

```sql
CREATE TABLE IF NOT EXISTS ingest_watermark (
    poll_ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_call_ts TIMESTAMPTZ NOT NULL,  -- 本轮处理完后的 call_ts 水位
    calls_seen        INTEGER NOT NULL DEFAULT 0,  -- 本轮新喊单数(过滤前)
    note              TEXT
);

CREATE TABLE IF NOT EXISTS ledger_meta (
    schema_version INTEGER NOT NULL,
    applied_ts     TIMESTAMPTZ NOT NULL DEFAULT now(),
    note           TEXT
);
```

当前水位 = `SELECT max(last_seen_call_ts) FROM ingest_watermark`;每轮轮询追加一行(轮询本身也留档,可审"为什么那天没收到信号")。

### 4.7 视图 — 现状读取与对账

```sql
-- 每个订单的当前状态 = 事件流最新一行
CREATE OR REPLACE VIEW v_orders_current AS
SELECT *
FROM orders
QUALIFY row_number() OVER (PARTITION BY order_id ORDER BY seq DESC) = 1;

-- 有效成交 = 剔除作废行与被作废行(§5.3 修正机制)
CREATE OR REPLACE VIEW v_fills_effective AS
SELECT *
FROM fills f
WHERE f.voids_fill_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM fills v WHERE v.voids_fill_id = f.fill_id);

-- 每个订单的成交聚合
CREATE OR REPLACE VIEW v_order_filled AS
SELECT order_id,
       sum(qty)                               AS filled_qty,
       sum(qty * price) / nullif(sum(qty), 0) AS avg_fill_price,
       min(fill_ts)                           AS first_fill_ts,
       max(fill_ts)                           AS last_fill_ts,
       count(*)                               AS n_fills
FROM v_fills_effective
GROUP BY order_id;

-- 每日每票最终快照(同日重抓取最新)
CREATE OR REPLACE VIEW v_positions_eod AS
SELECT *
FROM positions_daily
QUALIFY row_number() OVER (PARTITION BY snapshot_date, ticker ORDER BY snapshot_ts DESC) = 1;

-- ledger 侧推算净持仓(对账用,§7)
CREATE OR REPLACE VIEW v_recon_ledger_qty AS
SELECT o.ticker,
       sum(CASE WHEN o.side = 'buy' THEN f.qty ELSE -f.qty END) AS ledger_qty
FROM v_fills_effective f
JOIN v_orders_current o USING (order_id)
GROUP BY o.ticker;

-- B4 最新簿记快照(下单闸门读这里)
CREATE OR REPLACE VIEW v_pdt_latest AS
SELECT *
FROM pdt_ledger
QUALIFY row_number() OVER (ORDER BY event_ts DESC, entry_id DESC) = 1;
```

## 5. 字典与修正机制

### 5.1 `signals.decision_reason` 原因码

| decision | 原因码 | 含义 |
|---|---|---|
| followed | `all_gates_passed` | 入口过滤 + 引擎风控全过,转下单 |
| skipped | `pdt_limit_reached` | B4:滚动 5 日 day-trade 数达上限 |
| skipped | `insufficient_settled_cash` | B4:已结算资金不足 |
| skipped | `position_already_open` | 同票已有跟单持仓,不加仓(v1 规则) |
| skipped | `kill_switch_on` | kill-switch 处于触发态,只收录不下单 |
| skipped | `signal_stale` | 收录时距 call_ts 超过新鲜度阈值(信号源延迟约 1-2h,阈值随 rule_version) |
| skipped | `ticker_not_tradable` | Firstrade 不可交易/停牌/非美股主板 |
| skipped | `risk_cap_exceeded` | 组合层风控(单票/总敞口上限) |
| skipped | `exit_trigger` | 反向喊单,仅作退出触发收录(§6),本身不开新仓 |
| skipped | `manual_block` | operator 手工拉黑(handle/ticker) |
| skipped | `duplicate_signal` | r3.2(规则引擎 v0.1):同 handle×ticker×direction 窗口内重复,取最新弃旧 |
| skipped | `direction_conflict` | r3.2:窗口内 PROVEN 多空对立,整票跳过 |
| skipped | `merge_lost` | r3.2:同日同票多 handle 择优合并中落选(wilson_lo→conviction→confidence) |
| skipped | `handle_cap_exceeded` | r3.2:单 handle 并发仓位达上限(默认 5,随 rule_version) |

原因码是**封闭集**,新增必须升 `rule_version` 并更新本表(spec 与代码同步改)。

### 5.2 `orders.status` 迁移白名单(r3)

`submitted→partial`、`submitted→filled`、`submitted→cancelled`、`submitted→rejected`、`submitted→expired`、`partial→filled`、`partial→cancelled`、`partial→expired`。终态(`filled`/`cancelled`/`rejected`/`expired`)后不得追加**状态推进行**;**唯一豁免 = 更正行**(`corrects_seq` 非空,见 §5.3)。

### 5.3 修正机制(append-only 下怎么改错)

- **orders(r3,Exec④)**:非终态更正=追加新事件行(`seq+1`)+ `note` 缘由;**误记终态后的更正**=追加 `corrects_seq=<被更正行 seq>` 的更正行(豁免终态封锁;status 写更正后的正确值,`note` 必填缘由),`v_orders_current` 取最新行即自动以更正为准;原错误行永久留存可审。不改旧行。
- **fills**:回采错误(OCR/解析错)时,追加一行 `voids_fill_id=<错误行 fill_id>` 的作废行(其余字段复制原行,`note` 写明),再追加正确的新 fill。`v_fills_effective` 自动剔除作废对;原始错误行永久留存可审。
- **positions_daily**:重抓=同日新 `snapshot_ts` 行,`v_positions_eod` 取最新;旧快照留存。
- **signals**:不可变。tier 判定错误属上游 CSV/口径问题,由 `tier_csv_date` 溯源;后续动作(撤单/平仓)在 orders 层留痕。

## 6. 退出逻辑(`orders.exit_reason`)

stock-picker **没有退出信号建模**(INTEGRATION_NOTES §4),退出逻辑 quant 侧自建。平仓单是一笔正常的 `orders` 记录(`side='sell'`,v1 只做多),`signal_id` 仍指向**原入场信号**(保住"这笔平仓属于哪次跟单"的链路),`exit_reason` 必填:

| exit_reason | 触发 | 说明 |
|---|---|---|
| `hold_21d` | 持有满 21 个交易日 | 对标 stock-picker `call_outcomes` 的 21d 评估口径,默认退出 |
| `stop_loss` | 自建止损线触发 | 阈值随 rule_version;源头无止损字段,不从原帖抽 |
| `direction_flip` | 同 handle 对同 ticker 出现反向新喊单 | 该反向喊单收录为 `exit_trigger` 信号,平仓单 `exit_trigger_signal_id` 指向它(平仓依据也留原帖快照) |
| `manual` | operator 手工平仓 | `note` 必填缘由 |
| `kill_switch` | kill-switch 触发的强平/撤单 | `kill_switch_engaged=TRUE` 同时成立 |

## 7. 对账(positions_daily 为锚)

每个交易日收盘后:
1. 抓 Firstrade 持仓页 → 追加 `positions_daily` 快照(含 `raw_text` 原件)。
2. 比对 `v_recon_ledger_qty.ledger_qty`(fills 累计推算)vs `v_positions_eod.qty`(券商页面)。
3. **一致** → 在 `pdt_ledger` 落 `eod_snapshot`(note 记 `recon=ok`)。
4. **不一致** → 告警停新单(出错先停,红线 6);排查后按 §5.3 追加修正行,**绝不**用 UPDATE 抹平差异。差异本身就是审计证据(漏采成交?页面解析错?agent 重复下单?)。

## 8. 审计五问示例查询

```sql
-- 给定一笔订单,一条 SQL 回答全部五问(空库可跑,实施后即用)
SELECT
    s.call_ts,                              -- 何时(博主发帖)
    o.submitted_ts, o.call_to_submit_ms,    -- 何时(我们下单 + 延迟)
    s.handle, s.tweet_url, s.tweet_text,    -- 跟谁的哪条帖(本地快照)
    s.tier, s.tier_csv_date,                -- 依据什么规则(当日诚实榜快照)
    s.decision, s.decision_reason, o.rule_version,
    f.filled_qty, f.avg_fill_price,         -- 实际成交多少
    o.status, o.exit_reason,                -- 现在状态
    o.kill_switch_engaged
FROM v_orders_current o
JOIN signals s USING (signal_id)
LEFT JOIN v_order_filled f USING (order_id)
WHERE o.order_id = 'ord_01JEXAMPLE';
```

## 9. 实施与会签清单

- [x] ~~%Audit 审~~(审核制度 2026-06-10 废止)
- [x] **%Exec 会签**(track/exec `docs/reviews/ORDER_LEDGER_SPEC_exec_signoff.md`):4 条修订 + 3 实施要点全采纳进 r3;2 条诚实声明在案(金额/选择器待 P2 首登核验)。
- [x] **%Dash 会签**(`docs/ORDER_LEDGER_SPEC_DASH_SIGNOFF.md`):锁实测 → §2 读写分离 + 两新表 + recon 结构化(结构化字段落 Valid 的 recon_runs/findings 实施时对齐)。
- [ ] **P1 实施批次(Exec)**:DDL 落 `src/execution/ledger/schema.sql` + writer 封装(insert-only API + 状态机白名单含 correction 分支 + fills 幂等查重 + 每循环 parquet 原子导出)+ `config/settings.py` 加 `execution_ledger_path` + pytest(状态机/作废对/幂等/水位/导出原子性)。
