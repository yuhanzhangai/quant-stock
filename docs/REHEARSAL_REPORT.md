# REHEARSAL_REPORT — 端到端演练(P1 收尾,P2 首登前置门)

> 执行:%Exec,2026-06-10。脚本 `scripts/exec_rehearsal.py`(可重跑,确定性注入价)。
> 链路:**真实信号 → 规则引擎 → ledger 假单 → 假成交回采 → 每循环 parquet 导出 → EOD 对账**。
> **绝不碰浏览器**;主树信号库只拷贝不写(红线 1);产物全部 gitignored。

## 结论:验收四问全 PASS(退出码 0)

| 验收 | 结果 | 证据 |
|---|---|---|
| ① 审计五问 SQL(spec §8 原文)一条答全 | **PASS** | 样例单 `ord_…HA4D9`:跟谁(stocksavvyshay)/何时喊/下单延迟 ms/成交 16@294.40/状态 filled,全字段非空 |
| ② Dash 检测导出自动脱 MOCK | **PASS** | `export_available=True`,页面选择逻辑选中 reader(`IS_MOCK=False`),freshness=fresh,真数据 orders=10 / signals=61 |
| ③ Valid 对账不变量 A 组(RECON_DESIGN_V0 §2) | **PASS ×9** | A1–A9 全 0 违反(A5 按 r3 口径:更正行豁免终态封锁,见下「待对齐」) |
| ④ agent_runs 心跳完整 | **PASS** | 3 轮 run 全部 started/finished 落定、export_ok=TRUE、error 全空 |

## 演练数据(全真实信号,2026-06-04 ~ 06-10)

- 候选 **71** 条(Data 管线 `signal_snapshots.duckdb` 拷贝)→ 规则引擎 v0.1 出决策 **61**(10 条窗口未到 pending 不出行)。
- 决策分布:followed **10**(`all_gates_passed`)/ skipped 51:`signal_stale` 29、`risk_cap_exceeded` 9、`handle_cap_exceeded` 6、`duplicate_signal` 5、`merge_lost` 2——六种原因码真实触发,门序与 COPYTRADE_RULES_SPEC §8 一致。
- 订单 10 笔假提交:终态 filled 9 / expired 1;fills 12 行(有效 10)。
- **§5.3 修正机制双演练**:① fills 作废对(错价→void→重记,`v_fills_effective` 自动剔除);② orders 更正行(误记 filled→`corrects_seq` 更正回 submitted→正常迁移 expired,r3 终态豁免实跑)。
- B4 簿记:初始 $100k → 10 笔 cash_debit → 期末 settled_cash $51,320.85;EOD 持仓 9 票,`recon=ok` 落 `eod_snapshot`。
- 每轮收尾 parquet 原子导出(3 轮),契约目录 `data/execution/export/`,Dash 即刻可读。

## 诚实声明(演练 ≠ 实盘,以下均待 P2 起核验)

1. **价格是注入假价**(确定性 $5–$500):P2 接 Firstrade 读层前没有权威决策时价源,演练不假装有。成交价=限价,无滑点。
2. **成交是剧本**:fake fill 文本自造,`raw_text` 格式与真实 Firstrade 页面无任何对应关系;选择器仍全部 `verified:false`。
3. **§7 对账在演练里是套套逻辑**:`positions_daily` 由 fills 推算生成,B 组(ledger vs 真实页面)完全未覆盖——那是 P2/C5 的事。A 组(ledger 内部完整性)是本次真验的部分。
4. **初始资金 $100k / 单仓 $5k 是假设**(rule_version 配置,未写死代码),真实模拟盘额度待 operator 首登核验后回填。
5. **退出链未演练**:无平仓腿(sell/exit_reason/direction_flip),A8 用弱口径(总量);hold_21d 等退出逻辑 P2+ 演练。
6. **A5 字典需同步**:RECON_DESIGN_V0 §2 A5「终态后无后续状态行」早于 r3,演练实现按 r3 加了更正行豁免——请 %Valid 在字典升版时吸收(已在结果行标注)。
7. 演练导出占用契约默认目录:P2 真 ledger 首轮导出会自然覆盖;如需提前清场 `rm -rf data/execution/export data/execution/rehearsal`。

## 复现

```bash
uv run python scripts/exec_rehearsal.py   # 退出码 0 = 四问全过;可重复跑(每次重建演练目录)
streamlit run dashboard/app.py            # 11/12 页应显真数据(无 MOCK 横幅)
```
