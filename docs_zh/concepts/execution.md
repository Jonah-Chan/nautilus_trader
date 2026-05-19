# 执行 (Execution)

NautilusTrader 可以同时（每个实例）处理多个策略和交易场所的交易执行和订单管理。执行涉及多个交互组件，因此了解执行消息（命令和事件）的可能流程非常重要。

主要的执行相关组件包括：

- `Strategy` (策略)
- `ExecAlgorithm` (执行算法)
- `OrderEmulator` (订单仿真器)
- `RiskEngine` (风险引擎)
- `ExecutionEngine` 或 `LiveExecutionEngine` (执行引擎)
- `ExecutionClient` 或 `LiveExecutionClient` (执行客户端)

## 执行流程 (Execution flow)

`Strategy` 基类继承自 `Actor`，包含所有通用的数据方法。它还提供了管理订单和交易执行的方法：

- `submit_order(...)` (提交订单)
- `submit_order_list(...)` (提交订单列表)
- `modify_order(...)` (修改订单)
- `cancel_order(...)` (取消订单)
- `cancel_orders(...)` (取消多个订单)
- `cancel_all_orders(...)` (取消所有订单)
- `close_position(...)` (平仓)
- `close_all_positions(...)` (全平)
- `query_account(...)` (查询账户)
- `query_order(...)` (查询订单)

这些方法创建必要的执行命令，并通过消息总线将它们发送到相关组件（点对点）。它们还发布事件，如 `OrderInitialized`。

并非每个命令都有单一的线性路径：

- `submit_order(...)` 对于仿真订单路由到 `OrderEmulator`；当设置了 `exec_algorithm_id` 时路由到 `ExecAlgorithm`；否则路由到 `RiskEngine`。
- `submit_order_list(...)` 根据仿真和 `exec_algorithm_id` 遵循相同的分支行为。
- `modify_order(...)` 对于仿真订单路由到 `OrderEmulator`；否则路由到 `RiskEngine`。
- 取消和查询命令可以根据命令和订单状态直接路由到 `OrderEmulator`、`ExecAlgorithm` 或 `ExecutionEngine`。

对于新订单提交，典型的流程如下：

`Strategy` -> `OrderEmulator` 或 `ExecAlgorithm` 或 `RiskEngine`

从那里开始，下游流程通常是：

`OrderEmulator` -> `ExecAlgorithm` 或 `ExecutionEngine`

`ExecAlgorithm` -> `RiskEngine` -> `ExecutionEngine` -> `ExecutionClient`

下图说明了 Nautilus 执行组件之间的消息流（命令和事件）。

```mermaid
flowchart LR
    strategy[策略 (Strategy)]
    emulator[订单仿真器 (OrderEmulator)]
    algo[执行算法 (ExecAlgorithm)]
    risk[风险引擎 (RiskEngine)]
    engine[执行引擎 (ExecutionEngine)]
    client[执行客户端 (ExecutionClient)]

    strategy --> emulator
    strategy --> algo
    strategy --> risk
    strategy --> engine
    emulator -. 订单发布 (OrderReleased) .-> risk
    emulator --> algo
    emulator --> engine
    algo --> risk
    risk <--> engine
    engine <--> client
```

## 订单管理系统 (Order Management System - OMS)

订单管理系统 (OMS) 类型是指用于将订单分配给仓位 (Position) 并跟踪该合约 (Instrument) 仓位的方法。
OMS 类型适用于策略和交易场所（模拟和真实）。即使交易场所没有明确说明所使用的方法，OMS 类型也始终在起作用。可以使用 `OmsType` 枚举指定组件的 OMS 类型。

`OmsType` 枚举有三个变体：

- `UNSPECIFIED` (未指定)：OMS 类型根据其应用位置默认为相应类型（详见下文）。
- `NETTING` (净额结算)：仓位被合并为每个合约 ID (Instrument ID) 一个单一仓位。
- `HEDGING` (对冲)：支持每个合约 ID 多个仓位（包括多头和空头）。

下表描述了不同的配置组合及其适用场景。
当策略和交易场所的 OMS 类型不同时，`ExecutionEngine` 通过覆盖或为接收到的 `OrderFilled` 事件分配 `position_id` 值来处理此问题。
“虚拟仓位 (Virtual position)”是指存在于 Nautilus 系统内但实际上不存在于交易场所的仓位 ID。

| 策略 OMS | 交易场所 OMS | 描述 |
|:---------|:-------------|:-----|
| `NETTING` | `NETTING` | 策略使用交易场所的原生 OMS 类型，每个合约 ID 只有一个仓位 ID。 |
| `HEDGING` | `HEDGING` | 策略使用交易场所的原生 OMS 类型，每个合约 ID 有多个仓位 ID（包括 `LONG` 和 `SHORT`）。 |
| `NETTING` | `HEDGING` | 策略 **覆盖** 了交易场所的原生 OMS 类型。交易场所跟踪每个合约 ID 的多个仓位，但 Nautilus 维护单一仓位 ID。 |
| `HEDGING` | `NETTING` | 策略 **覆盖** 了交易场所的原生 OMS 类型。交易场所跟踪每个合约 ID 的单一仓位，但 Nautilus 维护多个仓位 ID。 |

:::note
为策略和交易场所分别配置 OMS 类型增加了平台的复杂性，但允许广泛的交易风格和偏好（见下文）。
:::

OMS 配置示例：

- 大多数加密货币交易所使用 `NETTING` OMS 类型，代表每个市场一个单一仓位。交易者可能希望为一个策略跟踪多个“虚拟”仓位。
- 一些外汇 ECN 或经纪商使用 `HEDGING` OMS 类型，跟踪 `LONG` 和 `SHORT` 的多个仓位。交易者可能只关心每对货币的净 (NET) 仓位。

:::info
Nautilus 尚不支持交易场所侧的对冲模式，如 Binance 的 `BOTH` vs. `LONG/SHORT`（其中交易场所在每个方向上进行净额结算）。建议将 Binance 账户配置保持为 `BOTH`，以便将单一仓位进行净额结算。
:::

### OMS 配置 (OMS configuration)

如果未显式使用 `oms_type` 配置选项设置策略 OMS 类型，它将默认为 `UNSPECIFIED`。这意味着 `ExecutionEngine` 不会覆盖任何交易场所的 `position_id`，且 OMS 类型将遵循交易场所的 OMS 类型。

:::tip
在配置回测 (Backtesting) 时，您可以指定交易场所的 `oms_type`。为了准确起见，请将其与交易场所使用的 OMS 类型匹配。
:::

### 自定义仓位 ID 和净额结算 (Custom position IDs and NETTING)

自定义仓位 ID 仅在 `HEDGING` OMS 下有效。在 `NETTING` 下，根据定义，每个（合约，策略）只有一个单一仓位，引擎会为其分配一个格式为 `{instrument_id}-{strategy_id}` 的确定性 ID。

`ExecutionEngine` 在提交时强制执行此规则。如果有效的 OMS 解析为 `NETTING` 并且调用 `submit_order`（或 `submit_order_list`）时使用的 `position_id` 与 `{instrument_id}-{strategy_id}` 不匹配，则订单将被拒绝，并发出 `OrderDenied` 事件解释不匹配。

此规则仍然允许通用的平仓习惯：`Strategy.close_position(position)` 转发 `position.id`，在 `NETTING` 下这正是确定性 ID，因此它被接受。要使用任意 ID 标记或划分仓位，请将策略配置为 `oms_type=HEDGING`。

## 风险引擎 (Risk engine)

`RiskEngine` 是每个 Nautilus 系统的组成部分，包括回测、沙盒和实盘环境。它位于提交和修改路径上，还接收订单事件，如来自 `OrderEmulator` 的 `OrderReleased`。取消和查询命令直接路由到其他执行组件，不经过 `RiskEngine`。

除非在 `RiskEngineConfig` 中明确绕过，否则引擎将验证：

- 合约 (Instrument) 的价格和触发价格精度。
- 正价格，除非合约类别允许负价格。
- 数量精度以及基础数量的最小/最大边界。
- GTD 订单尚未过期。
- `reduce_only` (只减仓) 订单不会增加引用的仓位 (Position)。
- 引擎级的 `max_notional_per_order` (每单最大名义价值) 限制和合约 `max_notional` 限制。
- 非保证金账户的现金账户余额影响。
- 提交和修改的速率限制。
- 交易状态限制 (`ACTIVE`, `HALTED`, `REDUCING`)。

如果提交时的风险检查失败，系统将生成具有可读原因的 `OrderDenied` 事件。如果修改时的风险检查失败，它将生成 `OrderModifyRejected` 事件。

### 交易状态 (Trading state)

此外，Nautilus 系统的当前交易状态会影响订单流。

`TradingState` 枚举有三个变体：

- `ACTIVE`：提交和修改命令正常运行。
- `HALTED`：新的提交和修改命令被拒绝。取消命令仍然通过。
- `REDUCING`：允许取消，并且仅接受不增加风险敞口的提交或修改命令。

有关更多详细信息，请参阅 [`RiskEngineConfig` API 参考](/docs_zh/python-api-latest/config.html#nautilus_trader.risk.config.RiskEngineConfig)。

## 执行算法 (Execution algorithms)

平台支持自定义执行算法组件，并提供内置算法，如 TWAP (时间加权平均价格)。

### TWAP (时间加权平均价格)

TWAP 算法在指定的时间跨度内均匀分布执行。它接收一个代表总大小和方向的主订单，然后生成定期执行的较小子订单。

这通过随时间分布交易量来减少完整订单大小的市场影响。

该算法将立即提交第一个订单，最后提交的订单是在跨度期末的主订单。

以 TWAP 算法为例（见 `nautilus_trader/examples/algorithms/twap.py`），此示例演示了如何直接向 `BacktestEngine` 初始化并注册 TWAP 执行算法（假设引擎已经初始化）：

```python
from nautilus_trader.examples.algorithms.twap import TWAPExecAlgorithm

# `engine` 是一个已初始化的 BacktestEngine 实例
exec_algorithm = TWAPExecAlgorithm()
engine.add_exec_algorithm(exec_algorithm)
```

对于此特定算法，必须指定两个参数：

- `horizon_secs` (跨度秒数)
- `interval_secs` (间隔秒数)

`horizon_secs` 参数决定算法执行的时间段，而 `interval_secs` 参数设置单个订单执行之间的时间。这些参数决定了主订单如何拆分为一系列生成的订单。

```python
from decimal import Decimal
from nautilus_trader.model.data import BarType
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.examples.strategies.ema_cross_twap import EMACrossTWAP, EMACrossTWAPConfig

# 配置您的策略
config = EMACrossTWAPConfig(
    instrument_id=TestInstrumentProvider.ethusdt_binance().id,
    bar_type=BarType.from_str("ETHUSDT.BINANCE-250-TICK-LAST-INTERNAL"),
    trade_size=Decimal("0.05"),
    fast_ema_period=10,
    slow_ema_period=20,
    twap_horizon_secs=10.0,   # 执行算法参数（总跨度秒数）
    twap_interval_secs=2.5,    # 执行算法参数（订单之间的秒数）
)

# 实例化您的策略
strategy = EMACrossTWAP(config=config)
```

或者，您可以根据实际市场情况动态地为每个订单指定这些参数。在这种情况下，策略配置参数可以提供给一个执行模型，由该模型决定跨度和间隔。

:::info
您可以创建的执行算法参数数量没有限制。参数必须是一个字典，具有字符串键和原始类型值（可以跨线路序列化的值，如整数、浮点数和字符串）。
:::

### 编写执行算法 (Writing execution algorithms)

要构建自定义执行算法，请定义一个继承自 `ExecAlgorithm` 的类。

执行算法是一种 `Actor` 类型，因此它具备以下能力：

- 请求并订阅数据。
- 访问 `Cache` (缓存)。
- 使用 `Clock` (时钟) 设置时间警报和/或定时器。

此外，它还可以：

- 访问中央 `Portfolio` (投资组合)。
- 从接收到的主 (original) 订单生成次级订单 (secondary orders)。

一旦注册了执行算法且系统正在运行，它将通过 `exec_algorithm_id` 订单参数接收消息总线上寻址到其 `ExecAlgorithmId` 的订单。订单还可以携带 `exec_algorithm_params`，它是一个 `dict[str, Any]`。

:::warning
由于 `exec_algorithm_params` 字典的灵活性，彻底验证所有键值对以确保算法正确运行非常重要（首先要验证字典不是 `None` 且所有必需参数确实存在）。
:::

接收到的订单将通过以下 `on_order(...)` 方法到达。当由执行算法处理时，这些接收到的订单被称为“主 (primary)”（原始）订单。

```python
from nautilus_trader.model.orders.base import Order

def on_order(self, order: Order) -> None:
    # 在这里处理订单
```

当算法准备好生成次级订单时，可以使用以下方法之一：

- `spawn_market(...)` (生成 `MARKET` 订单)
- `spawn_market_to_limit(...)` (生成 `MARKET_TO_LIMIT` 订单)
- `spawn_limit(...)` (生成 `LIMIT` 订单)

:::note
将来会根据需要实现更多的订单类型。
:::

这些方法中的每一个都将主（原始）`Order` 作为第一个参数。默认情况下，主订单数量会减去生成的 `quantity`。这可以通过传递 `reduce_primary=False` 来禁用。

:::warning
当 `reduce_primary=True` 时，生成的数量不得超过主订单的 `leaves_qty`（剩余未成交数量）。
:::

:::note
如果生成的订单在接受前被拒绝 (Denied/Rejected)，扣除的数量会自动恢复到主订单。一旦被交易场所接受，减少就被视为已提交。
:::

执行算法可以持续生成次级订单、提交剩余的主订单，或者两者兼而有之，这取决于其设计。内置的 TWAP 示例在最后一个间隔提交剩余的主订单。

### 生成订单 (Spawned orders)

从执行算法生成的所有次级订单都将携带一个 `exec_spawn_id`，它是主（原始）订单的 `ClientOrderId`，其 `client_order_id` 遵循以下约定从该原始标识符派生：

- `exec_spawn_id`（主订单 `client_order_id` 值）
- `spawn_sequence`（生成的订单的序列号）

```
{exec_spawn_id}-E{spawn_sequence}
```

例如：`O-20230404-001-000-E1`（对于第一个生成的订单）

:::note
选择“主 (primary)”和“次级 (secondary)”/“生成 (spawn)”术语是为了避免与“父 (parent)”和“子 (child)”挂钩订单术语发生冲突或混淆（执行算法也可能处理挂钩订单）。
:::

### 管理执行算法订单 (Managing execution algorithm orders)

`Cache` 提供了多种方法来帮助管理（跟踪）执行算法的活动。调用以下方法将返回符合给定查询过滤器的所有执行算法订单。

```python
def orders_for_exec_algorithm(
    self,
    exec_algorithm_id: ExecAlgorithmId,
    venue: Venue | None = None,
    instrument_id: InstrumentId | None = None,
    strategy_id: StrategyId | None = None,
    side: OrderSide = OrderSide.NO_ORDER_SIDE,
    account_id: AccountId | None = None,
) -> list[Order]:
```

以及更具体地查询某个执行系列/生成的订单。调用以下方法将返回给定 `exec_spawn_id` 的所有订单（如果找到）。

```python
def orders_for_exec_spawn(self, exec_spawn_id: ClientOrderId) -> list[Order]:
```

:::note
这也包括主（原始）订单。
:::

## 自有订单簿 (Own order books)

自有订单簿 (Own order books) 是 L3 级别订单簿，仅跟踪您自己（用户）按价格水平组织的订单，与交易场所的公开订单簿分开维护。

### 用途 (Purpose)

自有订单簿有多种用途：

- 实时监控您的订单在交易场所公开订单簿中的状态。
- 在提交前检查价格水平的可用流动性以验证订单放置。
- 通过识别已存在您自己订单的价格水平来帮助防止自我交易 (self-trading)。
- 支持依赖于队列位置 (queue position) 的高级订单管理策略。
- 在实盘交易期间实现内部状态与交易场所状态之间的对账。

### 生命周期 (Lifecycle)

自有订单簿是按合约 (Instrument) 维护的，并随着订单在其生命周期中的转换而自动更新。订单在提交或接受时被添加，在修改时被更新，在完全成交、取消、拒绝或过期时被移除。

只有具有价格的订单才能在自有订单簿中表示。市价单和其他没有显式价格的订单类型被排除在外，因为它们无法定位在特定的价格水平上。

### 安全的取消查询 (Safe cancellation queries)

在查询自有订单簿以获取要取消的订单时，请使用 **排除** `PENDING_CANCEL` 的 `status` (状态) 过滤器，以避免处理已经在取消过程中的订单。

:::warning
在状态过滤器中包含 `PENDING_CANCEL` 可能会导致：

- 对同一订单重复尝试取消。
- 虚增的活跃订单计数（处于 `PENDING_CANCEL` 的订单在确认取消前保持“开启”状态）。
- 当多个策略尝试取消相同订单时，订单状态爆炸。

:::

许多方法公开的可选 `accepted_buffer_ns` 参数是一个基于时间的防护，它仅返回其 `ts_accepted` (接受时间戳) 至少是过去这么多纳秒的订单。当 `accepted_buffer_ns > 0` 时，您还必须提供 `ts_now`。尚未被交易场所接受的订单其 `ts_accepted = 0`，因此一旦缓冲窗口过去，它们就会被包含在内。要排除这些在途订单，您必须将缓冲区与显式状态过滤器配对（例如，限制为 `ACCEPTED` / `PARTIALLY_FILLED`）。

### 审计 (Auditing)

在实盘交易期间，可以定期针对缓存的开启和在途订单索引对自有订单簿进行审计，以确保一致性。审计验证关闭的订单是否已被移除，并验证在途订单（已提交但尚未接受）在交易场所延迟窗口期间是否保持被跟踪。

审计间隔可以使用实盘交易配置中的 `own_books_audit_interval_secs` 参数进行配置。

## 超额成交 (Overfills)

当订单的累计成交数量超过原始订单数量时，就会发生超额成交 (Overfill)。例如，一个 100 单位的订单收到了总计 110 单位的成交，则超额成交为 10 单位。

### 超额成交是如何发生的 (How overfills occur)

超额成交可能源于两个根本不同的原因：

- 重复的成交事件（网络/消息问题）。
- 撮合引擎处的真实超额成交（真实的执行结果）。

**撮合引擎处的真实超额成交**

在某些情况下，撮合引擎实际执行的数量确实超过了订单要求的数量。这是一种真实的执行结果，而不是重复事件：

- **撮合引擎竞争条件 (Race conditions)**：在具有高并发性的快速市场中，一个订单在完全从订单簿中移除之前，可能会几乎同时与多个交易对手匹配。
- **最小手数限制**：如果订单的剩余数量低于交易场所的最小可交易手数，一些撮合引擎无论如何都会填满最小手数，而不是留下无法交易的余数。
- **DEX/AMM 机制**：使用自动做市商的去中心化交易所可能具有执行机制，其中实际成交数量由于价格影响计算而与请求的数量略有不同。
- **多重成交原子性**：一些交易场所不保证部分执行之间的原子成交数量，允许总成交超过原始订单数量。

**重复的成交事件**

除了真实的超额成交外，相同的成交事件可能会被多次投递：

- WebSocket 重新连接重新播放先前接收到的事件。
- 交易场所内部的重试或投递保证机制。
- 交易场所执行报告中的 API 时间问题。

系统通过 `trade_id` 去重处理重复事件（见下文），但具有不同 `trade_id` 值的重复事件需要进行超额成交处理。

**对账时的竞争条件 (Race conditions with reconciliation)**

在实盘交易期间，系统通过两个并行渠道维护状态：

- 通过 WebSocket 到达的实时成交事件。
- 轮询交易场所获取成交历史的定期对账。

如果相同的成交在去重发生之前通过具有不同标识符的两个渠道到达，两者都可能应用于订单。这在以下情况下特别可能发生：

- 在 WebSocket 连接正在建立时运行对账的系统启动期间。
- 网络不稳定导致成交中途重新连接。
- 成交到达速度快于对账周期的高频交易。

以下情况会增加对账竞争条件的可能性：

- **阈值降低**：`open_check_threshold_ms` 和 `inflight_check_threshold_ms` 设置（均默认为 5,000 毫秒）定义了引擎在对差异采取行动之前的等待时间。将这些降低到低于到交易场所的往返延迟会增加在实时事件到达之前通过对账处理成交的机会（反之亦然）。
- **对账频率增加**：将 `open_check_interval_secs` 设置为激进的值（例如 1-2 秒）会增加系统轮询交易场所的频率，从而为实时事件产生更多的竞争条件机会。
- **启动延迟减少**：`reconciliation_startup_delay_secs` 设置（默认 10 秒）为 WebSocket 连接在持续对账开始前稳定提供了时间。减小此值会增加启动窗口期间重复成交的机会。

有关配置详情，请参阅 [持续对账 (Continuous reconciliation)](../how_to/configure_live_trading.md#continuous-reconciliation)。

### 系统行为 (System behavior)

`ExecutionEngine` 在应用每个成交事件之前，通过将订单当前 `filled_qty` (已成交数量) 加上传入的 `last_qty` (本次成交数量) 与原始 `quantity` 进行比较，来检查潜在的超额成交。

`allow_overfills` 配置选项（默认：`False`）控制如何处理超额成交：

| `allow_overfills` | 行为 |
|-------------------|------|
| `False`           | 记录并拒绝成交，保留订单当前状态。 |
| `True`            | 记录警告，应用成交，并在 `overfill_qty` 中跟踪超出的部分。 |

当允许超额成交时，订单的 `overfill_qty` 字段跟踪超出的数量。订单转换为 `FILLED` (已成交) 状态，且 `leaves_qty` (剩余数量) 被强制设为零。

### 重复成交检测 (Duplicate fill detection)

`Order` 模型强制要求每个 `trade_id` 只能应用一次。在 `Order.apply()` 内部，如果传入成交的 `trade_id` 已存在于订单上，则硬性检查会抛出错误。这是防止重复计算执行的不变量。

**核心引擎路径（回测和实时事件处理）**

在核心 `ExecutionEngine`（用于回测和处理实时成交事件）中，在调用 `apply()` 之前，引擎会检查 `Order.is_duplicate_fill()`，它比较：

- `trade_id` (成交 ID)
- `order_side` (订单方向)
- `last_px` (本次成交价格)
- `last_qty` (本次成交数量)

如果所有字段都与现有成交完全匹配，则优雅地跳过该事件并记录警告日志。这避免了对良性的完全重放（例如来自 WebSocket 重新连接）抛出错误。如果 `trade_id` 匹配但其他字段不同（“嘈杂的重放”），则 4 字段检查通过，但由于重复的 `trade_id`，`Order.apply()` 将抛出错误。引擎捕获此错误，记录带有完整上下文的异常并丢弃该成交 - 它不会崩溃。

**实盘对账清理器 (Live reconciliation sanitizer)**

在实盘对账期间，`LiveExecutionEngine` 在生成成交事件 *之前* 仅根据 `trade_id` 进行预过滤。此检查在上述 4 字段检查之前运行。如果成交报告到达时带有订单上已存在的 `trade_id`，则无论价格或数量是否不同，都会将其跳过。当数据确实不同时，会记录警告以提醒操作员潜在的交易场所数据质量问题。

这种预过滤确保了来自交易场所重放或对账竞争的“嘈杂重复项”在触发模型完整性错误之前被过滤掉。如果交易场所确实需要更正成交数据，它应该使用适当的执行报告语义，而不是使用相同的 `trade_id` 重新发送。

对账生成的 `trade_id` 值是对账成交输入的确定性哈希，因此重放对账的重启会产生相同的 `trade_id`，并被此清理器去重，而不是被视为新成交。

### 配置 (Configuration)

对于实盘交易，在 `LiveExecEngineConfig` 中启用超额成交容差：

```python
from nautilus_trader.live.config import LiveExecEngineConfig

config = LiveExecEngineConfig(
    allow_overfills=True,  # 记录警告而不是拒绝
)
```

:::tip
在已知会发出重复成交的交易场所进行交易时，或者预期持仓对账与交易所成交事件发生竞争时，请启用 `allow_overfills=True`。监控日志中的超额成交警告，以识别可能需要特定交易场所处理的模式。
:::

:::warning
当 `allow_overfills=False`（默认值）时，拒绝的成交可能会导致系统与交易场所之间的持仓差异。请使用 [对账 (live.md#execution-reconciliation)](live.md#execution-reconciliation) 功能来检测并解决此类差异。
:::

## 对账报告 (Reconciliation reports)

执行引擎消费实盘交易中适配器发出的四种对账报告变体。每种变体扮演不同的角色，并且当匹配的订单尚未在本地缓存中时，具有不同的回退机制。

| 变体 | 使用案例 | 订单在缓存中缺失时 |
|------|----------|-------------------|
| `OrderStatusReport` (订单状态报告) | 独立的订单状态更新。 | 根据报告创建外部订单；如果状态为 `PartiallyFilled` (部分成交)/`Filled` (已成交)，则根据 `avg_px` (平均成交价)/`filled_qty` (已成交数量) 合成一个推断出的成交。 |
| `FillReport` (成交报告) | 独立的执行结果。 | 根据成交创建外部订单（`OrderType::Market`，数量为 `last_qty`）；然后应用真实的成交，以便保留其 `trade_id` 和 `commission` (佣金)。 |
| `OrderWithFills` (带成交的订单) | 与产生它的成交捆绑在一起的订单状态更新。 | 创建不带推断成交的外部订单；先应用提供的成交；`report.filled_qty` 与提供的 `last_qty` 之和之间的任何剩余差距通过推断成交来填补。 |
| `PositionStatusReport` (仓位状态报告) | 来自交易场所的仓位快照。 | 记录日志；仓位是从成交中派生出来的，而不是在此处引导 (bootstrapped)。 |

### 何时使用每种变体

适配器根据交易场所的线路格式针对给定事件实际交付的内容来选择变体：

- 对于普通订单生命周期更新（已接受、部分成交、已取消、已过期），其中成交详情在不同流上单独到达，请使用 `OrderStatusReport`。
- 对于仅为交易场所发起的平仓提供成交且从未开启用户级订单的交易场所，请使用 `FillReport`（典型的例子是 Hyperliquid 清算：用户收到带有 `liquidation` 元数据的 `userFills` 条目，但订单流中没有条目）。
- 当单个交易场所事件同时映射到状态更新和一个或多个成交，且适配器在同一时间点两者都可用时，请使用 `OrderWithFills`。捆绑让引擎能够应用真实的成交元数据（`trade_id`，`commission`），并仅对剩余数量合成推断出的成交。Binance Futures 通过 `dispatch_exchange_generated_fill` 对交易所生成的 ADL、清算和结算订单使用此方式。

### 外部订单创建 (External order creation)

当报告引用的订单不在缓存中时（交易场所发起的 ADL / 清算 / 结算，由不同进程下达的订单，或尚未在本地观察到的订单），引擎会创建一个 *外部订单 (external order)* 并将所有权路由到：

- 已通过 `register_external_order_claims` 认领该合约的策略，或
- 默认回退到 `EXTERNAL` 策略。

外部订单的 `client_order_id` 在存在时取自报告，否则从 `venue_order_id` 派生。订单被添加到缓存中，注册交易场所订单 ID 索引，并且引擎发出适当的生命周期事件（`OrderAccepted`, `OrderFilled`, `OrderCanceled`, `OrderExpired`），以便仓位通过正常的事件管道进行更新。

这意味着作为单个 `FillReport` 到达的 Hyperliquid 清算和作为捆绑的 `OrderWithFills` 到达的 Binance ADL 都会更新本地仓位，而无需任何策略侧的处理。

## 相关指南 (Related guides)

- [事件 (Events)](events.md) - 订单和仓位事件类型及分发。
- [订单 (Orders)](orders.md) - 订单类型及管理。
- [仓位 (Positions)](positions.md) - 来自执行的仓位跟踪。
- [策略 (Strategies)](strategies.md) - 来自策略的订单提交。
