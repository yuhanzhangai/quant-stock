# ORDER_LEDGER_SPEC 会签意见 — %Exec(orders/fills 写入方)

> 对象:主仓 `docs/ORDER_LEDGER_SPEC.md` **r2 版**(2026-06-10 14:47,含 %Valid 修正:
> `signal_id = 'sig_' || tweet_id || '_' || ticker`、`tweet_id` 撤销 UNIQUE)。
> 结论:**同意(原则上)**,附 3 条 spec 修订建议(其中 2 条建议在实施前采纳)+ 2 条待 C5 实盘核验的诚实声明。
> 立场:我是 ledger 唯一写入方;以下逐条对应 Lead 指定的五个核点。

## r2 变更对执行层的影响:无异议,且更优

signal 粒度从 per-tweet 改为 per-(tweet, ticker),与 orders 的 per-ticker 粒度一一对应,
`direction_flip` 退出时 `exit_trigger_signal_id` 也能精确指到"那条帖里的那只票"。
`orders.signal_id` 外键对我透明(我只引用不构造)。会签按 r2 版本出具。

## ① fills 字段够不够回采对账用 —— 基本够,缺一个关键锚点

`fills(fill_id, order_id, fill_ts, qty, price, raw_text, scraped_ts, voids_fill_id, note)` 配合
`v_fills_effective`/`v_order_filled` 对我可用;`raw_text` 原始文本快照与我的审计哲学一致,
Playwright `inner_text` 可抓(✓ 已实现同类原语 `session.read_text`/`read_table`)。

**建议 1(实施前采纳):`orders` 增加 `broker_order_ref TEXT`(可空)。**
Firstrade 提交确认页/订单状态页**预期**会显示券商侧订单号(是否真有、长什么样待 C5
首登核验——故设计为可空,没读到就空着,前提不成立时优雅降级)。没有它,当**同票多单并存**时,
回采的成交行与我们的 `order_id` 只能靠"时间窗 + qty 猜配"——脆,且错配会污染审计件。
有它,fills↔orders 匹配有唯一可靠锚(fills 经 order_id 链接即可,不必重复加列)。
seq=0 行可空(确认页没读到就空着),回采到再追加事件行补记。

**建议 2(实施前采纳):fills 幂等纪律写进 §5.3。**
我的回采是**轮询型**(同一订单页会被反复读)。append-only + 重复轮询,若无去重约定 =
同一笔成交反复插行,对账永远不平。建议 spec 明文:writer 插入前按自然键
`(order_id, fill_ts, qty, price)` 查 `v_fills_effective`,已存在则跳过;
真实的同键重复成交(罕见)靠 `raw_text`/broker 序号区分后人工裁决。

## ② append-only 纪律与执行循环兼容性 —— 兼容,确认如下

- 我的循环单线程串行,天然满足"单写者";现有审计 JSONL(`data/execution/audit.jsonl`,
  页面动作级)与 ledger(业务事件级)互补,不冲突、不合并。
- 我的代码本就无 UPDATE/DELETE 语义,insert-only API 直接兼容。
- **一个落账时序约定请写进实施批次**:`orders` seq=0(status='submitted')在
  **提交点击成功后立即落账**,不等确认页解析——若点击后、读确认前出异常
  (我的 trader 对一般异常会 engage kill 并停;若是外部已触发的 ExecutionHalted
  则直接停,kill 已在),券商侧订单可能已存在,ledger 必须先有这一行
  (note 标 `confirmation_unverified`),否则出现"券商有单、账上无单"的对账黑洞。

**建议 3:`orders.status` 枚举补 `expired` 终态**(`submitted→expired`、`partial→expired`)。
日内限价单收盘未成交是 Firstrade 真实会发生的终态,既不是 cancelled(没人撤)也不是
rejected。若不加,请在 spec 写死映射口径(expired 记作 cancelled + note),
别让 writer 遇到未知态时自行发明——但加枚举更干净。

**建议 4(请 r3 澄清,spec 内部矛盾):§5.2 终态封锁 vs §5.3 追加更正的冲突。**
§5.2 规定终态(filled/cancelled/rejected)后"不得再追加状态行";§5.3 规定 orders
"任何更正=追加新事件行(seq+1)"。若回采解析错误把订单**误记成终态**(如误判 filled),
writer 既不能 UPDATE(铁律 1)也不能追加更正行(§5.2 禁止)——账永久错,§7 对账
永久不平且停新单无解。fills 有 voids 作废机制,orders 终态没有对应的更正出口。
请明确:更正行豁免终态封锁(note 必标 correction),或给 orders 引入 voids 类语义。

## ③ pdt_ledger 最新快照做下单闸门 —— 接口可用,一个边界提醒

`v_pdt_latest` 读 `day_trades_5d`/`settled_cash`(两列每行 NOT NULL)对我是一次轻量读,
下单前查询无压力。边界:快照的滚动 5 日计数**只在写入时刻准确**——若最新快照是昨天的,
今天窗口前移后真实计数可能已回落,快照会**高估**(**以已落账事件为准的前提下**方向
fail-safe,只误伤不放水)。
建议闸门实现:最新快照 `trade_date < 今日` 时,用原始 `day_trade` 事件行现算滚动计数,
快照做交叉核对(spec §4.5 本就要求两套账互验,这里只是把"何时用哪套"说清)。
**独立的低估通道(与快照过期正交,要心里有数)**:day-trade 判定挂在平仓腿成交上,
而成交确认靠轮询回采——券商侧已成、回采未落账的 day trade 不在任何一套账里,
此窗口内闸门会放行本不该放的单。靠两点兜底:确认失败即停新单(我的 trader 现行为)
+ §7 日终对账;正常轮询间隔内的残余窗口是已知接受的风险,记入实施批次文档。

## ④ kill_switch 状态随单快照 —— 可行,一个反向约定请写明

`orders.kill_switch_engaged BOOLEAN` 每事件行快照:我的 `KillSwitch.engaged` 属性现成,
每次落账时读一下即可,零成本。`exit_reason='kill_switch'` ⇒ `kill_switch_engaged=TRUE`
的一致性约束也认。
**反向约定(请写进实施批次):ledger 写入本身不被 kill-switch 阻断。**
kill 只停**页面动作**(我的 guarded 原语已如此),记账必须永远能记——kill 触发后的
撤单事件、HALT 留痕恰恰是最需要落账的时刻。

## ⑤ 初始资金 / 单仓 $5k —— 机制确认,数额待核验(诚实声明)

- **机制上无障碍**:整数股下单(我的 `OrderIntent.qty` 是 int,> 0 校验),
  qty = floor($5000 / price) 由引擎层算好传我即可;ledger `qty DECIMAL(18,4)` 比我宽,兼容。
- **数额我现在无法确认**:Firstrade 模拟盘初始资金额度、是否可配置、是否支持碎股,
  我**尚未实盘核验**(还没首登,选择器全部 verified:false)。请 Strat 把初始资金/单仓
  金额做成 rule_version 配置,**不要写死 $5k 假设**;C5 首登后我第一时间回填实测值。
- 同理,状态机对 Firstrade 真实订单状态的覆盖(spec §9 要我确认的)我只能给
  **预核验意见**:枚举(+expired)看起来覆盖标准券商状态;最终以 C5 实盘为准,
  遇到映射不了的页面状态,按"先停不硬冲"处理(停新单 + 告警),不静默落账。

## 其余确认(无异议)

- 分库(`data/execution/ledger.duckdb`)+ gitignored:与我现有 `data/execution/` 布局一致。✓
- `positions_daily.raw_text` spec 标"可选但建议"(注:§7 第 1 步行文是"含 raw_text 原件",
  与列注释口径略不一):**我方承诺必填**,实施时建议干脆 NOT NULL,消掉这个不一致。
- 流程建议:spec 文件目前未入 git(无 hash 可锚定版本),建议 Lead 尽快 commit,
  后续 r3 等版本断言可由 git 哈希锚定,会签/审计链路更干净。
- §7 对账不一致 → 告警停新单:与我的"出错先停"语义一致;实施时我倾向直接 engage
  kill-switch(停一切页面动作,比只停新单更保守)。
- 状态迁移白名单由 writer 落账前校验、非法拒写并告警:认领,这是我的职责。
- `ticker_not_tradable` 原因码:注意我的 `OrderIntent` 校验当前拒收带点/横杠 ticker
  (BRK.B 类),引擎层遇 PROVEN 喊单是这类票时应走 skip(`ticker_not_tradable`),
  别让 ValidationError 上抛炸循环——实施批次的引擎↔trader 接口处理好这层。
