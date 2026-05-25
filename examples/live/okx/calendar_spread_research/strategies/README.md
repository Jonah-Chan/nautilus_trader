# 日历价差策略版本

本目录归档 OKX 期权日历价差研究轨道的全部策略代码版本。

## 版本规则

- 每个 Phase 启动后，对应策略模块作为保留版本管理。
- 每个 Phase 至少一个、最多五个策略版本，例如 `phase1_v0_...py`，必要时可继续到 `phase1_v4_...py`。
- 每个版本策略文件名必须包含该版本最显著的特点以作区分，避免只有版本号不同但语义不清。
- 不要继续向 `phase0_v0_flow_validation.py` 添加盈利、执行或风险逻辑。
- 根目录文件 `../../okx_option_calendar_spread_dynamic.py` 和 `../../okx_option_calendar_spread.py` 只作为兼容入口。
- 一个 Phase promotion 后，应冻结上一阶段模块并创建下一阶段模块，而不是把旧文件改成新的行为面。
- 不要为未启动阶段创建 placeholder 策略模块；version record 会先保留命名。

## 文件

- `phase0_v0_flow_validation.py`：已接受的 BTC/ETH flow-validation harness，使用 OKX live data 与 Nautilus sandbox execution。
- `phase0_v0_flow_validation_strategy_logic.md`：与 Phase 0 源码对齐的中文策略逻辑文档。
- `phase1_v0_selective_l2_execution_audit.py`：Phase 1 selective `books5` execution-audit 策略，继承 Phase 0 生命周期路径，增加 bounded L2 depth subscription 和 `EXECUTION_AUDIT`。
- `phase2_v0_shadow_signal_research.py`：Phase 2 离线 shadow-input filter，消费 Phase 1 execution-audit artifact，保留 execution-policy label，不提交订单。
- `phase2_v1_live_shadow_posterior_collector.py`：Phase 2 live-shadow posterior collector，继承 Phase 1 selective L2 audit，默认 no-order；显式 `--enable-execution --no-dry-run` 可接入 Nautilus 本地 sandbox 做受控执行测试。它跟踪 candidate watches，发出 close-side `posterior_<window>` audit rows，并在 live chain Greeks 可用时写入保守的 L1-mid calendar fair-value proxy 与 Greeks/DTE/moneyness context。
- `phase2_v1_shadow_signal_research.py`：兼容 wrapper，转发到 `phase2_v1_live_shadow_posterior_collector.py`。
- `reference_fixed_calendar_spread.py`：旧的固定两腿参考示例，仅用于对比。

## 计划模块名

| 阶段 | v0 模块 | 可选 v1 模块 |
| --- | --- | --- |
| Phase 0 | `phase0_v0_flow_validation.py` | 不计划新增；Phase 0 验收后冻结 |
| Phase 1 | `phase1_v0_selective_l2_execution_audit.py` | 只有 audit model 实质变化时才允许 `phase1_v1_selective_l2_execution_audit.py` |
| Phase 2 | `phase2_v0_shadow_signal_research.py` | `phase2_v1_live_shadow_posterior_collector.py` live-shadow posterior collector |
| Phase 3 | `phase3_v0_execution_planner.py` | 只有 planner semantics 实质变化时才允许 `phase3_v1_execution_planner.py` |
| Phase 4 | `phase4_v0_risk_recovery.py` | 只有 recovery/risk semantics 实质变化时才允许 `phase4_v1_risk_recovery.py` |
| Phase 5 | `phase5_v0_staged_near_production.py` | 只有 evidence-tier semantics 实质变化时才允许 `phase5_v1_staged_near_production.py` |
