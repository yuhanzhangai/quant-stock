-- ORDER_LEDGER schema r3(docs/ORDER_LEDGER_SPEC.md @ ca54cc9,§4 DDL 原文落地)
-- 纪律:append-only;writer 只 INSERT(唯一例外见 writer.py agent_runs 注释);Dash 永不直连本库。

-- §4.1 signals — 喊单收录(一信号一行,不可变)
CREATE TABLE IF NOT EXISTS signals (
    signal_id        TEXT PRIMARY KEY,      -- 'sig_' || tweet_id || '_' || ticker(同帖多 ticker 不撞键,r2)
    tweet_id         TEXT NOT NULL,         -- analyst_calls.tweet_id(X snowflake;同帖多 ticker 可重复)
    handle           TEXT NOT NULL,
    author_id        TEXT,
    tier             TEXT NOT NULL CHECK (tier IN (
                         'PROVEN', 'TRACKING', 'FADE', 'INSUFFICIENT',
                         'PROVEN_1REGIME', 'PROVEN_BAD_1REGIME'
                     )),                    -- 收录当日诚实榜 status 快照(21d 口径,strip 后)
    tier_csv_date    DATE NOT NULL,
    ticker           TEXT NOT NULL,
    direction        TEXT NOT NULL CHECK (direction IN ('bullish', 'bearish', 'neutral')),
    call_ts          TIMESTAMPTZ NOT NULL,
    ingested_ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
    tweet_text       TEXT NOT NULL,         -- 原帖全文快照(tweets.text 的本地副本)
    tweet_url        TEXT NOT NULL,
    tweet_created_at TIMESTAMPTZ,
    tweet_blocked    BOOLEAN NOT NULL DEFAULT FALSE,
    conviction       TEXT CHECK (conviction IS NULL OR conviction IN ('low', 'medium', 'high')),
    confidence       DOUBLE CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    decision         TEXT NOT NULL CHECK (decision IN ('followed', 'skipped')),
    decision_reason  TEXT NOT NULL,
    rule_version     TEXT NOT NULL
);

-- §4.2 orders — 订单事件流(一状态变化一行)
CREATE TABLE IF NOT EXISTS orders (
    order_id          TEXT NOT NULL,        -- 'ord_' + ULID,应用侧生成
    seq               INTEGER NOT NULL CHECK (seq >= 0),
    event_ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    signal_id         TEXT NOT NULL REFERENCES signals (signal_id),
    ticker            TEXT NOT NULL,
    side              TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    qty               DECIMAL(18, 4) NOT NULL CHECK (qty > 0),
    order_type        TEXT NOT NULL CHECK (order_type IN ('market', 'limit')),
    limit_price       DECIMAL(18, 4) CHECK (
                          (order_type = 'market' AND limit_price IS NULL)
                          OR (order_type = 'limit' AND limit_price > 0)
                      ),
    submitted_ts      TIMESTAMPTZ NOT NULL,
    call_to_submit_ms BIGINT,
    broker_order_ref  TEXT,                 -- r3(Exec①):Firstrade 页面订单号(可空)
    status            TEXT NOT NULL CHECK (status IN (
                          'submitted', 'partial', 'filled', 'cancelled', 'rejected', 'expired'
                      )),                   -- r3(Exec③):expired=日内限价单收盘未成交
    corrects_seq      INTEGER,              -- r3(Exec④):非 NULL=更正行(终态封锁豁免,note 必填)
    rule_version      TEXT NOT NULL,
    kill_switch_engaged BOOLEAN NOT NULL DEFAULT FALSE,
    exit_reason       TEXT CHECK (exit_reason IS NULL OR exit_reason IN (
                          'hold_21d', 'stop_loss', 'direction_flip', 'manual', 'kill_switch'
                      )),
    exit_trigger_signal_id TEXT REFERENCES signals (signal_id),
    note              TEXT,
    PRIMARY KEY (order_id, seq)
);

-- §4.3 fills — 成交回采(不可变追加)
CREATE TABLE IF NOT EXISTS fills (
    fill_id       TEXT PRIMARY KEY,         -- 'fil_' + ULID
    order_id      TEXT NOT NULL,            -- 逻辑外键 → orders.order_id(writer 写入前校验)
    fill_ts       TIMESTAMPTZ NOT NULL,
    qty           DECIMAL(18, 4) NOT NULL CHECK (qty > 0),
    price         DECIMAL(18, 4) NOT NULL CHECK (price > 0),
    raw_text      TEXT NOT NULL,            -- Firstrade 页面回采的原始文本快照(审计原件)
    scraped_ts    TIMESTAMPTZ NOT NULL DEFAULT now(),
    voids_fill_id TEXT,                     -- 修正机制:指向被作废的 fill(§5.3)
    note          TEXT
);

-- §4.4 positions_daily — 每日收盘持仓快照(对账锚点)
CREATE TABLE IF NOT EXISTS positions_daily (
    snapshot_date  DATE NOT NULL,
    snapshot_ts    TIMESTAMPTZ NOT NULL DEFAULT now(),
    ticker         TEXT NOT NULL,
    qty            DECIMAL(18, 4) NOT NULL,
    avg_cost       DECIMAL(18, 6),
    close          DECIMAL(18, 4),
    unrealized_pnl DECIMAL(18, 2),
    raw_text       TEXT,                    -- 列可空(spec),但 writer API 必填(Exec 会签承诺)
    PRIMARY KEY (snapshot_date, ticker, snapshot_ts)
);

-- §4.5 pdt_ledger — PDT 计数与 settled-cash 簿记(B4 约束落点)
CREATE TABLE IF NOT EXISTS pdt_ledger (
    entry_id      TEXT PRIMARY KEY,         -- 'pdt_' + ULID
    event_ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
    trade_date    DATE NOT NULL,
    event_type    TEXT NOT NULL CHECK (event_type IN (
                      'day_trade', 'cash_debit', 'cash_credit', 'cash_settled', 'eod_snapshot'
                  )),
    ticker        TEXT,
    order_id      TEXT,                     -- 逻辑外键 → orders.order_id
    cash_delta    DECIMAL(18, 2),
    settle_date   DATE,
    day_trades_5d INTEGER NOT NULL,
    settled_cash  DECIMAL(18, 2) NOT NULL,
    note          TEXT
);

-- §4.5b account_daily — 账户总权益曲线落点(r3,Dash 会签)
CREATE TABLE IF NOT EXISTS account_daily (
    snapshot_date  DATE NOT NULL,
    snapshot_ts    TIMESTAMPTZ NOT NULL DEFAULT now(),
    total_equity   DECIMAL(18, 2) NOT NULL,
    cash           DECIMAL(18, 2),
    buying_power   DECIMAL(18, 2),
    raw_text       TEXT,
    PRIMARY KEY (snapshot_date, snapshot_ts)
);

-- §4.5b agent_runs — 每轮执行循环心跳(r3,Dash 会签;红线 6 可观测性)
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id          TEXT PRIMARY KEY,        -- 'run_' + ULID
    started_ts      TIMESTAMPTZ NOT NULL,
    finished_ts     TIMESTAMPTZ,             -- 循环结束落定(崩溃则为 NULL,本身即证据)
    kill_switch     BOOLEAN NOT NULL,
    signals_seen    INTEGER NOT NULL DEFAULT 0,
    orders_placed   INTEGER NOT NULL DEFAULT 0,
    fills_scraped   INTEGER NOT NULL DEFAULT 0,
    export_ok       BOOLEAN,
    error           TEXT,
    note            TEXT
);

-- §4.6 辅助表 — 水位与 schema 版本
CREATE TABLE IF NOT EXISTS ingest_watermark (
    poll_ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_call_ts TIMESTAMPTZ NOT NULL,
    calls_seen        INTEGER NOT NULL DEFAULT 0,
    note              TEXT
);

CREATE TABLE IF NOT EXISTS ledger_meta (
    schema_version INTEGER NOT NULL,
    applied_ts     TIMESTAMPTZ NOT NULL DEFAULT now(),
    note           TEXT
);

-- §4.7 视图 — 现状读取与对账

CREATE OR REPLACE VIEW v_orders_current AS
SELECT *
FROM orders
QUALIFY row_number() OVER (PARTITION BY order_id ORDER BY seq DESC) = 1;

CREATE OR REPLACE VIEW v_fills_effective AS
SELECT *
FROM fills f
WHERE f.voids_fill_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM fills v WHERE v.voids_fill_id = f.fill_id);

CREATE OR REPLACE VIEW v_order_filled AS
SELECT order_id,
       sum(qty)                               AS filled_qty,
       sum(qty * price) / nullif(sum(qty), 0) AS avg_fill_price,
       min(fill_ts)                           AS first_fill_ts,
       max(fill_ts)                           AS last_fill_ts,
       count(*)                               AS n_fills
FROM v_fills_effective
GROUP BY order_id;

CREATE OR REPLACE VIEW v_positions_eod AS
SELECT *
FROM positions_daily
QUALIFY row_number() OVER (PARTITION BY snapshot_date, ticker ORDER BY snapshot_ts DESC) = 1;

CREATE OR REPLACE VIEW v_recon_ledger_qty AS
SELECT o.ticker,
       sum(CASE WHEN o.side = 'buy' THEN f.qty ELSE -f.qty END) AS ledger_qty
FROM v_fills_effective f
JOIN v_orders_current o USING (order_id)
GROUP BY o.ticker;

CREATE OR REPLACE VIEW v_pdt_latest AS
SELECT *
FROM pdt_ledger
QUALIFY row_number() OVER (ORDER BY event_ts DESC, entry_id DESC) = 1;
