# Phase 2 `v0_shadow_signal_research`

## 目标

构建交易机会模型研究框架，用经济假设替代机械开平仓，并持续检验模型是否能在 executable costs 后形成非脆弱正候选。当前阶段只允许 shadow/data-only 证据，不允许真实订单，也不允许默认进入 sandbox execution。

## 策略代码

- 主模块：`../strategies/phase2_v0_shadow_signal_research.py`。
- live-shadow posterior collector：`../strategies/phase2_v1_live_shadow_posterior_collector.py`。
- 不得把盈利逻辑写回 Phase 0 或 Phase 1 模块。

## 设计

- 输入：IV term structure、IV gap、DTE pair、theta/vega/gamma proxy、moneyness、delta bucket、quote age、spread、event window、liquidity。
- 输出：`expected_edge_after_costs`。
- 状态：shadow-only、candidate、tradable/executable、blocked。
- posterior 观察窗口：1m、5m、30m、1h。
- Phase 1 policy label 是强制输入：`execution_realism_candidate` 可进入 `candidate`；`shadow_only_delayed_depth_warning` 只能进入 `shadow_only_warning`；其它政策保持 `blocked`。
- mid/mark-only edge 明确忽略，不得用于 promotion。

## 双 gate

- `phase2_research_complete`：研究框架是否成型。要求 3-5 个模型进入 registry，每个模型有经济假设、输入特征、entry/reject 条件和鲁棒性测试，并由统一实验账本跟踪。
- `phase3_entry_gate`：是否允许提出或进入执行规划器。要求至少一个模型在可执行成本后有非脆弱正候选，且通过成本缓冲、fair-value 折扣、fresh-L2-only、delayed-depth 排除和 posterior coverage 支持。
- 当前状态：`phase2_research_complete=in_progress`，`phase3_entry_allowed=false`。这表示尚未证明可 promotion，不表示 calendar spread 方向已失败。

## 当前证据

- 第一阶段 input artifact：`../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_shadow_inputs_from_complete_baskets_20260524T021125Z.json`，590 events，227 shadow inputs，363 blocked，79 fresh candidate，148 delayed-depth warning。
- strict fair-value fixture：`phase2_shadow_inputs_fixture_fair_value_20260524T021125Z.json` 只证明 CLI/schema path，不是研究信号。
- posterior skeleton / gap audit 已证明当前历史数据缺 close-side executable exit values，不是 schema 问题。
- `strategies/phase2_v1_live_shadow_posterior_collector.py` 是 Phase 2 live-shadow/posterior collector，已能收集 close-side `posterior_<window>` audit row，并在 Greeks 可用时写入 `l1_mid_calendar_proxy_not_edge_model` fair-value context。该 proxy 只能作为诊断上下文；该策略默认 no-order，但显式 `--enable-execution --no-dry-run` 允许 Nautilus 本地 sandbox execution 测试。

最新 no-order evidence：

- `../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_v1_live_shadow_posterior1h_20260524T065244Z.log`
- `../../../../.omx/artifacts/dynamic-calendar-pnl/execution_audit_summary_phase2_v1_live_shadow_posterior1h_20260524T065244Z.json`
- `../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_shadow_inputs_with_phase2_v1_live_shadow_posterior1h_20260524T065244Z.json`

结果：

- 3515 audit events，zero sandbox fills，zero errors。
- Phase 1 verdict 仍是 `diagnostic_only_not_sandbox_execution_ready`。
- 3515 Phase 2 events，714 shadow inputs，2801 blocked。
- posterior coverage 只改善短窗口：1m observed=16、5m observed=7，30m/1h 全部 missing。

Bucket evidence：

- `../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_bucket_evidence_phase2_v1_live_shadow_posterior1h_20260524T065244Z.{json,md}`
- 3515 events，58 buckets。
- 353/353 computed expected-edge samples 为负，范围 `-0.00250` 到 `-0.00005`。
- 23/23 observed posterior samples 为负，范围 `-0.0020` 到 `-0.0003`。
- 无 positive expected-edge bucket，无 positive observed-edge bucket。

Gate review artifact：

- `../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_gate_review_phase2_v1_live_shadow_posterior1h_20260524T065244Z.{json,md}`
- `phase2_passes=false`
- `phase3_entry_allowed=false`

Gate refinement artifact：

- `../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_gate_refinement_plan_phase2_v1_live_shadow_posterior1h_20260524T100053Z.{json,md}`
- 当前推荐：先做离线 strict non-proxy fair-value replay，再做 selective L2 / delayed-depth calibration review；只有 artifact 证明有候选理由后才考虑 posterior coverage。

L2 calibration：

- `../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_l2_calibration_phase2_v1_live_shadow_posterior1h_20260524T100053Z.{json,md}`
- 结论：full-fresh ratio 过低、selected delayed-depth ratio 过高、cap-blocked events 过多；继续 data-only。

Fair-value replay：

- `../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_fair_value_replay_bs_v0_phase2_v1_live_shadow_posterior1h_20260524T100053Z.{json,md}`
- strict map：`../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_fair_value_map_bs_v0_phase2_v1_live_shadow_posterior1h_20260524T100053Z.json`
- source：`offline_black_scholes_iv_term_structure_v0`
- 353 replay events 中 352 负，1 个 ETH PUT 极小正值 `0.000008363659780045`。
- zero stable positive replayed buckets，observed posterior samples 仍为负。
- verdict：`not_promotable_no_stable_positive_bucket`。

Fair-value robustness：

- `../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_fair_value_robustness_bs_v0_phase2_v1_live_shadow_posterior1h_20260524T100053Z.{json,md}`
- 读取同一 Phase 2 shadow artifact 与 strict fair-value map，离线测试 extra cost buffer 和 fair-value haircut。
- 结果：base positive event count 为 1，fragile positive event count 为 1；`extra_cost_0_00001` 已足以让唯一正样本转负。
- 所有场景都没有 stable positive bucket，verdict 为 `not_promotable_positive_sample_is_not_robust`。

Posterior coverage gap：

- `../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_posterior_coverage_gap_phase2_v1_live_shadow_posterior1h_20260524T100053Z.{json,md}`
- 将当前 no-promotion tag 的 posterior coverage 缺口独立成专用 artifact。
- 结果：714 个 included entries，30m/1h 完全缺失，23 个 observed short-window edge 全为负。
- gate 解释为 `not_promotable_long_windows_missing_and_no_positive_signal`；这补强阻塞证据，不放行 Phase 3。

Research registry：

- `../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_research_registry_v0_20260524T120709Z.{json,md}`
- 建立 5 个初始交易机会模型：`iv_gap_theta_carry_v0`、`atm_short_dte_theta_v0`、`delta_bucket_term_slope_v0`、`event_filtered_calendar_v0`、`liquidity_adjusted_iv_gap_v0`。
- 建立统一 experiment ledger，明确 baseline shadow、bucket evidence、fair-value replay、robustness、posterior gap、model comparison 和 L2 policy/signal overlap 均未构成 Phase 3 entry 证据。
- 结论：Phase 2 方向未被判死刑；当前进入交易机会模型研究框架推进，但 Phase 3 entry gate 仍阻塞。

模型比较：

- `../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_model_comparison_iv_gap_dte_v0_20260524T115529Z.{json,md}`
- 完成 `phase2_model_replay_iv_gap_dte_v0`，按 IV gap、DTE、delta/moneyness、event window 和 L2 freshness 分层比较 5 个模型。
- 结果：353 个 replayable events 中 352 个 replay edge 为负、1 个极小正值；23 个已观测 posterior edge 全为负。
- 所有模型 `stable_positive_after_costs=false`，`non_fragile_positive_candidate=false`。
- 结论：该 artifact 推进 Phase 2 research-complete 证据，但不构成 Phase 3 entry 证据。

L2 policy/signal overlap：

- `../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_l2_policy_signal_overlap_v0_20260524T120603Z.{json,md}`
- 交叉审计 fresh-L2、delayed-depth、cap-blocked、stale/warming 与 fair-value replay edge 的重叠关系。
- 结果：353 个可 replay edge 中 352 个为负、1 个极小正值；23 个已观测 posterior edge 全为负。
- delayed-depth warning 478 个、cap-blocked 2751 个、stale-depth 71 个，均没有可执行成本 edge。
- 结论：fresh L2 本身没有非脆弱正候选；delayed-depth/cap-blocked/stale 只能解释数据质量或订阅上限风险，不能作为 Phase 3 entry 证据。

## 门槛

- Research-complete 门槛：registry、ledger、模型 replay/comparison、L2 policy/signal overlap 和 no-promotion closeout 必须可复核。
- Phase 3 entry 门槛：positive buckets 必须在成本扣除后仍为正；negative samples / false positives 必须被理解；不能依赖 mid-only 或 mark-only profit；bucket 经过 Phase 1 executable-price 和 transport-quality filter 后仍必须为正。
- 当前状态：Phase 2 research-complete 已 close out 为 no-promotion，Phase 3 被阻止。唯一非代理 fair-value 正样本已被 robustness 审计判定为 fragile，30m/1h posterior coverage 也由 gap artifact 证明全缺失，不构成 promotion 证据。
