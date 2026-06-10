# FOLLOW_PERF — S 账快照(signal / counterfactual)

> 生成 2026-06-10T23:58:30+00:00 · code_commit `ec1a010` · 复现:`uv run python -m src.perf.s_account`
> 水位:call_ts 2026-06-04T03:56:18-07:00 → 2026-06-10T15:29:06-07:00 · tier_csv_date 2026-06-10 → 2026-06-10 · horizon 21 交易日(口径=上游 call_outcomes,JOIN 消费不重算)
> **未经独立复核**(强制审核制度 2026-06-10 废止,按新质量纪律自检后发布)

## 漏斗

| outcome_status | n | 说明 |
|---|---|---|
| evaluated | 0 | 进入汇总 |
| pending | 69 | 21d 窗口未熟,不进任何汇总 |
| no_price | 0 | 上游无价格序列,S 账缺失单列 |
| unmatched | 2 | call_outcomes 无对应行(上游尚未评估/缺失,日级对账跟踪)|

## 总体(仅 evaluated;死区 is_hit=NULL 不计入分母)

| n | graded_n | hit_rate | Wilson95 下界 | abnormal mean | abnormal median |
|---|---|---|---|---|---|
| 0 | 0 | —(graded_n<5) | —(graded_n<5) | — | — |

## handle × tier(graded_n<5 只报 n 不报率)

| handle | tier | n | graded_n | hit_rate | Wilson95 下界 | abn mean |
|---|---|---|---|---|---|---|
| jimmyhuli | PROVEN | 0 | 0 | —(graded_n<5) | —(graded_n<5) | — |
| stocksavvyshay | PROVEN | 0 | 0 | —(graded_n<5) | —(graded_n<5) | — |
| joely7758521 | PROVEN | 0 | 0 | —(graded_n<5) | —(graded_n<5) | — |

## ingest_lag 分桶(spec §3.2 桶;v0 仅 ingest 段,wall/actionable 待 E 账)

| 桶 | n | graded_n | hit_rate | abn mean |
|---|---|---|---|---|
| >3d | 0 | 0 | —(graded_n<5) | — |
| 1–3d | 0 | 0 | —(graded_n<5) | — |
| 6–24h | 0 | 0 | —(graded_n<5) | — |
| 2–6h | 0 | 0 | —(graded_n<5) | — |
| ≤2h | 0 | 0 | —(graded_n<5) | — |

## 延迟两段拆解(p50 / p90)

| 段 | p50 | p90 | 含义 |
|---|---|---|---|
| ingest_lag(端到端)| 65.8h | 151.7h | call_ts→ingested_ts,上游+我方之和 |
| upstream_lag | 1.1h | 78.1h | call_ts→上游 fetched_at,日级大=上游深档回扫 |
| poll_lag | 65.0h | 81.1h | fetched_at→ingested_ts,大=我方轮询间隔 |

## 已知局限(spec §5.3 + 本期)

- **本期 ingested_ts 含管线首跑回填**:7d 窗口一次性补录,ingest/poll 段延迟偏大,不代表稳态;稳态数字以管线常驻轮询后的下期为准。
- S 账为 counterfactual(上游 T+1 close 进出),非实际成交;E 账与 S−E 归因待 P3 真实成交接入。
- 上游价格序列复权方式未核实;`pending` 行随上游评估推进迁移,重跑数字会变(活库)。
- 绩效报告是观察记录,不是策略验证,不产生 PASS/上线判定(spec §5.4)。
