# Firstrade 专用 Chrome Profile 接管(P2 登录路线)

> 背景:Firstrade 风控拦截全新空 profile(`Reference code 2192`),且 Playwright 默认的
> 自动化指纹(`--enable-automation`/`--no-sandbox`/`navigator.webdriver`)会被识别。
> 实测只有**养熟的信任 profile**能稳定登录。故路线 = 用真实 Chrome 在一个**专用隔离 profile**
> 里手动登一次养熟,Playwright 再接管该 profile。

## 安全约束(Lead 已定,不可削弱)

- **绝不接管 operator 的主 Chrome profile**——那会把其全部登录态暴露给无人值守自动化(P3 风险)。
- 用**专用隔离 profile**(默认 `.auth/chrome_profile/`,gitignored),只登 Firstrade 一个站。
- **凭据零接触**:养熟那一步 operator 在真 Chrome 里手动输入,不经任何自动化代码;
  Playwright 侧对凭据字段硬拒(`type_human` 代码层保证),只复用养熟后的 profile。

## 三步走

### 步骤①:建专用 profile + 手动登(operator,在真实 Chrome 里)

```bash
make exec-warm-profile
```

它用专用 profile 目录打开一个**真实 Chrome**(非 Playwright)。在这个 Chrome 窗口里:

1. 访问 Firstrade,**人工**登录(账号/密码/2FA 全手动);
2. 勾选 **"记住此设备 / Remember this device"**(养熟的关键——下次免二次验证);
3. 进到模拟盘账户页确认登录成功;
4. **⌘Q 完全退出 Chrome**(不是关窗口——必须退进程,否则 profile 被锁,步骤②会报占用)。

> 等价手动命令(make 不可用时):
> `open -na "Google Chrome" --args --user-data-dir="<repo>/.auth/chrome_profile"`

### 步骤②:Playwright 接管(operator 跑,我在旁)

```bash
make exec-login
```

Playwright 用 `channel=chrome` + 反检测配置接管同一个养熟 profile:

- 启动前**自动检测 profile 是否被占用**:若步骤①的 Chrome 没退干净,会明确报
  `专用 Chrome profile 正被进程 <pid> 占用…请先 ⌘Q 退出`——照做再重试;
- 接管后通常已带登录态(profile 已养熟),直接回车;若登录态过期则在此窗口补登一次;
- 反检测已就位:无 `--enable-automation`、无 `--no-sandbox` 警告条、`navigator.webdriver=undefined`、
  真 Chrome 内核与 UA。

### 步骤③:选择器核验交接(operator + 我)

登录态确认后,按 `docs/SELECTOR_VERIFICATION_CHECKLIST.md` 走:operator 翻页,我跑只读探针
+ DevTools 三关,逐项标 `verified:true`。

## 故障对照

| 现象 | 处理 |
|---|---|
| `Reference code 2192` / 验证失败 | profile 没养熟或被风控盯上;确认步骤①勾了"记住设备"且登录成功再退出。**只重试一次新验证码,再失败先停**(红线 6,别触发账号锁) |
| `专用 profile 正被进程 N 占用` | 步骤①的 Chrome 没退干净 → ⌘Q 完全退出(Activity Monitor 确认无 Google Chrome 进程)再跑 |
| 想从头重来 | `rm -rf .auth/chrome_profile`(清空养熟 profile)后重走步骤① |
| 排障对照(确认是不是反检测导致) | `EXEC_STEALTH=0 make exec-login` 关掉反检测对比;`EXEC_CHROME_PROFILE_DIR=""` 回退一次性模式 |

## 配置开关(`.env`,前缀 `EXEC_`)

| 变量 | 默认 | 说明 |
|---|---|---|
| `EXEC_CHROME_PROFILE_DIR` | `.auth/chrome_profile` | 专用 profile 目录;**绝不指主 profile**;留空回退 storage_state 一次性模式 |
| `EXEC_STEALTH` | `1` | 反检测开关(去自动化标志+抹 webdriver);`0` 仅排障对照 |
| `EXEC_HEADLESS` | `0` | 登录/核验必须有头(operator 要看页面) |
