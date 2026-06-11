# PRICE_SOURCE_SPEC — PaperBroker 权威价源接口(Data→Exec 对接)

> %Data 供稿(2026-06-11),应 Lead 指令(ROADMAP P2)。给 Exec PaperBroker 提供"信号时点价 + 每日收盘价"。
> 立场:**接口契约 + 诚实约束**。价源绝不造假价(演练用的确定性 $5–$500 注入价到此为止);拿不到价就 fail-closed
> 抛 `PriceUnavailable`,由 PaperBroker 映射成 skip 原因码(`no_price` / `ticker_not_tradable`,ORDER_LEDGER_SPEC §5.1)。
> 实现:`src/data/price_source.py`。源:`prices.db`(只读日线)+ yfinance 补。

## 1. 接口契约(PaperBroker 调这两个 + 一个异常)

```python
class PriceUnavailable(Exception):
    """拿不到可信价:ticker 无效/停牌/超出数据窗口/yfinance 失败。绝不返回假价。"""

@dataclass(frozen=True)
class PricePoint:
    ticker: str
    price: float                       # 价格(见 adjusted 字段判定口径)
    as_of: datetime                    # 这个价真实代表的时刻(UTC, TIMESTAMPTZ 同源)
    requested: datetime | date         # 调用方要的时点(留痕,审计成交价偏差)
    kind: Literal["intraday", "daily_close"]
    source: Literal["yfinance", "prices_db"]
    adjusted: bool                     # True=后向复权价(prices.db);False=原始成交价(yfinance raw)
    is_stale: bool                     # as_of 与 requested 偏离超阈值(调用方决定容忍度)

class PriceSource:
    def price_at(self, ticker: str, ts: datetime, *, max_staleness_min: float = 120.0) -> PricePoint:
        """信号时点成交价(模拟成交用)。前向跟单 ts≈now → yfinance 近端 intraday/last。
        超 max_staleness 或拿不到 → PriceUnavailable。adjusted=False(原始可成交价)。"""

    def close_on(self, ticker: str, d: date) -> PricePoint:
        """日收盘价(每日 mark / 止损 / 21d 退出评估用)。prices.db 优先(已维护 1575 票),
        缺则 yfinance 补。拿不到 → PriceUnavailable。"""
```

## 2. 数据底座事实(决定能给什么、不能给什么)

- **prices.db**(`~/.stock-picker-mcp/prices.db`,settings.prices_db_path,**只读**):
  `price_cache(ticker, date, close REAL, fetched_at)` + `price_meta(ticker, last_pull, min_date, max_date)`。
  716k 行 / 1575 票;NVDA 覆盖 2024-06-11~2026-06-10;stock-picker 每日维护。
  - **只有日线 adjusted close,无 OHLCV、无 intraday**(记忆 [[prices-db-close-only]] 实证)。
  - **是后向复权序列**(实证:NVDA 2024-06-06 库里 $120.79,拆股前真实收盘 ~$1209,10:1 拆股回填)。
    → **复权价 ≠ 当时真实成交价**;只适合(a)近端无后续拆股的日 mark、(b)收益率口径(S 账 counterfactual)。
    **绝不能用来重建历史某日的"我会成交在多少"**。
- **yfinance**(1.4.1):
  - intraday `1m` 仅近 ~7 天、`1h` 近 ~730 天;**任意历史时点(>7d)拿不到分钟价**——前向跟单不受影响(信号 ts≈now,入库 p50≈45min)。
  - 可取 raw close(`auto_adjust=False` 的 `Close`)= 原始成交价;有限速 + 偶发失败,需缓存 + 重试。

## 3. 两个用途的价口径(关键,P&L 正确性所系)

| 用途 | 方法 | 口径 | 为什么 |
|---|---|---|---|
| **信号成交价**(E 账 entry) | `price_at(ticker, signal_ts)` | **raw**(adjusted=False),yfinance 近端 | 模拟成交=我那一刻真会付的价,要原始价不要复权价 |
| **每日 mark / 止损 / 退出** | `close_on(ticker, date)` | prices.db(adjusted)优先,yfinance raw 补 | 近端 + 无后续拆股时 adjusted==raw;1575 票已维护,省 yfinance 限速 |

> ⚠️ **复权一致性风险(必须 Exec/Valid 知晓)**:同一持仓周期内,若 `price_at` 用 raw、`close_on` 用 adjusted,
> 且**持有期跨越拆股**,则建仓 raw 价与拆股后被回填的 adjusted mark 不在同一基准 → P&L 失真。
> **前向 21d 持仓 + 无拆股时三者同基准(raw==adjusted),无问题**;跨拆股是边缘但真实。
> 缓解(建议,需会签):(a)mark 也走 yfinance raw 保持单一基准;或(b)prices.db 省限速但在 Valid 对账加
> **拆股事件侦测**(持仓期内 split → 该笔单独按 raw 重算)。MVP 默认 (b),拆股日列异常,不静默抹平。

## 4. 给 Exec 的决策点(PaperBroker 接线前定，本契约随之收口)

1. **成交价时点语义**:`price_at` 对一条 fresh 信号(ts 在最近交易时段内)返回"当前 last/近端 intraday"。
   但若信号 ts 落在**盘后/周末/停牌**,要的是:(a)下一个开盘价?(b)当日收盘价?(c)直接 skip(`market_closed`)?
   —— 这是 PaperBroker 成交策略,价源**只如实暴露 as_of + is_stale**,不替你选。请定策略,我配字段。
2. **staleness 容忍**:`max_staleness_min` 默认 120(对齐信号源入库 p50≈45min/p95≈3.3h)。超阈 → 你要"标 stale 仍成交"还是"skip `signal_stale`"?默认抛 `PriceUnavailable` 让你 skip,要软标我加 `is_stale=True` 不抛的开关。
3. **滑点**:ROADMAP 说"可配滑点"。建议滑点是 **PaperBroker 的成交模型**(对 mid/last 加 bps),不是价源职责——价源给干净市场价,滑点你加。同意否?
4. **缓存落点**:yfinance 结果缓存写**我方** `data/prices/price_cache.duckdb`(可写侧,gitignore),**绝不写 stock-picker prices.db**(只读铁律)。日级 close 也可回填本地缓存省 yfinance 调用。

## 4b. 与 Exec PaperBroker 口径对齐(2026-06-11,Exec @ 224f8bc 撮合=T_entry 收盘+滑点)

Exec 撮合用**入场交易日(T_entry)收盘价**,不是 intraday → **正好是 `close_on(ticker, T_entry)`,不是 `price_at`**。
`price_at`(intraday)Exec 当前用不上,留给 P3 实时成交/未来分钟撮合。回答 Exec 两问:

- **① price_cache 是否权威 + 新鲜度**:
  是,前向跟单的 T_entry 收盘 + 每日 mark 可用 price_cache。但**强烈建议改走 `close_on` 而非直读 price_cache**——
  它在 price_cache 缺该日时**自动 yfinance raw 补**,正是你问的"谁保证每日更新到最新交易日"的安全网:
  prices.db 由 stock-picker 每日维护(实测 max_date=今天),但**那是另一项目的 cron,我不控**;`close_on` 的
  yfinance 兜底使 prices.db 晚一天也不阻塞你。接口已就绪:`close_on(ticker, date) -> PricePoint`,fail-closed,带 `source`/`adjusted` 标。
- **② 入场日无价覆盖**:
  我**不能保证 100% 覆盖**(退市/次新/极低流动性/prices.db 未拉到)。`close_on` 在 prices.db + yfinance 均无时
  fail-closed 抛 `PriceUnavailable` → 你映射 `no_price`/`ticker_not_tradable`。**你"跳过不假装"是对的默认**,与我契约一致。
  "顺延到下一有价交易日"我支持但**别静默**:成交在 T_entry+k 收盘会比信号贵/偏,必须记 `PricePoint.as_of`(真实成交日)+
  drift,让 Valid 的 S−E 归因看到这段延迟成本;口径定为**最多顺延 N 个交易日,记 drift,超出则 skip**。N 由你定,我配字段。

> ⚠️ **复权时代错位(你的 2024-06-03 冒烟必看)**:price_cache 是**后向复权**价。你冒烟"NVDA 2024-06-03 收盘 114.94 建仓 43 股"——
> 但 2024-06-03 NVDA 拆股前真实成交价 ~$1149(10:1 拆股在 2024-06-10),**$114.94 是回填的复权价,当时根本买不到**,
> 据此算的 43 股是时代错位的虚构量。**前向跟单不受影响**(今天的收盘=今天真价,未来拆股尚未回填);
> 但**任何历史回放(用旧日期)在 price_cache 上都会拿到复权价而非当时真价**——历史回放请用 yfinance raw(close_on 的 yfinance 兜底即 raw),
> 或在 Valid 对账加拆股侦测。前向 paper 这条不咬,但边界你和 Valid 都要知道,别拿历史复权价当真实成交回放。

## 4c. 新鲜度机制(把 Exec 三问定死,前向 runner 据此无人值守常态跑)

**总原则:日收盘新鲜度由本仓负责,不依赖 stock-picker 的 cron。** prices.db 是优化(命中即省 yfinance),
yfinance 是真理来源(永远有最新已收盘交易日),我方缓存记录每次拉取(确定性 + 审计 + 省限速)。

- **① 谁更新 / 命令**:`uv run python -m src.data.warm_prices --date <交易日>`(票默认取自 `signal_candidates`,
  或 `--tickers NVDA,AMD`)。**前向 runner 每个交易日盘后先跑它再撮合**——它对每个 followed 标的:我方缓存/prices.db
  命中即算 covered,缺则 yfinance 拉 raw 写回我方缓存。退出码:`0`=全覆盖/已补齐;`2`=有 missing(yfinance 也拿不到,
  **runner 据此逐票 skip,不整轮停**);`3`=全 missing(疑似 yfinance 不可用,runner 停 + 告警)。
  > ⚠️ **不要用 `price_meta.max_date >= 目标日` 当闸门**:实测候选 35 票里 SPCX 在 price_meta 有行但 06-10 无 close,
  > "max_date 当前"≠"每票当天有价"。**正确闸门 = warm 返回的 missing 列表**(逐票),不是 stock-picker 元数据。
- **② 盘后多久保证当日 close**:本仓 warm 走 yfinance,**美东 16:00 收盘后 ≥1.5h(建议 17:30 ET / 14:30 PT 起跑)**
  yfinance 当日官方 close 已稳定可取。prices.db(stock-picker)落库时点我不控、不承诺;warm 的 yfinance 兜底使其无关紧要。
- **③ 某 followed 标的当天 close 还没落库怎么办**:warm 已是 followed 标的的**优先补抓**(yfinance 按需,小时级当前)。
  仍真拿不到的尾部(退市/次新/停牌/无 Yahoo 数据,如实测 SPCX)→ `close_on` fail-closed 抛 `PriceUnavailable`
  → **建仓:你 skip 不假装,正确**(映射 `no_price`/`ticker_not_tradable`);**持仓 mark/退出:别静默跳过**——
  缺价时沿用上一已知 mark 并标 stale(不强平、不造价),差异交 Valid 对账,绝不用旧价静默成交。我无法保证 100% 覆盖(无 Yahoo 数据的票客观存在),skip-honest 是对的默认。

> 接口形态:runner 可直接 `PriceSource().warm_daily_close(tickers, d)` + `close_on(ticker, d)`,或 cron 调上面 CLI。
> 我方缓存落 `data/prices/daily_close.duckdb`(可写侧,gitignore);**绝不写 stock-picker prices.db**(只读铁律,已加路径守卫 + 测试)。

## 5. 红线遵守
- stock-picker `prices.db` **只读**(`mode=ro` / DuckDB read_only);我方缓存只落 `data/prices/`(gitignore)。
- 价源**只读不下单**,与执行/真金无关;P2 本地 paper、P3 真钱都共用这一价源(P3 真金成交价仍以券商回执为准,价源只做决策/mark)。
- 拿不到价 fail-closed,**绝不造假价**(演练注入价范式终止)。
- 含数据结论未经独立复核(审核制 2026-06-10 废止);事实可经 prices.db 行 + yfinance 复核。
```
