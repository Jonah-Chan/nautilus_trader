# Phase 5 `v0_staged_near_production`

## 目标

在不真实下单的边界内，逐级形成接近生产的证据。

## 策略代码

- 计划主模块：`../strategies/phase5_v0_staged_near_production.py`。
- 可选修订：只有 evidence-tier semantics 发生实质变化时才允许 `../strategies/phase5_v1_staged_near_production.py`。
- 即使到 Phase 5，真实账户执行仍不在本研究轨道内。

## 证据层级

1. 使用保守执行模型的 replay/backtest。
2. 使用真实行情但无订单的 live shadow。
3. 带 execution audit 的 calibrated sandbox。
4. 可选 demo/canary：必须显式隔离真实生产，且本研究轨道仍不得放真实账户订单。
5. 保留 no-real-order 边界的 production-candidate report。

Phase 0 后的复盘结论：不能只靠 lifecycle success 推广。Phase 0 证明了 data/order/position/report path；Phase 1-4 仍必须分别证明 executable pricing、positive expectancy、显式 execution decision 和 recovery policy。

## 测试

- 15-30 分钟 smoke run。
- 2-4 小时 validation run。
- 单次 run 上限：8 小时。
- 跨证据层级差异复盘。

## 门槛

- 每次 promotion 都有 artifact 证据。
- 告警、报告、风控和恢复路径已被证明。
- 真实交易所执行保持在研究范围之外。
- production-candidate 证据必须展示从 shadow signal 到 execution audit，再到 risk/recovery check 的 bucket 谱系。

## 当前状态

未进入。Phase 2 gate 当前失败，因此不得推进 Phase 5 evidence tier。
