# SIGNAL_ADAPTER_PRERESEARCH — stock-picker 只读信号适配层(预研)

> Data(quant:1.0)产出,2026-06-10。依据 `docs/INTEGRATION_NOTES.md`(stock-picker Lead 亲写)+ 对两库/CSV 的实地核查。
> **铁律:stock-picker 两库(trackrecord.db / tweets.db)+ 诚实榜 CSV 一律只读,绝不写。** 所有 sqlite 连接走 `src/signals/paths.py::connect_readonly()`(URI `mode=ro`,写操作直接报错);我方落地数据只写本仓 `data/signals/`(已 gitignore)。

## 0. 实地核查结论(2026-06-10,均为只读查询实测)

| 项 | INTEGRATION_NOTES 说法 | 实测 |
|---|---|---|
| 最新诚实榜 CSV | `exports/leaderboard_honest_<date>.csv` 取最新 | ✅ `2026-06-10` 存在于两个发布点 |
| CSV CRLF 坑 | 末列 status 带 `\r` 必须 strip | ✅ 确认 CRLF;读取器对全部字符串列 strip |
| `analyst_calls.call_ts` | ISO8601 UTC | ✅ 且 is_call=1 的行**零空值**(可放心做水位) |
| direction 取值 | bullish/bearish/neutral | is_call=1 内实测只有 bullish(18,521)/bearish(2,659) |
| 近 30 天喊单量 | — | 5,008 行(一帖多票),distinct tweet_id 2,464 |
| tweets JOIN 覆盖 | `analyst_calls.tweet_id` JOIN `tweets.id` | ✅ 近 30 天孤儿率 **0/2464** |
| `tweets.created_at` | "as returned" | 实测 ISO8601 UTC;`fetched_at` 为 unix 秒(入库点时) |
| `tweets.url` | 直接有 | ✅ 零空值(仍保留拼 URL fallback) |
| `blocked` | 合规屏蔽帖 | 745/116,799;快照保留标志,对外展示不用 |
| 入库延迟 | 发帖→入库 < 1-2h | 稳态 p50≈45min / p90≈1.5h / p95≈3.3h,基本属实;但有 ~1.5% 深档回扫长尾(详见 §2) |
| status 枚举 | 6 种 | **实测 7 种:文档漏了 `PROVEN_BAD`(19 行)**,前缀匹配 'PROVEN' 会误收 49 行(真 PROVEN 仅 17) |

## 1. 诚实榜 CSV 读取器(`src/signals/honest_leaderboard.py`)

- 发现:按文件名日期取最新,探测顺序 `~/stock-picker-mcp/exports/` → `~/spm-web/exports/`(主发布点优先)。
- 解析:全部字符串列 strip(化解 CRLF 残留 `\r`);数值/布尔列显式 cast。
- 过滤:`status` **精确等值** `== "PROVEN"`(前缀匹配会误收 `PROVEN_1REGIME`/`PROVEN_BAD_1REGIME`)且 `horizon == "21d"`。
- 不在 quant 侧重算 Wilson CI/cross_regime——直接信 status 列(stock-picker Lead 明确要求)。
- 刷新节奏:上游每天 13:30 PT 重新生成;我方每日取最新即可,无需 watch。
- **实测(2026-06-10 CSV,469 数据行)**:status 分布 INSUFFICIENT 235 / TRACKING 168 / PROVEN_BAD_1REGIME 28 / **PROVEN_BAD 19(INTEGRATION_NOTES 枚举未列,实测发现)** / PROVEN 17 / PROVEN_1REGIME 2 / FADE 0。
- **PROVEN@21d = 5 人**:stocksavvyshay(n=3832, hit 0.535)、joely7758521(n=258, 0.651)、jimmyhuli(n=214, 0.570)、etfswingtrader(n=44, 0.818)、danzanger(n=44, 0.773),全部 cross_regime=true。
- API:`discover_latest_csv() -> Path` · `load_leaderboard(path=None) -> pl.DataFrame`(全列 strip + 显式类型化,空字段→null,cross_regime null→False)· `proven(horizon='21d') -> pl.DataFrame` · `proven_handles(horizon='21d') -> list[str]`。

## 2. analyst_calls 增量轮询(`src/signals/calls_poller.py`)

**核心设计:事件时间水位 + 回看窗 + 去重**(纯 `call_ts > last_seen` 水位会漏迟到行)。

- 为什么会漏:`call_ts` 是**发帖时间**(事件时间),而行的**入库时间**晚 0~2h(爬虫每博主约每小时一轮)。若水位已被新帖推前,之后才入库的老帖(call_ts < 水位)永远查不到。
- 方案:每轮查 `is_call=1 AND call_ts > (水位 - overlap)`,用 `seen_recent`(`tweet_id|ticker` → call_ts)去重后只返回真正新行;水位推进到本批最大 call_ts;`seen_recent` 修剪到回看窗内防膨胀。
- **延迟实测(2026-06-10,14 天样本 n=1361,JOIN 孤儿 0、负延迟 0、min=41s)**:
  - 原始分布被爬虫启动期回填污染(tweets.db 最早 fetched_at=06-02,06-05~06-07 集中回填占样本 81%):原始 p50=88.8h / p99=242.6h,**不可用于定 overlap**。
  - 剔除回填期(fetched_at≥06-08)稳态 n=260:**p50≈45min、p90≈1.5h、p95≈3.3h**;稳态 p99 仍被 4 条"深档回扫"拉到 115.7h(05-29~06-04 旧帖几天后才首次入库)。
  - overlap 覆盖率:3h=93.8% / **6h=97.3%** / 12h=98.1% / **24h=98.5%** / 48h=98.5%(平台期)。
- **取值结论**:默认 `overlap_hours=6`(覆盖 97.3%),生产建议 24h(98.5%,窗口查询代价极小)。残余 ~1.5% 是深档回扫(不是慢爬,是旧帖几天后才被发现),**overlap 结构上覆盖不了**,兜底 = 日级对账(bootstrap 窗口全量重扫,seen_recent 去重天然支持)。超出 overlap 的迟到行会漏由测试固化为已知行为。
- 去重 key 必须 `(tweet_id, ticker)`:表主键即此,一帖多票是常态(30 天 5,008 行 / 2,464 帖)。
- 首轮 bootstrap:从 `now - 7d` 起(不全量回灌;历史回测用全量另算)。
- 水位状态:JSON 持久化在 `data/signals/`(沿用 paper_runner 的本地状态文件模式)。
- 字符串比较即时序:同格式 ISO8601 UTC 字典序 = 时间序(实测全库 21,180 行统一 `YYYY-MM-DDTHH:MM:SS+00:00`、len 25、零 NULL);**水位与每条取回行都有格式断言**(混入 'Z' 后缀/naive 串会静默漏单,违例直接 ValueError fail loud)。
- 水位单调不回退:取 `max(旧水位, 本批最大)`——某轮只有迟到行时字面"本批最大"会把水位拉回、窗口反复扩大。
- API:`PollerState` / `load_state` / `save_state`(原子写 + 写路径守卫)/ `poll_new_calls(state, overlap_hours=6.0, bootstrap_days=7.0, conn=None) -> (pl.DataFrame, PollerState)` / `measure_ingest_latency(sample_days=14)`(`python -m src.signals.calls_poller` 直接打印)。

## 3. 原帖快照(`src/signals/tweet_snapshot.py`)

**目的:每单留"下单依据"的点时副本,不依赖 stock-picker 库长期不变。**

- 取法:`analyst_calls.tweet_id` → `tweets.id`(两库各自只读连接 + 内存 join,不做 sqlite ATTACH,降低误写面)。
- 存:我方 DuckDB `data/signals/signal_snapshots.duckdb` 表 `tweet_snapshots`,含 text/url/created_at/handle/author_id/媒体/互动数/blocked + `snapshot_ts`(复制时刻)。
- **幂等 = 首次写入即定格**:tweet_id 主键,已存在跳过不覆盖(快照语义:决策时点的副本,之后上游怎么变都不影响留档)。
- `blocked=1` 照存但带标志(内部信号可用,对外展示禁用)。
- 无截图:上游只有纯文本 + 媒体 URL;`url` 字段实测零空值,仍保留 `https://x.com/{handle}/status/{tweet_id}` 拼接 fallback。
- **`snapshot_ts` 必须 TIMESTAMPTZ**(对抗复核抓出的 high):naive `TIMESTAMP DEFAULT now()` 在本机(PT)会存偏 7 小时的墙钟,且事后无法区分;现为 `TIMESTAMPTZ DEFAULT now()` + 旧 naive schema 连接时直接拒绝(fail loud),配 UTC 守卫测试。
- 实测:近 7 天 5 条真实喊单快照(stocksavvyshay/NVDA、marketrebels/SMCI、kunal00/MU、schaeffers/CBRL、schaeffers/RGTI),孤儿 0、url 全 https、call 侧 ticker 与 tweet 侧 tickers JSON 全一致;重跑幂等(0 新增)。
- 表:`tweet_snapshots(tweet_id VARCHAR PK, handle, author_id, username, created_at, fetched_at BIGINT, text, url, media, has_media, blocked, like_count, retweet_count, view_count, tickers, sentiment, snapshot_ts TIMESTAMPTZ DEFAULT now())`。
- API:`snapshot_tweets(tweet_ids, db_path=…, tweets_conn=None) -> int`(新插入行数;孤儿/blocked 走 loguru warning)· `fetch_snapshot(tweet_id, db_path=…) -> dict | None`。

## 4. 上游明确不提供的(下游设计要自扛)

1. **无目标价/止损字段**——只有方向。要 target/stop 得从快照 text 自己抽(后续课题)。
2. **无退出信号建模**——博主平仓喊单会被归成 `bearish` 但不标"对前单的平仓";`call_outcomes` 是事后评估非博主动作。**跟单退出逻辑 quant 自建**:固定持有期(对标 21d)/ 自有止盈止损 / 同博主同票反向新喊单当退出触发。
3. **冲突聚合算法在前端 TS**(`spm-web/web/lib/queries.ts`)——不可直接复用,Python 重写时直接取真实权重 `W = {PROVEN: 3, TRACKING: 1.5, FADE: 1, INSUFFICIENT: 0.5}`(queries.ts:866)。本预研不实现聚合,留给信号层下一步。

## 5. 验收与下一步

- **验收(2026-06-10)**:pytest `tests/signals` **24/24 全绿**(含真实库只读 smoke,本机全部实跑非 skip)· ruff 干净 · pyright(`uv run --extra dev`)0 错误。
- **对抗复核(3 个独立审查者 + 1 轮修复)**:V1 门禁 PASS;V2 只读纪律 PASS(纵深防御建议已采纳 → `assert_writable_path` 写路径守卫 + 拒绝测试);V3 正确性抓出 1 high(snapshot_ts 时区,已修)+ 2 medium(写路径守卫、水位格式断言,已修),复核通过、遗留 0。
- 已知 low 备忘(不阻塞):`connect_readonly` URI 未做百分号转义(对两个固定路径安全,复用到含 `?`/`#` 路径前需加固);WAL 库只读连接会触碰源目录 `-shm` 文件(SQLite 标准行为,不改库内容,immutable=1 对活跃写入库不安全故不可用);测试 yield fixture 注解瑕疵。
- 环境注:pyright 需 `uv run --extra dev`(dev 工具在 optional-dependencies);venv 实际 Python 3.12。
- 下一步建议(等 Lead 排期):① 最小信号管线 = PROVEN@21d × bullish 新喊单(上游建议的最高质量子集)→ ② 喂回测验证 edge(Valid)→ ③ 聚合/冲突 Python 重写 → ④ 退出规则研究。
