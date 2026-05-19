# 仓位 (Positions)

本指南解释了 NautilusTrader 中仓位 (Positions) 的工作原理，包括其生命周期、从订单成交 (Order fills) 中聚合、盈亏 (PnL) 计算，以及净额结算 (Netting) OMS 配置中重要的仓位快照 (Position snapshotting) 概念。

## 概览 (Overview)

仓位 (Position) 代表对市场中特定合约 (Instrument) 的未平仓风险敞口。仓位是跟踪交易绩效和风险的基础，因为它们聚合了特定合约的所有成交，并持续计算未实现盈亏 (Unrealized PnL)、平均入场价 (Average entry price) 和总敞口等指标。

系统在订单成交时自动创建仓位，并跟踪其从开启到关闭的全过程。平台通过其 OMS（订单管理系统）配置支持净额结算 (Netting) 和对冲 (Hedging) 两种仓位管理风格。

## 仓位生命周期 (Position lifecycle)

### 创建 (Creation)

系统在第一次成交时开启一个仓位：

- **净额结算 (NETTING) OMS**：在合约的第一次成交时开启（每个合约一个仓位）。
- **对冲 (HEDGING) OMS**：在新的 `position_id` 的第一次成交时开启（每个合约有多个仓位）。

仓位跟踪以下信息：

- 开启订单和成交详情。
- 入场方向 (`LONG` 多头 或 `SHORT` 空头)。
- 初始数量和平均价格。
- 初始化和开启的时间戳。

:::tip
您可以从您的 Actor/策略中使用 `self.cache.position(position_id)` 或 `self.cache.positions(instrument_id=instrument_id)` 通过缓存 (Cache) 访问仓位。
:::

### 更新 (Updates)

随着后续成交的发生，仓位会：

- 聚合买入和卖出成交的数量。
- 重新计算平均入场和离场价格。
- 更新峰值数量（达到的最大敞口）。
- 跟踪所有关联的订单 ID 和成交 ID。
- 按币种累计佣金 (Commissions)。

### 关闭 (Closure)

当净数量变为零 (`FLAT`) 时，仓位关闭。关闭时：

- 记录平仓订单 ID。
- 计算从开启到关闭的持续时间。
- 计算最终已实现盈亏 (Realized PnL)。
- 在净额结算 (NETTING) OMS 中，当仓位稍后重新开启时，引擎会对关闭状态进行快照以保留历史盈亏（参见 [仓位快照 (#position-snapshotting)](#仓位快照-position-snapshotting)）。

## 订单成交聚合 (Order fill aggregation)

仓位聚合订单成交以维持对市场敞口的准确视图。聚合过程 handle both sides of trading activity:

### 买入成交 (Buy fills)

当买单 (BUY order) 成交时：

- 增加多头敞口或减少空头敞口。
- 更新开仓交易的平均入场价。
- 更新平仓交易的平均离场价。
- 计算任何已平仓部分的已实现盈亏 (Realized PnL)。

### 卖出成交 (Sell fills)

当卖单 (SELL order) 成交时：

- 增加空头敞口或减少多头敞口。
- 更新开仓交易的平均入场价。
- 更新平仓交易的平均离场价。
- 计算任何已平仓部分的已实现盈亏 (Realized PnL)。

### 净仓位计算 (Net position calculation)

仓位维护一个代表净敞口的 `signed_qty` (有符号数量) 字段：

- 正值表示 `LONG` (多头) 仓位。
- 负值表示 `SHORT` (空头) 仓位。
- 零表示 `FLAT` (持平/已平仓) 仓位。

```python
# 示例：仓位聚合
# 初始以 $50 买入 100 单位
signed_qty = +100  # 多头 (LONG) 仓位

# 随后以 $55 卖出 150 单位
signed_qty = -50   # 现在是空头 (SHORT) 仓位

# 最后以 $52 买入 50 单位
signed_qty = 0     # 仓位持平 (FLAT)（已关闭）
```

## 仓位调整 (Position adjustments)

仓位调整跟踪在正常订单成交之外发生的数量或盈亏变化，确保仓位数量准确反映真实的净资产头寸。系统为这些场景生成 `PositionAdjusted` 事件。

### 基础货币佣金 (Base currency commissions)

交易现货货币对（例如 BTC/USDT）或外汇现货时，以基础货币支付的佣金直接影响接收或交付的净数量：

- **开仓成交**：佣金从交易数量中扣除。以 0.001 BTC 佣金买入 1.0 BTC，会导致 0.999 BTC 的净多头仓位。
- **平仓成交**：佣金应用于 `signed_qty`，因为它影响实际库存。卖出 0.999 BTC 的多头 (LONG) 仓位并支付 0.000999 BTC 佣金，会使您持有 0.000999 BTC 的空头 (SHORT) 仓位，而不是持平 (FLAT)，因为您总共付出了 0.999999 BTC。
- **反手 (Flips)**：佣金影响反手双方的最终仓位大小。

:::note
基础货币佣金仅适用于佣金币种与 `instrument.base_currency` 匹配的现货货币对和外汇现货合约。对于其他合约，佣金是单独跟踪的，不影响仓位数量。
:::

### 资金费用 (Funding payments)

资金调整跟踪永续合约的定期支付，而不影响仓位数量。这些记录为 `quantity_change = None`，并可能包含盈亏影响。

### 调整跟踪 (Adjustment tracking)

所有调整都保留在仓位事件历史记录中：

- `position.adjustments` 返回所有 `PositionAdjusted` 事件的列表。
- 每项调整包括类型（`COMMISSION` 或 `FUNDING`）、数量变化和时间戳。
- 当仓位关闭并重新开启时，调整历史记录会被清除。当事件被清除时，与移除的成交相关的佣金调整会被重新生成，而非佣金调整（例如资金费用）会被保留。

## OMS 类型与仓位管理 (OMS types and position management)

NautilusTrader 支持两种主要的 OMS 类型，它们从根本上影响仓位的跟踪和管理方式。还存在 `OmsType.UNSPECIFIED` 选项，它默认为组件的上下文。有关完整详细信息，请参阅 [执行指南 (execution.md#order-management-system-oms)](execution.md#order-management-system-oms)。

### 净额结算 (NETTING)

在 `NETTING` 模式下，一个合约的所有成交都聚合成一个单一仓位：

- 每个合约 ID 一个仓位。
- 所有成交都计入同一个仓位。
- 随着净数量的变化，仓位会从 `LONG` (多头) 转换为 `SHORT` (空头)（或反之）。
- 历史快照保留已关闭的仓位状态。

### 对冲 (HEDGING)

在 `HEDGING` 模式下，同一个合约可以存在多个仓位：

- 同时存在多个 `LONG` (多头) 和 `SHORT` (空头) 仓位。
- 每个仓位都有一个唯一的仓位 ID。
- 仓位是独立跟踪的。
- 仓位之间没有自动净额结算。
- 关闭的仓位保留在缓存历史中但不会重新开启；新成交会创建新仓位。

:::warning
使用 `HEDGING` 模式时，请注意保证金要求的增加，因为每个仓位都会独立消耗保证金。某些交易场所可能不支持真正的对冲模式，并会自动对仓位进行净额结算。
:::

### 策略与交易场所 OMS (Strategy vs venue OMS)

平台允许为策略和交易场所配置不同的 OMS：

| 策略 OMS | 交易场所 OMS | 行为 |
|----------|--------------|------|
| `NETTING` | `NETTING` | 策略和交易场所级别每个合约都只有一个仓位。 |
| `HEDGING` | `HEDGING` | 两个级别都支持多个仓位。 |
| `NETTING` | `HEDGING` | 交易场所跟踪多个仓位，Nautilus 维持单一仓位。 |
| `HEDGING` | `NETTING` | 交易场所跟踪单一仓位，Nautilus 维护虚拟仓位。 |

:::tip
对于大多数交易场景，保持策略和交易场所 OMS 类型一致可以简化仓位管理。覆盖配置主要适用于自营交易台或对接旧系统。有关交易场所特定的 OMS 配置，请参阅 [实盘指南 (live.md)](live.md)。
:::

## 仓位快照 (Position snapshotting)

仓位快照是净额结算 (NETTING) OMS 配置的一项重要功能，它保留已关闭仓位的状态，以便进行准确的盈亏 (PnL) 跟踪和报告。

### 为什么快照很重要 (Why snapshotting matters)

在净额结算 (NETTING) 系统中，当一个仓位关闭（变为 `FLAT`）然后通过新交易重新开启时，仓位对象会被重置以跟踪新敞口。如果没有快照，来自上一个仓位周期的历史已实现盈亏 (Realized PnL) 将会丢失。

### 工作原理 (How it works)

当一个 `NETTING` 仓位关闭然后收到同一合约的新成交时，执行引擎会在重置之前对关闭的仓位状态进行快照，保留：

- 最终的数量和价格。
- 已实现盈亏 (Realized PnL)。
- 所有成交事件。
- 佣金总额。

该快照存储在以仓位 ID 为索引的缓存中。然后，仓位为新周期重置，而先前的快照仍然可以访问。投资组合 (Portfolio) 聚合所有快照的盈亏以获得准确的总额。

:::note
这种历史快照机制不同于可选的仓位状态快照 (`snapshot_positions`)，后者定期记录开仓状态用于遥测。有关 `snapshot_positions` 和 `snapshot_positions_interval_secs` 设置，请参阅 [实盘指南 (live.md)](live.md)。
:::

### 示例场景 (Example scenario)

```python
# 净额结算 (NETTING) OMS 示例
# 周期 1：开启多头 (LONG) 仓位
BUY 100 单位，价格 $50   # 仓位开启
SELL 100 单位，价格 $55  # 仓位关闭，盈亏 = $500
# 进行快照，保留 $500 已实现盈亏

# 周期 2：开启空头 (SHORT) 仓位
SELL 50 单位，价格 $54   # 仓位重新开启 (空头)
BUY 50 单位，价格 $52    # 仓位关闭，盈亏 = $100
# 进行快照，保留 $100 已实现盈亏

# 总已实现盈亏 = $500 + $100 = $600（来自快照）
```

如果没有快照，只能获得最近一个周期的盈亏，从而导致报告和分析错误。

## 盈亏计算 (PnL calculations)

NautilusTrader 提供的盈亏计算考虑了合约规范和市场惯例。

### 已实现盈亏 (Realized PnL)

在仓位部分或全部平仓时计算：

```python
# 对于标准合约
realized_pnl = (exit_price - entry_price) * closed_quantity * multiplier

# 对于反向 (inverse) 合约（区分方向）
# 多头 (LONG): realized_pnl = closed_quantity * multiplier * (1/entry_price - 1/exit_price)
# 空头 (SHORT): realized_pnl = closed_quantity * multiplier * (1/exit_price - 1/entry_price)
```

引擎根据仓位方向自动应用正确的公式。

### 未实现盈亏 (Unrealized PnL)

使用当前市场价格为开仓仓位计算。`price` 参数接受任何参考价格（买入价、卖出价、中值价、最后成交价或标记价格）：

```python
position.unrealized_pnl(last_price)  # 使用最后成交价
position.unrealized_pnl(bid_price)   # 对多头仓位保守
position.unrealized_pnl(ask_price)   # 对空头仓位保守
```

对于持平 (`FLAT`) 仓位，无论提供的价格是多少，都返回 `Money(0, settlement_currency)`。

### 总盈亏 (Total PnL)

结合已实现和未实现部分：

```python
total_pnl = position.total_pnl(current_price)
# 返回已实现盈亏 + 未实现盈亏
```

### 货币考虑因素 (Currency considerations)

- 盈亏以合约的结算货币计算。
- 对于外汇，这通常是计价货币。
- 对于反向合约，盈亏可能以基础货币计价。
- 投资组合按合约以结算货币聚合已实现盈亏。
- 多货币总额需要仓位类之外的转换。

## 佣金与成本 (Commissions and costs)

仓位跟踪所有交易成本：

- 佣金按币种累计。
- 每笔成交的佣金都会添加到运行总计中。
- 支持多种佣金币种。
- 仅当佣金以结算货币计价时，已实现盈亏才包含佣金。
- 其他佣金是单独跟踪的，可能需要转换。

```python
commissions = position.commissions()
# 返回 list[Money]，包含按币种聚合的佣金总额

notional = position.notional_value(current_price)
# 返回计价货币（标准）或基础货币（反向）的金额 (Money)
```

**局限性：**

- 如果反向合约未设置 `base_currency`，则会引发 panic。
- 不处理 quanto 合约（返回计价货币而不是结算货币）。
- 对于 quanto 合约，请改用 `instrument.calculate_notional_value()`。

## 仓位属性与状态 (Position properties and state)

### 标识符 (Identifiers)

- `id`: 唯一的仓位标识符。
- `instrument_id`: 交易的合约。
- `account_id`: 持有仓位的账户。
- `trader_id`: 拥有仓位的交易者。
- `strategy_id`: 管理仓位的策略。
- `opening_order_id`: 开启仓位的客户端订单 ID。
- `closing_order_id`: 关闭仓位的客户端订单 ID。

### 仓位状态 (Position state)

- `side`: 当前仓位方向（`LONG`, `SHORT`, 或 `FLAT`）。
- `entry`: 当前开仓的方向（多头 `LONG` 为 `Buy`，空头 `SHORT` 为 `Sell`）。仓位反手时更新。
- `quantity`: 当前仓位绝对大小。
- `signed_qty`: 有符号仓位大小（多头为正，空头为负）。
- `peak_qty`: 仓位生命周期内达到的最大数量。
- `is_open`: 仓位当前是否开启。
- `is_closed`: 仓位是否已关闭 (`FLAT`)。
- `is_long`: 仓位方向是否为 `LONG` (多头)。
- `is_short`: 仓位方向是否为 `SHORT` (空头)。

### 定价与估值 (Pricing and valuation)

- `avg_px_open`: 平均入场价。
- `avg_px_close`: 平仓时的平均离场价。
- `realized_pnl`: 已实现盈亏。
- `realized_return`: 十进制形式的已实现回报率（例如 5% 为 0.05）。
- `quote_currency`: 合约的计价货币。
- `base_currency`: 基础货币（如果适用）。
- `settlement_currency`: 盈亏结算货币。

### 合约规范 (Instrument specifications)

- `multiplier`: 合约乘数。
- `price_precision`: 价格的十进制精度。
- `size_precision`: 数量的十进制精度。
- `is_inverse`: 是否为反向合约。

### 时间戳 (Timestamps)

- `ts_init`: 仓位初始化的时间。
- `ts_opened`: 仓位开启的时间。
- `ts_last`: 最后一次更新的时间戳。
- `ts_closed`: 仓位关闭的时间。
- `duration_ns`: 从开启到关闭的纳秒级持续时间。

### 关联数据 (Associated data)

- `symbol`: 合约的代码。
- `venue`: 交易场所。
- `client_order_ids`: 与仓位关联的所有客户端订单 ID。
- `venue_order_ids`: 与仓位关联的所有交易场所订单 ID。
- `trade_ids`: 来自交易场所的所有成交 ID。
- `events`: 应用于仓位的所有订单成交事件。
- `event_count`: 应用的成交事件总数。
- `last_event`: 最近的成交事件。
- `last_trade_id`: 最近的成交 ID。

:::info
有关完整的类型信息和详细的属性文档，请参阅 仓位 [API 参考](/docs_zh/python-api-latest/model/position.html#nautilus_trader.model.position.Position)。
:::

## 事件与跟踪 (Events and tracking)

仓位维护完整的事件历史记录：

- 所有订单成交事件按时间顺序存储。
- 跟踪关联的客户端订单 ID。
- 保留来自交易场所的成交 ID。
- 事件计数指示应用的成交总数。

这些历史数据可以实现：

- 详细的仓位分析。
- 交易对账。
- 绩效归因。
- 审计追踪。

:::tip
使用 `position.events` 访问完整的成交历史以进行对账。`position.trade_ids` 属性有助于与经纪商对账单进行匹配。有关对账的最佳实践，请参阅 [执行指南 (execution.md)](execution.md)。
:::

## 数值精度 (Numerical precision)

仓位计算使用 64 位浮点数 (`f64`) 进行盈亏和平均价格计算。虽然定点类型（`Price`, `Quantity`, `Money`）在配置的小数位上保留精确精度，但内部计算会转换为 `f64` 以平衡性能和溢出安全。

### 设计初衷 (Design rationale)

平台在仓位计算中使用 `f64` 以平衡性能和准确性：

- 浮点运算明显快于任意精度算术。
- 即使使用 128 位整数，原始整数乘法也可能溢出。
- 每次计算都从精确的定点值开始，避免了累计误差。
- IEEE-754 双精度提供约 15 位十进制数字的准确度。

### 经过验证的精度特性 (Validated precision characteristics)

测试确认 `f64` 算术在典型的交易场景中保持准确性：

- 标准金额：标准货币中金额 ≥ 0.01 时无精度损失。
- 高精度合约：9 位小数的加密货币价格保留在 1e-6 容差范围内。
- 连续成交：100 次成交显示无漂移（佣金准确度达 1e-10）。
- 极端价格：处理从 0.00001 到 99,999.99999 的范围而不会溢出。
- 往返交易：以相同价格开启和关闭会产生精确的盈亏（仅计佣金）。

有关实现细节，请参阅 `crates/model/src/position.rs` 中的 `test_position_pnl_precision_*` 测试。

:::note
对于需要精确十进制算术的监管合规或审计追踪，请考虑使用外部库的 `Decimal` 类型。低于 `f64` 最小精度 (~1e-15) 的极小金额可能会四舍五入为零。这不会影响具有标准货币精度（通常为 2-9 位小数）的实际交易场景。
:::

## 与其他组件的集成 (Integration with other components)

仓位与几个关键组件交互：

- **投资组合 (Portfolio)**：聚合不同合约和策略的仓位。
- **执行引擎 (ExecutionEngine)**：根据成交创建并更新仓位。
- **缓存 (Cache)**：存储仓位状态和快照。
- **风险引擎 (RiskEngine)**：监控仓位限制和敞口。

:::note
不会为价差合约 (Spread instruments) 创建仓位。虽然挂钩订单仍可为价差触发，但它们在没有仓位链接的情况下运行。引擎将价差合约与常规仓位分开处理。
:::

## 总结 (Summary)

仓位是跟踪交易活动和绩效的核心。在构建交易策略时，了解仓位如何聚合成交、计算盈亏以及处理不同的 OMS 配置至关重要。仓位快照在净额结算 (NETTING) 模式下提供了准确的历史跟踪，而事件历史则支持详细的分析和对账。

## 相关指南 (Related guides)

- [事件 (Events)](events.md) - 成交如何产生仓位事件。
- [订单 (Orders)](orders.md) - 创建和修改仓位的订单。
- [执行 (Execution)](execution.md) - 更新仓位的成交处理。
- [投资组合 (Portfolio)](portfolio.md) - 投资组合级别的仓位聚合。
