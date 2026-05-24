# Phase 3 `v0_execution_planner`

## 目标

显式化两腿执行决策，并让失败路径可恢复、可审计。

## 策略代码

- 计划主模块：`../strategies/phase3_v0_execution_planner.py`。
- 可选修订：只有 planner semantics 发生实质变化时才允许 `../strategies/phase3_v1_execution_planner.py`。
- 不得把 execution-planner 行为回写到早期 validation 或 signal-shadow 模块。

## 设计

- planner 决定 taker/maker、IOC/FOK/post-only、leg order、limit price、worst price、timeout、fallback。
- partial fill、cancel/replace、missing close quote、reduce-only no-position、residual flatten 都是显式状态。
- 每个决策都输出可审计 execution record。
- Phase 0 已证明 Nautilus sandbox IOC 可能与本地 `INITIALIZED` 状态竞态，并在预期 immediate cancel 后延迟成交。Phase 3 必须把这类行为标成 sandbox-specific，不能偷换成 OKX exchange IOC 语义。
- GTC 可以用于 flow validation，但不能自动变成类生产执行决策。planner 必须说明每种 TIF 的选择理由和陈旧挂单风险。

## 测试

- 合成事件测试：单腿成交、partial fill、IOC cancel、timeout、reduce-only reject、flatten fallback。
- short max-open sandbox run。
- IOC cancel race、delayed maker fill、stale GTC order、post-disconnect active basket close 场景。

## 门槛

- 单腿失败不能被误判为 normal FLAT。
- residual exposure duration 有上限。
- expected cost 与 observed sandbox cost 按 cycle 比较。
- IOC、FOK、GTC、post-only 的 sandbox-specific 行为都有明确审计解释。

## 当前状态

未进入。Phase 2 gate 当前 `phase2_passes=false`、`phase3_entry_allowed=false`，因此不得实现或执行 Phase 3 planner。
