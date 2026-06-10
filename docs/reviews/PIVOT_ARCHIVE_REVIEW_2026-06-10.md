# 转向手术对照核查记录(机动/Review,2026-06-10)

> 核查人:%Review(quant:0.0,原 Audit)。性质:Lead 指派的专项对照核查,非审计裁决(审核制度已废止)。
> 任务:归档范围 vs operator 授权范围一致性 / 无夹带 / 可逆性。

## 一、对照基准
- **授权出处**:`team/PROGRESS_LOG.md`「转向手术完成」条目——operator 原话"不再搞虚拟货币…按博主喊单建议…把之前繁琐的回测等内容删了 但要做下单留档…当天下单依据帖子";删除方式 A(移 archive/ 保留历史)经 Lead 提案、operator 回复"我同意"。
- **核查范围**:`d293eff`(转向归档 130 文件)+ `80686b3`(整理批次,含策略归档 114 rename)。同条目提及的 `ce97294`(依赖瘦身)与宪法改写不在本次指派范围,未核。

## 二、核查结果:**一致 / 无夹带 / 可逆**

### 1. 范围一致性 ✅
`d293eff` 实际归档(128 个 rename,逐条清点):
- src 十包(analysis/backtest/data_quality/exchange/factors/ingestion/replay/risk/strategies/validation,68 文件)→ archive/src/
- experiments 3 + registry 1 + crypto 配置 11 → archive/
- 研究脚本 15 → archive/scripts/;研究 dashboard 页 9 → dashboard/legacy_pages/;对应测试 21 → archive/tests/

与 commit 自述逐项吻合(15 脚本/9 页/十包均与申报数一致);全部属于授权语义"回测等(研究层)内容"+crypto 遗留。**128/128 目标路径全部落在 archive/ 或 dashboard/legacy_pages/**,无移往他处(注:9 个中文页名在 git 输出中带引号转义,已逐条人工确认目标均为 legacy_pages)。

**下单留档能力未受损**:保留集逐项在位——src/{storage,news,notify,research,signals,logging_setup} + scripts/{medic,tsay,restart_team,replay_copytrade_rules_v0} + config/settings.py + docs/ORDER_LEDGER_SPEC.md;归档清单中零涉 ORDER_LEDGER/signals/storage。

### 2. 无夹带 ✅
- `d293eff` 全部 rename 为 **R100(内容零改动)**;仅 2 个 M:Makefile(删 verify-okx-legacy/quality 目标、typecheck 收编为存活五层)、tests/scripts/test_importable.py(冒烟清单缩编+docstring 更新)——两者均在 commit 自述内,diff 逐行核过无超界改动。
- frozen 策略基线(minute_swing 等)系 R100 原样移入 archive/,内容未动,符合"归档不复活"纪律;红线 3 的"不改"在归档语境下未被违反(操作=授权下的移动,非改参)。
- `80686b3` 已于废止前完成全量核查(114 rename 全 R100、18M+3D+7A 全部申报内、红线扫描净、零 blocker),本次复点 rename 类型无非 R100 条目,结论沿用。

### 3. 可逆性 ✅
- `d293eff` **零 D(删除)条目**,纯 git mv + 2 处适配;`git revert` 或反向 mv 即可整体还原,git 历史完整保留(方式 A 承诺兑现)。
- `80686b3` 的 3 个 D 为运行时产物(pid/log),不影响代码可逆性。

### 4. 旁证
当前树 `uv run pytest -q` = **85 passed / 0 failed**(高于 commit 时 52,系成员分支后续合入新增测试),`ruff check src/ tests/` 干净;存活层无断裂迹象。

## 三、备注(不构成异议)
1. ce97294(依赖瘦身 -ccxt/-vectorbt 等)建议后续顺手做一次"被删依赖 vs 存活 import"对照,确认无潜伏断裂(news 的 feedparser 修复已在该 commit 自述)。
2. dashboard/legacy_pages/ 不在 archive/ 树下,属第二归档位;建议 MIGRATION_MANIFEST 或 README 一句话注明,防未来误认为现役页面。
