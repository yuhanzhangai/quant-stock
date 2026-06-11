# FIRSTRADE_API_RESEARCH — MaxxRK/firstrade-api 只读调研

> %Data 供稿(2026-06-10),应 Lead 指令。**纯只读**:克隆源码到 `/tmp` 逐行读 + 官方文档 + GitHub/PyPI 元数据;
> **全程未输入任何真实账号/密码/2FA,未发起任何登录或下单请求,未碰任何 Firstrade 账户。**
> 配合 %Exec `docs/SAFETY_GATE_LIB_MIGRATION.md`(track/exec @ b667219)的四闸矩阵 + 情况 A/B/C 框架。
> 调研对象:`MaxxRK/firstrade-api` @ `d7c5952`(2026-05-29),PyPI 包名 `firstrade` v0.0.39。

## 0. 一句话结论(决策级)

**库路线对本项目 PAPER_ONLY 否决 —— 命中 %Exec 框架的情况 B(致命)。**
**根因:Firstrade 平台本身不提供模拟盘(paper trading)产品**,因此这个库里没有、也不可能有"模拟盘账户";
它的每一个下单 endpoint 打的都是登录用户的**真实经纪账户**。在本项目语境下,把这个库接进下单链路 =
写真金下单代码 = **违反红线 2**。光是为执行而 import 它,就逼近红线 2 的"真实下单代码"灰区(dual-use)。

> 这不是工程细节,是可行性前提。情况 B 一旦成立,后面的 API 完备度/凭据方案都不改变"否决"结论——
> 它们只在情况 A(有模拟盘账户号)下才有意义,而情况 A 不成立。

## 1. 库 vs 浏览器 —— 一页对比

| 维度 | firstrade 库(本调研) | 浏览器路线(%Exec 现状,129 测冻结) | 对本项目的判定 |
|---|---|---|---|
| **能否只打模拟盘** | **不能**:Firstrade 无 paper 产品,库无账户类型参数,`account` 仅取自真实账户列表 | operator 在 Firstrade 网页**模拟盘**手动登入,agent 只在模拟盘页面操作 | 🔴 **决定性**:库=真金,浏览器=模拟盘 |
| 传输 | `requests` 直打 `api3x.firstrade.com`(移动 App 私有 API,User-Agent `okhttp/4.9.2`) | Playwright 驱动真 Chrome 走网页 | 库绕开浏览器 2192 风控,但见下"API 层风控" |
| 凭据姿态 | 用户名/密码/2FA **必须进 Python 进程**(程序化登录) | 凭据**从不进 Python 进程**(operator 手动登) | 🟡 库是实质**倒退** |
| 下单回执 | 结构化 `order_id`/状态(JSON) | 抓页面文本(脆,ledger r3 设计了可空降级) | 🟢 库更硬(但仅在情况 A 下才用得上) |
| 账户/持仓/成交查询 | API 齐全(见 §4),对账够用 | 抓持仓页/历史页文本 | 🟢 库更干净(同上,前提不成立) |
| 维护风险 | 逆向私有 API,"functionality may change at any time";有 endpoint 被上游改掉的历史 | 选择器随网页改版漂移(已建核验闸) | 两边都有上游漂移风险,性质不同 |
| 反检测 | 无浏览器指纹;但 HTTP 层可能有设备指纹/登录频率风控(未实测) | 一整套去 automation 标志/养 profile | 库免去浏览器反检测,但风控可能换面 |

## 2. 五点调研详录

### ① 登录机制(`firstrade/account.py` `FTSession`)
- **流程**:`session.headers` 注入固定 `access-token`(`urls.access_token()` 返回硬编码 `"833w3XuIFycv18ybi"`——
  这是**移动 App 级别的应用 token,非每用户密钥**,配合 `User-Agent: okhttp/4.9.2` 冒充 Firstrade 安卓 App)。
  POST `username`/`password` → `https://api3x.firstrade.com/sess/login` → 返回 JSON 含 `ftat`/`sid`/`mfa`/`otp`。
- **session+cookie**:`save_session=True` 时把 `ftat` token 存 `ft_cookies<username>.json`(明文 JSON,**本质是登录态,等同红线 5 的 cookie**)。
  `get_tokens()`/`build_session_from_tokens()` 支持外部传入 token 重建会话(可避开重复输密码,但 token 仍是敏感登录态)。
- **2FA(`_handle_mfa`,全支持)**:三条路径——
  (a) **PIN**:`verify_pin`;(b) **邮箱/SMS OTP**:`request_code` 发码 → `login_two(code)` 交互式输入;
  (c) **TOTP secret**:`pyotp.TOTP(mfa_secret).now()` 程序化生成(**最适合无人值守,但要求把 2FA 种子落进程/配置——新攻击面**)。
- **自动化检测**:走 `requests` 非浏览器,**无 webdriver/浏览器指纹**,理论上绕开 %Exec 说的 2192 类浏览器拒登。
  但 HTTP 层仍可能有**设备指纹/登录频率/App token 失效**风控(本调研无法只读证实,需实测——但因情况 B 否决,不必再测)。

### ② PAPER_ONLY 可行性(**头号项**)—— 情况 B
- **账户选择**(`FTAccountData`):`account_list()` 拉取**登录用户的真实账户**,`place_order(account=...)` 的 `account`
  只是这个真实账户号字符串。**全库无 `paper`/`demo`/`simulated`/`sandbox`/`practice` 任何枚举或参数**
  (源码 + README + issues 全文 grep 零命中)。`get_balance_overview` 里出现的 `margin`/`cash` 只是**余额字段名过滤**,
  非账户类型选择。
- **平台事实**:Firstrade **不提供 paper trading / demo 账户**(多家券商评测一致:BrokerChooser、TopRatedFirms、StockBrokers 等)。
  → 库里没有模拟盘账户号可供 §2.1-A 那样"下单前精确断言白名单"。
- **`dry_run` 不是隔离闸**(`order.py` `place_order(dry_run=True)` 默认):它只是把 `preview="true"` POST 到**真实**
  `/private/stock_order` 做预览校验、不发第二次 `preview=false`+`stage=P` 的真正提交。即 dry_run=True 不会成交,
  **但它打的仍是真实账户 endpoint**,且"成交与否"取决于调用方是否传 `dry_run=False`——
  这正是 %Exec 警告的"靠易错 flag 隔离"(情况 C)的更糟版本:连 flag 隔离的是真账户,**根本没有假账户存在**。
- **结论**:**情况 B**。不是 A(无模拟账户号)、也不是可救的 C(无任何 paper 维度可硬钉)。库路线 PAPER_ONLY 否决。

### ③ API 完备度(对账用,**仅在情况 A 下才有意义,此处供完整性**)
齐全:下单 `place_order`/`place_option_order`(market/limit/stop/stop-limit/trailing,B/S/SS/BC,DAY/EXT/OVERNIGHT/GT90);
查持仓 `get_positions`;查余额 `get_account_balances`/`get_balance_overview`;查订单 `get_orders(per_page)`;
撤单 `cancel_order`;账户历史 `get_account_history(range, custom_range)`;行情 `quote`/`ohlc`;期权链/Greeks;watchlist 增删查。
**对账三件套(下单/持仓/成交历史)API 上确实齐全**——但这只说明"若有模拟盘则够用",不改变情况 B 否决。

### ④ 凭据安全(逐行核流向)
- **登录请求 target 唯一**:全库 25 个 URL **全部** `https://api3x.firstrade.com`(grep 去重确认);
  用户名/密码只 POST 到 `sess/login`,2FA 码只到 `sess/verify_pin`/`sess/request_code`。
  **无任何第三方域名、无 `subprocess`/`eval`/`socket`/裸 `urlopen` 侧信道**(grep 确认),`_request` 是唯一出口、统一走 `session`。
- **泄漏面**:`debug=True` 会把完整 HTTP 请求/响应打进日志(源码 docstring 自己警告 "DO NOT POST YOUR LOGS ONLINE")——
  日志含 token,需在我方封装里**强制 debug=False + audit_log 凭据字段名硬拒扩到库参数名**(承接 %Exec §2.2)。
- **净评**:源码本身**不外泄**凭据(只发 firstrade.com),但"凭据进 Python 进程 + token 落明文 json + 2FA 种子落配置"
  相对浏览器"零接触"是**实质姿态倒退**,需 operator 决策 + 密钥管理方案(承接 %Exec 缺口 2.2)。

### ⑤ 维护活跃度 + 破坏性风险
- **活跃**:118★/36 fork/7 open issues,最后 push 2026-05-30(约 2 周前);2023-09 起 186 commits、28 个 PyPI 版本,
  近期节奏密(0.0.35→0.0.39 集中在 2026-02~05)。依赖极简:`requests` + `pyotp`。
- **破坏性风险真实**:README 明示"reverse-engineered, not official, **functionality may change at any time**";
  history 有上游改接口逼库跟改的实例(2026-02-05:`PRE_MARKET&AFTER_MARKET` 被 Firstrade 后台改成 `OVERNIGHT`,库被动改)。
  即**Firstrade 后台变更会直接打断库**,需持续追平——逆向私有 API 的固有脆性。

## 3. 给 Lead 的净结论与建议

1. **PAPER_ONLY 否决库路线(情况 B)**:Firstrade 无模拟盘产品 → 库无模拟账户 → 下单即真金 → 违反红线 2。
   **建议:不引入该库到执行/下单链路**;连为执行而 import 都不做(dual-use 红线灰区)。
2. **浏览器模拟盘路线保留为唯一可行路线**:它是当前唯一已知能同时保证"只碰模拟盘 + 凭据零接触"的方案。
   %Exec 的浏览器代码 + 129 测**继续冻结但不拆除**。
3. **若 operator 仍想要库的工程优点**(结构化回执/免浏览器稳定性),唯一正当用途是**只读对账**:
   用 `get_positions`/`get_orders`/`get_account_history` 旁路核对——但**这要求登入真实账户**,
   同样触发凭据进程化倒退(§2.4)且与"模拟盘"无关(真账户没有我们的模拟仓),**实际价值存疑,默认不做**。
4. **可无条件复用的**(与本否决无关,承接 %Exec §3.1):`safety.py` PAPER_ONLY 硬钉 + 真金环境黑名单、kill-switch
   文件机制、audit_log——这四块无论哪条路线都留着。

## 4. 不动声明 / 调研边界
- 本调研**未运行**该库任何登录/下单代码,未装入项目依赖(仅 `/tmp` 临时克隆,已可删)。
- 结论"Firstrade 无 paper 产品"依据公开券商评测(2025–2026),非官方一手确认;**若 operator 有内部渠道证实 Firstrade
  确有 API 可达的模拟盘账户,则回到情况 A 重评**——但现有公开证据一致指向"无"。
- 本文含数据/平台结论,**未经独立复核**(审核制 2026-06-10 已废止);事实可经源码行号 + 公开链接复核。

### 来源
- 源码:`MaxxRK/firstrade-api`(GitHub)`firstrade/{account,order,urls,exceptions}.py`、README、`/test.py`
- 元数据:GitHub API repo stats、PyPI `firstrade` releases
- 平台事实:[BrokerChooser — Firstrade demo account](https://brokerchooser.com/broker-reviews/firstrade-review/firstrade-demo-account)、[TopRatedFirms — Firstrade paper trading](https://topratedfirms.com/trading/virtual/firstrade-paper-trading.aspx)、[StockBrokers — paper trading guide](https://www.stockbrokers.com/guides/paper-trading)
