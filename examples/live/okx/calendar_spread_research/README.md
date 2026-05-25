# OKX 期权日历价差研究

本目录是 OKX 期权日历价差工作的长期计划、证据和策略代码归档入口。这里记录的是研究路线和 gate 证据，不是生产交易授权。

Phase 0 策略代码已经冻结为 `strategies/phase0_v0_flow_validation.py`：它用于验证 OKX 实时行情、Nautilus option-chain discovery、以及 Nautilus sandbox 执行生命周期。盈利逻辑不得继续堆到 Phase 0。

上层 `../okx_option_calendar_spread*.py` 文件只作为兼容入口。日历价差策略实现都放在 `strategies/` 下，并按 Phase / version 保留。

## 安全边界

- 允许连接 OKX live 读取行情和发现合约。
- 研究主线禁止真实账户下单。
- 当前 Phase 2 状态禁止默认进入 sandbox execution；显式 `--enable-execution --no-dry-run` 只允许接入 Nautilus 本地 sandbox，不向 OKX 账户下单。
- 任何 Phase 3/4/5 执行、风控、准生产路径都必须先有 artifact-backed Phase 3 entry gate 通过证明。
- 当前执行边界仍是 `execution_enabled=false`、`sandbox_execution=false`、`real_orders=false`、`sandbox_fill_rows=0`。

## 文件结构

- `roadmap.md`：原始 Phase 0-5 路线、promotion gate、当前状态。
- `versions/`：每个阶段的设计记录和证据记录。
- `strategies/`：按阶段保存的策略代码版本，以及与源码对齐的策略逻辑文档。
- `tools/`：本地证据分析工具，生成 JSON/Markdown artifact。后续新增 Markdown 输出必须使用中文叙述。

## 策略代码版本矩阵

| 阶段 | 策略代码归属 | 当前状态 |
| --- | --- | --- |
| Phase 0 | `strategies/phase0_v0_flow_validation.py` | 已接受为 flow-validation harness，冻结盈利逻辑 |
| Phase 1 | `strategies/phase1_v0_selective_l2_execution_audit.py` | data-only execution audit 已推进；当前仍不是 sandbox-execution ready |
| Phase 2 | `strategies/phase2_v0_shadow_signal_research.py`、`strategies/phase2_v1_shadow_signal_research.py` | 已从 baseline signal gate 调整为交易机会模型研究；v1 默认 no-order，但显式执行开关可走 Nautilus 本地 sandbox；research-complete 已 close out 为 no-promotion，Phase 3 entry gate 仍失败 |
| Phase 3 | `strategies/phase3_v0_execution_planner.py` | 未进入，被 Phase 2 gate 阻止 |
| Phase 4 | `strategies/phase4_v0_risk_recovery.py` | 未进入，被 Phase 2 gate 阻止 |
| Phase 5 | `strategies/phase5_v0_staged_near_production.py` | 未进入，被 Phase 2 gate 阻止 |

每个 Phase 至少一个、最多两个策略版本。如果某个 Phase 需要第三个策略模块，说明边界过大，应该拆分或进入下一阶段，而不是继续隐藏扩张。

## 当前路线状态

Phase 0 已完成/基本完成，不要重跑。接受证据是 GTC stop-after-FLAT 2h validation：144 个 BTC+ETH cycle 全部完成，BTC=59、ETH=85，unclosed=0，close-only=0，unmatched fills=0，无 `_UM`、无 contextual `60018`、无 `InvalidStateTrigger`、无 sandbox matching-engine error。该阶段证明的是生命周期、账户、持仓、PnL 报告和 FLAT stop gate；它不证明盈利能力，也不证明真实交易所执行质量。

Phase 1 已形成 selective L2 / execution audit 的 data-only baseline。当前关键结论：订阅上限可以被控制，`EXECUTION_AUDIT` 和 analyzer 能区分 fresh L2、delayed-depth warning、cap-blocked、not-selected、stale/missing depth 等状态；但 latest no-order summary 仍是 `diagnostic_only_not_sandbox_execution_ready`。阻塞点包括 full-fresh two-leg event coverage 过低、selected delayed-depth warning ratio 过高、cap-blocked events 多于 full-fresh executable events。

Phase 2 已起步并完成多轮 no-order shadow 证据：

- `phase2_v1_live_shadow_fairctx_posterior5m_20260524T042057Z` 证明 live fair-value context 与 close-side posterior plumbing 可运行，但只得到 1m/5m 短窗口负样本。
- `phase2_v1_live_shadow_posterior1h_20260524T065244Z` 是 bounded no-order wider-posterior smoke，运行 4800 秒、zero sandbox fill、zero errors，但 30m/1h posterior 仍全部缺失。
- `phase2_bucket_evidence_phase2_v1_live_shadow_posterior1h_20260524T065244Z.{json,md}` 显示 3515 events、58 buckets、353/353 expected-edge samples 为负、23/23 observed posterior samples 为负。
- `phase2_gate_review_phase2_v1_live_shadow_posterior1h_20260524T065244Z.{json,md}` 明确 `phase2_passes=false`、`phase3_entry_allowed=false`。
- `phase2_gate_refinement_plan_phase2_v1_live_shadow_posterior1h_20260524T100053Z.{json,md}` 给出当前最小补证路径：非代理 fair-value replay、L2/delayed-depth calibration、必要时再做 no-order posterior coverage。
- `phase2_l2_calibration_phase2_v1_live_shadow_posterior1h_20260524T100053Z.{json,md}` 再次确认 Phase 1/2 仍只能 data-only。
- `phase2_fair_value_replay_bs_v0_phase2_v1_live_shadow_posterior1h_20260524T100053Z.{json,md}` 使用 `offline_black_scholes_iv_term_structure_v0` 替代 L1-mid proxy；结果是 353 个 computed-cost replay event 中 352 个为负，只有 1 个 ETH PUT 极小正值 `0.000008363659780045`，且 bucket 不稳定、observed posterior 仍为负。
- `phase2_fair_value_robustness_bs_v0_phase2_v1_live_shadow_posterior1h_20260524T100053Z.{json,md}` 对这个唯一极小正样本做离线扰动审计；结果为 base 正样本 1 个、fragile 正样本 1 个、所有场景都没有 stable positive bucket，结论是 `not_promotable_positive_sample_is_not_robust`。
- `phase2_posterior_coverage_gap_phase2_v1_live_shadow_posterior1h_20260524T100053Z.{json,md}` 将当前 tag 的 posterior 缺口独立成专用补证报告：714 个 included entries 中 30m/1h 完全缺失，已观测 23 个短窗口 edge 全为负，结论是 `not_promotable_long_windows_missing_and_no_positive_signal`。
- `phase2_research_registry_v0_20260524T120709Z.{json,md}` 将 Phase 2 调整为交易机会模型研究框架，拆分 `phase2_research_complete` 与 `phase3_entry_gate`，并把模型比较、L2 policy/signal overlap 实验写入统一实验账本：当前研究完成度为 `in_progress`，Phase 3 entry 仍为 `blocked`。
- `phase2_model_comparison_iv_gap_dte_v0_20260524T115529Z.{json,md}` 完成 registry 后第一个离线模型比较实验，按 IV gap、DTE、delta/moneyness、event window 和 L2 freshness 分层比较 5 个模型；353 个 replayable events 中仍是 352 负、1 个极小正值，23 个已观测 posterior edge 全为负，没有稳定正值模型，也没有非脆弱正候选。
- `phase2_l2_policy_signal_overlap_v0_20260524T120603Z.{json,md}` 交叉审计 fresh-L2、delayed-depth、cap-blocked、stale/warming 与 fair-value replay edge：353 个可 replay edge 仍只给出 352 负、1 个极小正值；478 个 delayed-depth warning、2751 个 cap-blocked、71 个 stale-depth 事件均没有可执行成本 edge。
- `phase2_research_closeout_no_promotion_v0_20260524T131500Z.{json,md}` 回到 Phase 2 主线，汇总 5 个注册模型的 replay/overlap 证据和淘汰原因；结论为 `phase2_research_complete=true`、`phase2_promotion_proven=false`、`phase3_entry_allowed=false`。
- `phase2_research_registry_v0_20260524T131600Z.{json,md}` 将 closeout 写回统一 ledger：Phase 2 主线模型研究已完成为 no-promotion，Phase 3 gate 仍为 blocked。

因此当前 Phase 2 子状态是：no-promotion 后的 data-only 交易机会模型研究 closeout。它不是 Phase 0 重新验证阶段，也不是 Phase 3 execution planner 阶段。当前证据只说明 baseline 机械筛选和现有模型尚未证明可 promotion，不说明 calendar spread 研究方向已经失败。

## Phase 2 双 gate

- `phase2_research_complete`：判断研究框架是否成型。要求至少 3-5 个交易机会模型进入 registry，每个模型有经济假设、输入特征、entry/reject 条件、成本模型、鲁棒性测试，并用统一实验账本记录样本数、结果和失败原因。
- `phase3_entry_gate`：判断是否允许提出或进入 Phase 3。要求至少一个模型在可执行成本后有非脆弱正候选，并通过额外成本缓冲、fair-value 折扣、fresh-L2-only、delayed-depth 排除和目标 posterior coverage 支持。
- 当前状态：`phase2_research_complete=complete_no_promotion`，`phase3_entry_allowed=false`。

## 当前 Phase 3 硬阻塞

- 没有稳定正 edge bucket。
- 可执行预期 edge 与已观测 posterior edge 当前没有非脆弱正候选，非代理 fair-value replay 也没有稳定正 bucket。
- 30m/1h posterior coverage 已由专用 gap artifact 证明全缺失；这是 gate 失败项，不是 promotion 例外。
- Phase 1 selective L2 仍不是 sandbox-execution ready。
- fair-value 模型虽然已从 L1-mid proxy 进入离线 Black-Scholes replay，但唯一正样本极小且未通过 robustness/false-positive 审计。

## 下一步原则

下一步只能做本地、只读或 shadow-safe 的 Phase 2 补证。当前主线 closeout 已完成，优先顺序：

1. 已运行 `phase2_model_replay_iv_gap_dte_v0`，产物为 `phase2_model_comparison_iv_gap_dte_v0_20260524T115529Z.{json,md}`；结论是不放行 Phase 3。
2. 已运行 `phase2_l2_policy_signal_overlap_v0`，产物为 `phase2_l2_policy_signal_overlap_v0_20260524T120603Z.{json,md}`；结论是不放行 Phase 3。
3. 已运行 `phase2_research_closeout_no_promotion_v0`，产物为 `phase2_research_closeout_no_promotion_v0_20260524T131500Z.{json,md}`；结论是 Phase 2 主线 closeout 完成但不 promotion。
4. 若继续 Phase 2，只能先定义新的交易机会假设或外部特征，不应把 catalog replay 设施优化当作当前主线。
5. posterior coverage 只在离线实验产生非脆弱正候选后再进入新的 no-order live shadow；当前 gap artifact 只证明阻塞, 不证明值得长跑。

不要直接写 Phase 3 execution planner。不要真实订单、不要默认 sandbox execution；如显式请求 Phase 2 sandbox execution，只能使用 Nautilus 本地 sandbox 并保留 gate override 证据。
