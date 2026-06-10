# INTEGRATION_NOTES — 接 stock-picker-mcp 数据(信号源/标的池)

> 由 stock-picker Lead 回答 quant-stock 团队 5 问(2026-06-10,基于真实 schema/代码核实,非猜测)。
> 两库路径(同机、quant 只读):`~/.stock-picker-mcp/trackrecord.db` · `~/.stock-picker-mcp/tweets.db` · 诚实榜 CSV `~/stock-picker-mcp/exports/leaderboard_honest_<date>.csv`(也发布到 `~/spm-web/exports/`)。
> **铁律:quant 侧只读这些库/文件,绝不写 stock-picker 的 canonical DB。**

## 1. PROVEN 名单
- **不是 DB 视图,是 CSV 导出**:`exports/leaderboard_honest_<YYYY-MM-DD>.csv`,取最新日期那个。
- **判定 PROVEN** = `status` 列 == `"PROVEN"`,**且**用 21d 口径(`horizon=="21d"`)。底层方法=Wilson 95% CI 下界 > 0.5 + `cross_regime==True`(代码 honest_leaderboard.py,别在 quant 侧重算、直接读 status)。
- ⚠️ **CSV 是 CRLF**:末列 `status` 带 `\r`,精确匹配前必须 strip(`row['status'].strip()`),否则 0 命中。
- 列序:`handle,horizon,n,hit_rate,wilson_lo,wilson_hi,bull_n,bull_hit,bear_n,bear_hit,avg_dir_abret,span_days,earliest,latest,cross_regime,status`。
- 其它 tier:`TRACKING`/`FADE`(=PROVEN_BAD,反指)/`INSUFFICIENT`(n<30)/`PROVEN_1REGIME`/`PROVEN_BAD_1REGIME`。
- **刷新**:每天 **13:30 PT** dailymaint 自动 `evaluate→regen`,产新日期文件(幂等可覆盖)。quant 每日取最新即可。

## 2. 新喊单增量读取
- 表 `trackrecord.db / analyst_calls`。字段:
  - 发帖时间 = **`call_ts`**(ISO8601 UTC,如 `2026-06-10T19:27:45+00:00`)+ `call_date`(日期串)
  - 股票 = `ticker` · 方向 = **`direction`**(`bullish`/`bearish`/`neutral`)
  - 原帖 ID = **`tweet_id`**(X snowflake,时序单调)· 是否喊单 = `is_call`(1/0,只取=1)
  - 信度 = `confidence`(0-1)· 力度 = `conviction`(low/medium/high)· 博主 = `handle`/`author_id`
- ⚠️ **没有目标价/止损字段**——我们只抽方向,不抽 target_price/stop_loss。别假设有,要的话 quant 自己从原帖文本(见 §3)再抽。
- **原帖 URL**:`tweets.db.tweets.url` 直接有;或拼 `https://x.com/{handle}/status/{tweet_id}`。
- **延迟**:爬虫连续跑(约每博主每小时一轮),发帖→入库通常 **< 1-2 小时**。
- **增量推荐**:按 **`call_ts > last_seen` 轮询**(跨博主统一时间轴最稳);`tweet_id` 雪花单调可作辅助游标。建议落一个 `last_seen_call_ts` 水位。

## 3. 原帖留档(下单依据快照)
- ✅ **存全文**:`tweets.db / tweets` 表,`text` 字段 = 原帖全文。**无截图**(纯文本 + 媒体 URL)。
- 相关列:`id`(=tweet_id)`handle` `created_at` `text` `url` `media` `has_media` `like_count`/`retweet_count`/`view_count` `tickers` `sentiment`。
- 取法:`analyst_calls.tweet_id` JOIN `tweets.id` 取 `text`+`url`。**做"每单附当天下单依据帖子快照"=存这条 text + url + created_at**(自己复制一份留档,别依赖 stock-picker 的库长期不变)。
- 注:`tweets.blocked=1` 是合规屏蔽帖(中文敏感),展示/对外别用;quant 内部信号可用但知悉。

## 4. 退出信号(诚实 gap,重点)
- **没有独立的"退出"事件建模,也没有 entry↔exit 配对。**
- 博主止盈/平仓喊单会被 direction_review 归类成 `bearish`(rubric:took profits/trimmed/I'm out/做空/清了 → bearish),但**不标记"这是对前一笔多头的平仓"**。
- `call_outcomes` 表是**事后评估**(固定 1d/5d/21d 点对点 fwd_return vs SPY、is_hit、abnormal_return),**不是博主自己的退出动作**。
- **结论:跟单退出逻辑 quant 侧自建**。我们的护城河是"入场喊单准不准",不是退出信号。可选退出依据:固定持有期(对标 call_outcomes 的 21d)、自己的止盈止损、或监测同博主对同票的反向新喊单(direction 翻转)当退出触发。

## 5. 冲突/聚合(同票多博主、多空冲突)
- 有现成算法,但在**前端 TS**(`~/spm-web/web/lib/queries.ts`),**非可复用 Python 模块——借鉴算法、quant 侧 Python 重写**。
- **真实加权值(直接照用)**:tier 质量权重 `W = {PROVEN: 3, TRACKING: 1.5, FADE: 1, INSUFFICIENT: 0.5}`(queries.ts:866)。
- 聚合伪代码(按 ticker):
  ```
  对每个 ticker, 窗口内(如近7天/隔夜)所有 is_call=1:
    calls = analyst_calls where ticker=T and call_ts in window
    # 去重:同 handle×ticker×direction 只算最新一条(防同人刷量)
    dedup = latest per (handle, ticker, direction)
    tier(handle) = 读诚实榜 CSV status(n<30→INSUFFICIENT)
    heat   = Σ W[tier]                       # 质量加权热度(非单纯计数)
    bull_w = Σ W[tier] for direction=bullish # 多空加权分桶
    bear_w = Σ W[tier] for direction=bearish
    net    = bull_w - bear_w                  # >0 偏多, <0 偏空
    proven_bulls/bears = distinct PROVEN handles 各方向
    consensus = 1 - |bull_w-bear_w|/(bull_w+bear_w)  # 1=完全分歧
  信号强度建议: net 方向 × heat,且要求 proven 参与(纯 TRACKING/FADE 信号降权或忽略)
  ```
- 反指博主(FADE/PROVEN_BAD)处理:他们的 bullish 其实是反向信号——quant 决定是反着用还是直接忽略(我们前端是当"反指之星"展示真实低命中)。

## 协同建议
- 一条干净的链:**诚实榜 PROVEN 喊单(选股) → quant 标的池/信号 → 回测验证 edge → Firstrade 模拟盘跟单 → 对账**。
- 起点最小可跑:只取 **21d PROVEN + bullish 的新喊单**(最高质量子集)做第一版信号,验证有没有 edge,再扩。
- 退出靠 quant 自建(§4);别等 stock-picker 给退出信号。
