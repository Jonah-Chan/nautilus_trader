# OKX 期权日历价差路线图

## 总边界

这条路线的目标是逐步接近可运营、可审计的研究系统，但不授权真实交易所下单。当前主线仍处于 Phase 2 no-promotion 状态；Phase 3/4/5 尚未进入。

全局原则：

- 真实 OKX 连接只用于行情和合约发现。
- sandbox 执行只能在 gate 明确允许后作为受控证据层使用；当前不允许默认启用。
- 真实账户执行始终不在本研究轨道内。
- 每个 Phase 只能由 artifact 和 gate 结论推进，不能因为路线想完成而跳级。
- Markdown 文档和后续生成的阶段报告使用中文叙述；代码标识、artifact 路径和 JSON 字段名保持原样。

## Promotion 节奏

每个阶段遵循同一节奏：

1. 设计记录：更新对应 `versions/phaseN_*.md`，写明范围、假设和拒绝边界。
2. 单元 fixture：先锁住解析、状态、成本、风险或 gate 语义。
3. 短 smoke：15-30 分钟，BTC+ETH 覆盖，保留 HTML/JSON/Markdown artifact。
4. 验证 run：短 smoke 干净后再做 2-4 小时验证；单次 run 不超过 8 小时。已经产生足够完整 cycle 时，可以用 completed-cycle FLAT gate。
5. Review gate：升级前分类所有 warning/error、残余状态、PnL/account mismatch。
6. Forward-plan review：阶段接受后重新审查后续阶段目标，但不得放松 sandbox-only 和 no-real-order 边界。

阶段不能因为 headline PnL 晋级，只能因为证据证明下一层可以被可靠度量而晋级。

## Phase 0: freeze and complete flow validation

目的：让当前 validation harness 可信。

交付物：

- BTC+ETH live data + Nautilus sandbox execution 生命周期证据。
- 冻结策略模块 `strategies/phase0_v0_flow_validation.py`。
- 按 open-close cycle 归因的 PnL 报告。
- `AccountBalance` delta、`PositionClosed.realized_pnl`、fees、unclosed state、strategy state 分离展示。
- 单腿成交、IOC partial/cancel、reduce-only no position、缺失 close quote、restart existing position、subscription recovery 的异常路径测试。
- entry/exit executable price、spread、quote age、lifecycle duration、simulated fill source 可观测字段。
- 新上市合约 activation/listing-age guard。
- `--stop-after-flat-seconds` 和 `--stop-after-completed-baskets`。

门槛：

- BTC 和 ETH 都有 live evidence。
- 接受证据中没有未解释的 matching/sandbox error。
- 每个 cycle 能从 candidate replay 到 final state。
- Loss/profit 能按 cycle、leg、fee、unclosed state 解释。
- 后续 Phase 1-5 记录已按 Phase 0 真实证据校正。

当前状态：已完成/基本完成，不要重跑。

核心证据：

- 接受证据：`../../../../.omx/artifacts/dynamic-calendar-pnl/phase0_btc_eth_gtc_stopflat_2h_20260523T040010Z.log`。
- 报告：`../../../../.omx/artifacts/dynamic-calendar-pnl/pnl_report_phase0_btc_eth_gtc_stopflat_2h.html`。
- summary：`../../../../.omx/artifacts/dynamic-calendar-pnl/pnl_summary_phase0_btc_eth_gtc_stopflat_2h.json`。
- 结果：144 cycles，144 completed，BTC=59，ETH=85，unclosed=0，close-only=0，unmatched fills=0，fills=576，`PositionClosed` events=288。
- 无 contextual `60018`、无 `_UM`、无 `InvalidStateTrigger`、无 `Cannot cancel an order with INITIALIZED`、无 `FAILED_NEEDS_FLATTEN`、无 traceback。
- 6 条 `[ERROR]` 是 websocket/TLS transport reset，策略随后继续完成 cycle 并回到 FLAT；生产级 reconnect policy 归 Phase 4。
- 所有 144 个 cycle 都是负 PnL，原因是机械 bid/ask crossing + sandbox fee；这是 flow harness 的成本基线，不是 Phase 2 signal edge 结论。

## Phase 1: selective market-data realism and execution audit

目的：在不订阅全合约 L2 的前提下加入真实可执行价格估计。

交付物：

- broad scan 继续使用 `OptionChainSlice`、L1 quote、greeks、status。
- 仅对 top candidates 和 active basket legs 订阅 bounded `books5`。
- 计算 L2 VWAP、executable size、slippage estimate、protection price、missing-depth reason。
- 生成 `execution_audit` artifact，比较 signal price、L1 executable price、L2 VWAP，以及未来 sandbox execution sample 的 fill/deviation。
- 明确 sandbox IOC 行为和真实交易所 IOC 假设不同。
- 将 websocket reset / reconnect context 写入 audit。

门槛：

- L2 订阅数保持在配置上限内。
- 每个 candidate 都分类为 theoretical、L1 executable、L2 executable、blocked、stale/delayed/cap-blocked 等明确状态。
- sandbox fills 只能作为 lifecycle evidence，不能单独证明真实交易所 fill quality。
- 进入 sandbox execution sample 前，candidate-level L2 freshness 必须足够稳定。

当前状态：data-only audit baseline 已完成，但仍不是 sandbox-execution ready。

关键证据：

- 当前参考 no-order summary：`../../../../.omx/artifacts/dynamic-calendar-pnl/execution_audit_summary_phase2_v1_live_shadow_posterior1h_20260524T065244Z.json`。
- `sandbox_fill_rows=0`，`errors_total=0`，`http_50011_errors=0`，`max_active_l2_subscriptions=8`，`final_active_l2_subscriptions=0`。
- `phase1_policy_calibration.verdict=diagnostic_only_not_sandbox_execution_ready`。
- event gate 阻塞：full-fresh ratio `0.10497866287339971`，selected delayed-depth ratio `0.3665644171779141`，cap-blocked events `2751` 多于 full-fresh events `369`。
- `tools/analyze_phase2_l2_calibration.py` 生成的 `phase2_l2_calibration_phase2_v1_live_shadow_posterior1h_20260524T100053Z.{json,md}` 再次确认：保持 data-only，继续校准 delayed-depth 与 cap-blocking。

## Phase 2: positive-expectancy signal research

目的：用经济信号逻辑替代机械开平仓，建立可复现、可比较、可淘汰的交易机会模型研究框架；只有模型在 executable costs 后出现非脆弱正候选时，才允许进入 Phase 3 entry gate。

交付物：

- 输入：IV term structure、DTE、moneyness、delta bucket、quote age、spread、event window、liquidity。
- 输出：`expected_edge_after_costs` 和 posterior outcome。
- 状态：shadow-only、candidate、tradable/executable、blocked。
- 观察窗口：1m、5m、30m、1h。
- bucket 需要分离 underlying、settlement currency、expiry pair、strike/moneyness、quote age、spread、disconnect context。
- 严禁依赖 mark/mid-only profit illusion。

Phase 2 research-complete 门槛：

- 至少 3-5 个明确交易机会模型进入 registry。
- 每个模型有经济假设、输入特征、entry/reject 条件、成本模型和鲁棒性测试。
- 每个模型能产出结构化 artifact：`model_id`、input features、entry condition、cost model、expected edge、posterior outcome、rejection reason。
- 统一 experiment ledger 记录每次模型版本、样本数、结果和失败原因。
- 不要求已经有稳定正 bucket，但必须能解释当前样本不足或模型淘汰原因。

Phase 3 entry gate：

- positive bucket 在扣除 executable costs 后仍为正。
- negative samples 和 blocked reasons 已审计。
- 不允许用 L1-mid proxy 当作生产 fair value。
- Phase 1 executable-price / transport-quality filter 后，bucket edge 仍必须为正。
- 30m/1h posterior coverage 不能长期缺失。

当前状态：Phase 2 已完成 no-order shadow、posterior smoke、bucket evidence、gate review、模型比较、L2 policy/signal overlap 和主线 closeout；baseline promotion gate 失败。当前按双 gate 表述为 `phase2_research_complete=complete_no_promotion`、`phase3_entry_allowed=false`。

核心证据：

- No-order wider posterior smoke：`phase2_v1_live_shadow_posterior1h_20260524T065244Z`，运行 4800 秒，`dry_run=True`，`execution_enabled=False`，无 sandbox fills，无 errors。
- Phase 2 replay：3515 events，714 shadow inputs，2801 blocked，369 `candidate`，345 `shadow_only_warning`。
- posterior coverage：1m observed=16，5m observed=7，30m/1h 全部 missing。
- Bucket evidence：`../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_bucket_evidence_phase2_v1_live_shadow_posterior1h_20260524T065244Z.{json,md}`。结果为 353/353 expected-edge samples 负，23/23 observed posterior windows 负。
- Gate review artifact：`../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_gate_review_phase2_v1_live_shadow_posterior1h_20260524T065244Z.{json,md}`。结论为 Phase 2 不通过，Phase 3 不允许进入。
- Gate refinement plan artifact：`../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_gate_refinement_plan_phase2_v1_live_shadow_posterior1h_20260524T100053Z.{json,md}`。推荐离线 fair-value replay -> L2 calibration review -> 必要时 posterior coverage。
- L2 calibration：`../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_l2_calibration_phase2_v1_live_shadow_posterior1h_20260524T100053Z.{json,md}`。当前执行就绪 gate 仍失败。
- Fair-value replay：`../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_fair_value_replay_bs_v0_phase2_v1_live_shadow_posterior1h_20260524T100053Z.{json,md}`。使用 `offline_black_scholes_iv_term_structure_v0`，不使用 L1-mid far-minus-near proxy；353 replay events 中 352 负，1 个极小正值，稳定正值 replay bucket 数为 0。
- Fair-value robustness：`../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_fair_value_robustness_bs_v0_phase2_v1_live_shadow_posterior1h_20260524T100053Z.{json,md}`。唯一 base 正样本也是 fragile 正样本；加入 `0.00001` 额外成本 buffer 即转负，所有扰动场景都没有 stable positive bucket。
- Posterior coverage gap：`../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_posterior_coverage_gap_phase2_v1_live_shadow_posterior1h_20260524T100053Z.{json,md}`。714 个 included posterior entries 中 30m/1h 完全缺失，23 个 observed short-window edge 全为负；结论为 `not_promotable_long_windows_missing_and_no_positive_signal`。
- 研究 registry：`../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_research_registry_v0_20260524T120709Z.{json,md}`。定义 `iv_gap_theta_carry_v0`、`atm_short_dte_theta_v0`、`delta_bucket_term_slope_v0`、`event_filtered_calendar_v0`、`liquidity_adjusted_iv_gap_v0`，并建立统一 experiment ledger 与研究额度；该版本已纳入模型比较和 L2 policy/signal overlap 实验结果。
- 模型比较：`../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_model_comparison_iv_gap_dte_v0_20260524T115529Z.{json,md}`。完成 `phase2_model_replay_iv_gap_dte_v0`，按 IV gap、DTE、delta/moneyness、event window 和 L2 freshness 分层比较 5 个模型；353 个 replayable events 中 352 个 replay edge 为负、1 个极小正值，23 个已观测 posterior edge 全为负，没有稳定正值模型，也没有非脆弱正候选。
- L2 policy/signal overlap：`../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_l2_policy_signal_overlap_v0_20260524T120603Z.{json,md}`。353 个可 replay edge 仍只有 1 个极小正值，478 个 delayed-depth warning、2751 个 cap-blocked、71 个 stale-depth 事件均没有可执行成本 edge；结论为 `not_promotable_l2_policy_overlap_has_no_non_fragile_positive_signal`。
- 主线 closeout：`../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_research_closeout_no_promotion_v0_20260524T131500Z.{json,md}`。汇总 5 个注册模型的 replay/overlap 证据和淘汰原因；结论为 `phase2_research_complete=true`、`phase2_promotion_proven=false`、`phase3_entry_allowed=false`。
- 更新后 registry：`../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_research_registry_v0_20260524T131600Z.{json,md}`。将 closeout 写回统一 ledger，Phase 2 主线模型研究已完成为 no-promotion，Phase 3 gate 仍为 blocked。

当前下一步：Phase 2 主线已 close out 为 no-promotion。若继续 Phase 2，只能先定义新的交易机会假设或外部特征；不要把 catalog replay 设施优化当作当前主线。posterior coverage 缺口已经被专用 artifact 证明为 Phase 3 entry gate 失败项；只有模型证据出现非脆弱正候选后，才考虑新的 no-order live shadow。只有 Phase 3 entry gate 用 artifact 证明通过后，才允许提出或进入 Phase 3。

## Phase 3: execution planner and residual-risk handling

目的：显式化两腿执行决策。

交付物：

- IOC/FOK/post-only、maker/taker、leg order、limit/worst price、timeout、cancel/replace、fallback 的 planner。
- partial fill、residual leg、max naked-leg time、flatten rules。
- 每个执行决策都能审计。
- expected cost 与 observed sandbox cost 可比较。

门槛：

- 单腿失败进入已知 residual state。
- residual exposure duration 有上限。
- IOC/FOK/GTC/post-only 的 sandbox-specific 行为有明确解释。

当前状态：未进入。Phase 2 gate 失败硬阻止 Phase 3。

## Phase 4: risk, account, and recovery

目的：把策略变成可运维系统。

交付物：

- underlying/expiry/strike/greeks/open basket/daily loss/consecutive loss limits。
- expiry blackout、market-data outage、IV shock、underlying jump protections。
- persistent state、restart reconciliation、kill switch、reduce-only、disconnect policy。
- websocket reset 后必须重新验证 quote freshness 和 account/position state。

门槛：

- restart/disconnect/half-fill/missing data 都进入已知状态。
- risk violation 阻止新开仓。
- account/strategy/sandbox states 可从 artifact 对齐。

当前状态：未进入。Phase 2 gate 失败硬阻止 Phase 4。

## Phase 5: staged near-production evidence

目的：在不真实下单的边界内逐级接近生产证据。

证据层级：

1. 使用保守执行模型的 replay/backtest。
2. 使用真实行情但不下单的 live shadow。
3. 带 execution audit 的 calibrated sandbox。
4. 可选 demo/canary，但必须与真实生产隔离，且不得放真实账户订单。
5. production-candidate 报告。

门槛：

- 每个 tier 都有 artifact 和 review。
- alerts/reporting/risk/recovery 被证明。
- 无真实账户执行。

当前状态：未进入。Phase 2 gate 失败硬阻止 Phase 5。
