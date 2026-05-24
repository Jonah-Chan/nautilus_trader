# Phase 1 `v0_selective_l2_execution_audit`

## 目标

在不订阅全量 option universe L2 的前提下，引入可审计的执行现实性估计。

## 策略代码

- 主模块：`../strategies/phase1_v0_selective_l2_execution_audit.py`。
- 可选修订：只有 audit model 发生实质变化时才允许新增 `../strategies/phase1_v1_selective_l2_execution_audit.py`。
- 不得把 `../strategies/phase0_v0_flow_validation.py` 改造成 Phase 1 策略。

## 已实现

- side-aware depth helper：BUY 吃 asks，SELL 吃 bids。
- VWAP 计算：executable quantity、notional、worst price、insufficient-depth classification。
- `missing_book` / `stale_book` / `warming_up` / `not_selected_for_l2` 等 audit status。
- active-leg-first bounded L2 subscription selection。
- selective `subscribe_order_book_depth(..., depth=5|10)` wiring。
- `EXECUTION_AUDIT` 日志：status、executable quantity、VWAP、worst price、notional、book age、depth levels。
- 独立 `parse_args` / `build_node_components` / `build_node` runner。
- `PHASE1_EVAL_SUMMARY`：解释 no-candidate run 是 missing chain、cross-series skew、stale chain、missing quote、non-positive quote、internal reject 还是 true opportunity。
- `--l2-warmup-ms`、`--l2-retention-ms`、`--l2-min-hold-ms`、`--max-l2-subscription-changes-per-scan` 等 bounded L2 控制。
- retained candidate re-audit、subscription context fields、close-side L2 audit for Phase 2 posterior collection。
- `../tools/analyze_phase1_execution_audit.py`：从 log 生成 execution-audit JSON artifact，并给出 event gate / policy verdict。

## 设计边界

- broad scan 使用 `OptionChainSlice`、L1 quote、greeks、status。
- 只对 top candidate legs 和 active basket legs 订阅临时 bounded `books5`。
- 当前 live Phase 1 artifact 都是 data-only，`sandbox_fill_rows=0`。
- analyzer 可在未来 execution-enabled sample 中把 sandbox `OrderFilled` 与 L2 audit row 关联，但 artifact 必须标记为 `lifecycle_only_not_exchange_fill_quality`。
- delayed-depth 5s-30s 行被标记为 `shadow_only_delayed_depth_warning`：它们不能用于 Phase 1 sandbox execution gate，只能带 warning 标签输入 Phase 2 shadow research。

## 关键证据

Phase 1 已通过多轮 data-only shadow 找到并收敛了以下问题：

- candidate 可能因 stale chain / cross-series skew 短窗口消失。
- 直接 subscribe 后立刻 audit 会产生大量 `missing_book`，需要 warmup/retention。
- 单纯扩大 L2 cap 或 complete-candidate-basket selector 不足以通过 gate。
- ranked active series 能证明 BTC dominance 不是固定策略规则，而是市场窗口和订阅/cap 压力共同结果。
- selected books 通常能收到初始 depth，但 depth refresh cadence 稀疏，很多 selected legs 变成 delayed-depth warning。

当前参考 artifact：

- `../../../../.omx/artifacts/dynamic-calendar-pnl/execution_audit_summary_phase2_v1_live_shadow_posterior1h_20260524T065244Z.json`

当前 gate 状态：

- `phase1_policy_calibration.verdict=diagnostic_only_not_sandbox_execution_ready`。
- `sandbox_fill_rows=0`。
- full-fresh two-leg event coverage 为 `0.10497866287339971`，低于 sandbox-sample gate。
- selected delayed-depth warning ratio 为 `0.3665644171779141`，高于 gate。
- cap-blocked events `2751` 多于 full-fresh executable events `369`。

L2 calibration refinement artifact：

- `../../../../.omx/artifacts/dynamic-calendar-pnl/phase2_l2_calibration_phase2_v1_live_shadow_posterior1h_20260524T100053Z.{json,md}`
- 结论：`keep_data_only_calibrate_delayed_depth_and_cap_blocking`。

## 门槛

- L2 subscription count 必须低于配置上限。
- 每个 candidate 都有 audit classification。
- sandbox fill 不能单独证明真实交易所 fill quality。
- 进入 sandbox execution sample 前，candidate-level L2 freshness、delayed-depth policy、cap-blocked interpretation 必须先清楚。

## 测试与验证

当前相关测试覆盖：

- active-leg-first top-N selection。
- sufficient/insufficient/stale/missing book VWAP。
- BUY/SELL side depth selection。
- Phase 1 build-node config inheritance 与 L2 控制。
- analyzer policy fields、event gate、selected-L2 age distribution、reentrant subscribe/unsubscribe bookkeeping。

历史验证已通过多轮 targeted pytest 和 ruff；当前 Phase 1 结论不是测试失败，而是 live data-only gate 未满足。

## 下一步

不要启动 Phase 1 sandbox-execution sample。下一步只应围绕 delayed-depth / cap-blocking policy 做可解释 data-only 校准，并把 warning-only delayed-depth 行作为 Phase 2 shadow input，而不是把它误标为 executable。
