# Phase 4 `v0_risk_recovery`

## 目标

加入风险限制、状态持久化和恢复行为，把策略从研究脚本推进为可运维系统。

## 策略代码

- 计划主模块：`../strategies/phase4_v0_risk_recovery.py`。
- 可选修订：只有 recovery 或 risk semantics 发生实质变化时才允许 `../strategies/phase4_v1_risk_recovery.py`。
- Phase 3 planner 不应拥有持久化恢复或 account reconciliation 语义。

## 设计

- 风险限制：underlying、expiry、strike、greeks、open basket count、loss、naked-leg duration。
- kill switch 与 reduce-only mode。
- active baskets、orders、positions、residual legs 持久化状态。
- 基于 sandbox account state 做 restart reconciliation。
- Phase 0 中 websocket/TLS reset 没有破坏接受 run，但 Phase 4 必须把该观察转成政策：重连预算、陈旧数据冻结、活跃 basket 平仓政策、重连后的 account/position reconciliation。
- transport reset 后的新开仓必须显式重验 quote freshness 和 strategy/account state。

## 测试

- risk trip fixture。
- open position restart。
- subscription disconnect/reconnect 检查。
- account/strategy/sandbox reconciliation 报告。
- 从 Phase 0 reset window log replay，证明 reconnect 后不能用 stale quote 静默开仓。

## 门槛

- restart 和 disconnect 进入已知状态。
- risk violation 阻止新开仓。
- account state 与 strategy state 可从 artifact 对齐。
- websocket reset 后，除非 quote freshness 与 account/position state 已重验，否则不能允许新 entry。

## 当前状态

未进入。Phase 2 gate 当前失败，因此不得实现或执行 Phase 4 risk/recovery 路径。
