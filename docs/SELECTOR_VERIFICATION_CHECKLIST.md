# 选择器核验清单(P2 首登配套,%Exec 带 operator 逐项过)

> 对象:`config/execution/firstrade_selectors.yaml` 全部 17 项(现全 `verified:false` 占位猜测)。
> 工具:浏览器 DevTools(主)+ `scripts/exec_verify_selectors.py`(只读探针,我来跑,operator 只管翻页)。
> 纪律:核验阶段**零点击零输入**(看 DOM 不动 DOM);凭据字段永远人工;过一项标一项 `verified:true`,我 commit 我分支。

## 0. 通用三关(每个选择器都过)

DevTools(Cmd+Opt+I)→ Console:

```js
$$("<css>").length   // 关 1:唯一性,必须 === 1(0=猜错,>1=歧义要收紧)
$$("<css>")[0]       // 关 2:正确性,hover 返回值 → 页面高亮的就是目标元素
// 关 3:稳定性 —— 刷新页面再跑一次;退出重进再跑一次;换一只 ticker 的变体页再跑一次
```

**选择器质量标准**(占位猜错时按此优先级找替代):
1. 非自动生成的 `id` > `name` 属性 > `data-*` 属性 > 语义化 class;
2. **拒绝**:框架自动生成 class(`css-1x2y`/`jsx-`/`ng-`/随机 hash——刷新或发版就变)、`nth-child` 位置流(加一行就错位)、依赖显示文本的匹配(数字/语言会变);
3. 过验后在 YAML 同项 `note` 里记:页面 URL 路径 + 元素 outerHTML 摘要 + 核验日期(审计链)。

## 1. login 组(核验时机:**先开一个未登录的隐身窗**验登录页,再用登录态验 marker)

| 项 | 验什么 | 注意 |
|---|---|---|
| `login_username` | 登录页用户名输入框,三关 | 用途仅为**识别被登出弹回登录页**;自动化绝不往里输 |
| `login_password` | 登录页密码框,三关 | 同上;`type_human` 对凭据字段是硬拒的(代码层保证) |
| `logged_in_marker` | **双态验证**:登录态下任意主要页面 `length>=1`;隐身窗未登录 `===0` | 候选:logout 链接 `a[href*='logout']`、账户菜单容器。要求在账户/持仓/下单页**全部**稳定存在(它是每轮 `ensure_logged_in` 的依据) |

## 2. paper_account_marker(最关键——逐单下单前置闸,找不到=拒单)

**选哪个元素最稳(按优先级):**

1. **首选:模拟盘账号号码元素**。operator 首登后会知道模拟盘账号号(Firstrade 模拟账户有独立账号)。选"展示账户号的那个元素",并在 note 里记录**预期账号值**——后续我把 trader 的闸升级为「元素存在 **且** 文本匹配预期账号」双保险。理由:页面改版 class 会变、banner 会撤,但账号号不变;且真实账户账号≠模拟账号,天然防"连错账户"。
2. 次选:页面显式的 paper/simulated/practice 字样 banner——必须是**布局级**元素(header/账户信息区),不要营销弹窗/toast(会消失)。
3. **拒绝**:用 URL 子串当 marker(不在 DOM、重定向即失效)、颜色/样式类、任何登录前也可见的元素。

**验什么:** 在 agent 会访问的**每一个页面**(账户、持仓、下单、订单状态)都跑三关——它是逐单前置检查,**必须在下单页可见**。若实测只在账户页有 → 当场告诉我,我把 require 时机改为"进下单流程前先回账户页核验"或改组合 marker,**不硬凑选择器**。

**诚实声明:** 我们没有真实账户登录态可对照,"真账无此元素"无法实证——账号号匹配方案正是补这个洞的。

## 3. account 组(持仓/账户页)

| 项 | 验什么 | 注意 |
|---|---|---|
| `positions_table` | 三关 + **结构验证**:`$$("<css> tbody tr").length` 应等于持仓行数 | `read_table` 依赖 `{css} tbody tr` + `td` 结构;**若 Firstrade 是 div 网格不是 `<table>`,别硬凑选择器,告诉我改 read_table 适配**。空仓时表壳也应存在(记录空仓时的表现) |
| `account_cash` | 三关 + `$$("<css>")[0].innerText` 是货币格式 | 把文本样例(如 `$100,000.00`)记进 note——回采解析按它写 |
| `account_buying_power` | 同上 | 模拟盘 buying power 与 cash 的关系也顺手记下(margin 假设核验) |

**顺手回填(Lead 台账两条诚实声明):** 账户页看到的**初始资金额度**、是否支持碎股——报我,我转 Strat 定 rule_version 配置实值。

## 4. order 组(下单页 —— 只看不点!)

| 项 | 验什么 | 注意 |
|---|---|---|
| `order_symbol_input` / `order_qty_input` / `order_limit_price_input` | 三关 + 确认是 `<input>` | **核验阶段不输入任何字符** |
| `order_side_buy` / `order_side_sell` | 三关 + 看 DOM 的 `for`/`role`/结构确认是真切换控件 | 不实际点击验证效果——首笔受控下单时再验行为 |
| `order_type_limit` / `order_type_market` | 同上 | 同上 |
| `order_preview_button` | 三关 + **按钮文案确认是"预览/Preview"而非直接提交** | 这是 dry_run 安全边界:trader 的 dry_run 档最远走到 preview 之前 |
| `order_submit_button` | **只做 DOM 识别,绝不点击**;note 标 `identified-not-clicked` | 真正首次点击=首笔受控下单,operator 在场 |
| `order_confirmation_text` | **本阶段无法核验**(不提交就没有确认页) | **留 `verified:false`**,首笔模拟盘单提交后现场核验补标。不假装能提前验 |

**顺手观察(影响 ledger):** 提交确认页/订单状态页是否显示**券商侧订单号**(`broker_order_ref` 的前提假设,r3 设计为可空降级)——首单时重点看。

## 5. 流程与完成标准

1. operator `make exec-login` 人工登录(含 2FA)→ 保存登录态(已 gitignored)。
2. 我起 `scripts/exec_verify_selectors.py`(只读探针,kill 可停);operator 在浏览器翻页:登录页(隐身窗)→ 账户页 → 持仓页 → 下单页。每页我敲组名探测 + DevTools 三关。
3. 占位猜错的:用 `css <候选>` 探针现场找真选择器,按 §0 质量标准定稿。
4. **17 项中 16 项**可本阶段核验;`order_confirmation_text` 留待首笔受控单。
5. 过验一批 → 我改 YAML(`css` 修正 + `verified:true` + note 记录)→ 自检 → commit 我分支 → 报 Lead。
