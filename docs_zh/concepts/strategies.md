# 策略 (Strategies)

策略继承自 `Strategy` 类，并实现其逻辑所需的方法。

**能力**：

- 所有的 `Actor`（执行单元）能力。
- 订单管理。

**与执行单元 (Actors) 的关系**：
`Strategy` 类继承自 `Actor`，这意味着策略可以使用执行单元的所有功能，外加订单管理能力。

:::tip
我们建议在深入研究策略开发之前，先阅读[执行单元 (Actors)](actors.md) 指南。
:::

策略可以添加到任何[环境上下文 (Environment contexts)](architecture.md#environment-contexts) 中的 Nautilus 系统，并且一旦系统启动，策略就会根据其逻辑开始发送命令和接收事件。

利用这些数据摄取、事件处理和订单管理（下文讨论）的构建模块，您可以构建任何类型的策略，包括趋势策略、动量策略、调仓策略、配对策略、做市策略等。

有关所有可用方法，请参阅 [`Strategy` API 参考手册](/docs/python-api-latest/trading.html)。

一个 Nautilus 交易策略主要由两部分组成：

- 策略实现本身，通过继承 `Strategy` 类来定义。
- *可选的* 策略配置，通过继承 `StrategyConfig` 类来定义。

:::tip
一旦定义了策略，相同的源代码可以同时用于回测 (Backtesting) 和实盘交易 (Live Trading)。
:::

策略的主要能力包括：

- 历史数据请求。
- 实盘数据流订阅。
- 设置时间警报或计时器。
- 缓存 (Cache) 访问。
- 投资组合 (Portfolio) 访问。
- 创建和管理订单与持仓。

## 策略实现 (Strategy implementation)

交易策略继承自 `Strategy`，因此您必须 define 构造函数。至少需要初始化基类：

```python
from nautilus_trader.trading.strategy import Strategy

class MyStrategy(Strategy):
    def __init__(self) -> None:
        super().__init__()  # <-- 必须调用超类以初始化策略
```

从这里开始，您可以根据需要实现处理程序 (Handlers)，以根据状态转换和事件执行操作。

:::warning
不要在 `__init__` 构造函数中（即在注册之前）调用 `clock` 和 `logger` 等组件。这是因为系统的时钟和日志子系统尚未初始化。
:::

### 处理程序 (Handlers)

处理程序是 `Strategy` 类中的方法，用于根据事件或状态变化执行操作。这些方法使用 `on_*` 前缀。根据您的策略需求实现其中一个或全部。

存在多个针对相似事件类型的处理程序，以便为您提供粒度控制。使用专用处理程序响应特定事件，或使用通用处理程序响应一系列相关事件（使用典型的 switch 语句逻辑）。系统会按照从最具体到最通用的顺序调用处理程序。

#### 有状态操作 (Stateful actions)

生命周期状态变化会触发这些处理程序。建议：

- 使用 `on_start` 方法初始化您的策略（例如，获取合约、订阅数据）。
- 使用 `on_stop` 方法执行清理任务（例如，取消未平仓订单、平仓、取消订阅数据）。

```python
def on_start(self) -> None:
def on_stop(self) -> None:
def on_resume(self) -> None:
def on_reset(self) -> None:
def on_dispose(self) -> None:
def on_degrade(self) -> None:
def on_fault(self) -> None:
def on_save(self) -> dict[str, bytes]:  # 返回要保存的用户定义状态字典
def on_load(self, state: dict[str, bytes]) -> None:
```

#### 数据处理 (Data handling)

这些处理程序接收数据更新，包括内置市场数据和用户自定义数据。

```python
from nautilus_trader.core import Data
from nautilus_trader.model import OrderBook
from nautilus_trader.model import Bar
from nautilus_trader.model import QuoteTick
from nautilus_trader.model import TradeTick
from nautilus_trader.model import OrderBookDeltas
from nautilus_trader.model import InstrumentClose
from nautilus_trader.model import InstrumentStatus
from nautilus_trader.model import OptionChainSlice
from nautilus_trader.model import OptionGreeks
from nautilus_trader.model.instruments import Instrument

def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
def on_order_book(self, order_book: OrderBook) -> None:
def on_quote_tick(self, tick: QuoteTick) -> None:
def on_trade_tick(self, tick: TradeTick) -> None:
def on_bar(self, bar: Bar) -> None:
def on_instrument(self, instrument: Instrument) -> None:
def on_instrument_status(self, data: InstrumentStatus) -> None:
def on_instrument_close(self, data: InstrumentClose) -> None:
def on_option_greeks(self, greeks: OptionGreeks) -> None:
def on_option_chain(self, chain: OptionChainSlice) -> None:
def on_historical_data(self, data: Data) -> None:
def on_data(self, data: Data) -> None:  # 传递给此处理程序的自定义数据
def on_signal(self, signal: Data) -> None:  # 传递给此处理程序的自定义信号
```

#### 订单管理 (Order management)

这些处理程序接收与订单相关的事件。
`OrderEvent` 类型消息按以下顺序传递给处理程序：

1. 特定处理程序（例如 `on_order_accepted`、`on_order_rejected` 等）
2. `on_order_event(...)`
3. `on_event(...)`

```python
from nautilus_trader.model.events import OrderAccepted
from nautilus_trader.model.events import OrderCanceled
from nautilus_trader.model.events import OrderCancelRejected
from nautilus_trader.model.events import OrderDenied
from nautilus_trader.model.events import OrderEmulated
from nautilus_trader.model.events import OrderEvent
from nautilus_trader.model.events import OrderExpired
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.events import OrderInitialized
from nautilus_trader.model.events import OrderModifyRejected
from nautilus_trader.model.events import OrderPendingCancel
from nautilus_trader.model.events import OrderPendingUpdate
from nautilus_trader.model.events import OrderRejected
from nautilus_trader.model.events import OrderReleased
from nautilus_trader.model.events import OrderSubmitted
from nautilus_trader.model.events import OrderTriggered
from nautilus_trader.model.events import OrderUpdated

def on_order_initialized(self, event: OrderInitialized) -> None:
def on_order_denied(self, event: OrderDenied) -> None:
def on_order_emulated(self, event: OrderEmulated) -> None:
def on_order_released(self, event: OrderReleased) -> None:
def on_order_submitted(self, event: OrderSubmitted) -> None:
def on_order_rejected(self, event: OrderRejected) -> None:
def on_order_accepted(self, event: OrderAccepted) -> None:
def on_order_canceled(self, event: OrderCanceled) -> None:
def on_order_expired(self, event: OrderExpired) -> None:
def on_order_triggered(self, event: OrderTriggered) -> None:
def on_order_pending_update(self, event: OrderPendingUpdate) -> None:
def on_order_pending_cancel(self, event: OrderPendingCancel) -> None:
def on_order_modify_rejected(self, event: OrderModifyRejected) -> None:
def on_order_cancel_rejected(self, event: OrderCancelRejected) -> None:
def on_order_updated(self, event: OrderUpdated) -> None:
def on_order_filled(self, event: OrderFilled) -> None:
def on_order_event(self, event: OrderEvent) -> None:  # 所有的订单事件消息最终都会传递给此处理程序
```

#### 持仓管理 (Position management)

这些处理程序接收与持仓相关的事件。
`PositionEvent` 类型消息按以下顺序传递给处理程序：

1. 特定处理程序（例如 `on_position_opened`、`on_position_changed` 等）
2. `on_position_event(...)`
3. `on_event(...)`

```python
from nautilus_trader.model.events import PositionChanged
from nautilus_trader.model.events import PositionClosed
from nautilus_trader.model.events import PositionEvent
from nautilus_trader.model.events import PositionOpened

def on_position_opened(self, event: PositionOpened) -> None:
def on_position_changed(self, event: PositionChanged) -> None:
def on_position_closed(self, event: PositionClosed) -> None:
def on_position_event(self, event: PositionEvent) -> None:  # 所有的持仓事件消息最终都会传递给此处理程序
```

#### 通用事件处理 (Generic event handling)

此处理程序最终会接收到达策略的所有事件消息，包括那些没有其他特定处理程序的事件。

```python
from nautilus_trader.core.message import Event

def on_event(self, event: Event) -> None:
```

#### 处理程序示例 (Handler example)

以下示例显示了一个典型的 `on_start` 处理程序方法实现（取自 EMA 交叉策略示例）。在这里我们可以看到：

- 指标 (Indicators) 被注册以接收 K 线 (Bar) 更新。
- 历史数据被请求（用于填充指标数据）。
- 实盘数据被订阅。

```python
def on_start(self) -> None:
    """
    策略启动时执行的操作。
    """
    self.instrument = self.cache.instrument(self.instrument_id)
    if self.instrument is None:
        self.log.error(f"无法找到合约 {self.instrument_id}")
        self.stop()  # 将策略转换为 STOPPED 状态
        return

    # 注册待更新的指标
    self.register_indicator_for_bars(self.bar_type, self.fast_ema)
    self.register_indicator_for_bars(self.bar_type, self.slow_ema)

    # 获取历史数据
    self.request_bars(self.bar_type)

    # 订阅实盘数据
    self.subscribe_bars(self.bar_type)
    self.subscribe_quote_ticks(self.instrument_id)
```

### 时钟和计时器 (Clock and timers)

策略可以访问 `Clock`，它提供了许多创建不同时间戳的方法，以及设置时间警报或计时器以触发 `TimeEvent`。

有关所有可用方法，请参阅 [`Clock` API 参考手册](/docs/python-api-latest/common.html)。

#### 当前时间戳 (Current timestamps)

虽然有多种获取当前时间戳的方法，但以下是两个常用的示例方法：

获取当前 UTC 时间戳，作为感知时区的 `pd.Timestamp`：

```python
import pandas as pd


now: pd.Timestamp = self.clock.utc_now()
```

获取当前 UTC 时间戳，作为自 UNIX 纪元以来的纳秒数：

```python
unix_nanos: int = self.clock.timestamp_ns()
```

#### 时间警报 (Time alerts)

可以设置时间警报，这会导致在指定的警报时间向 `on_event` 处理程序分发 `TimeEvent`。在实盘环境中，这可能会略微延迟几微秒。

此示例设置了一个从当前时间起一分钟后触发的时间警报：

```python
import pandas as pd

# 从现在起一分钟后触发 TimeEvent
self.clock.set_time_alert(
    name="MyTimeAlert1",
    alert_time=self.clock.utc_now() + pd.Timedelta(minutes=1),
)
```

#### 计时器 (Timers)

可以设置连续计时器，它会定期生成 `TimeEvent`，直到计时器过期或被取消。

此示例设置了一个从现在开始每分钟触发一次的计时器：

```python
import pandas as pd

# 每分钟触发一次 TimeEvent
self.clock.set_timer(
    name="MyTimer1",
    interval=pd.Timedelta(minutes=1),
)
```

### 缓存访问 (Cache access)

交易系统的核心 `Cache`（缓存）存储数据和执行对象（订单、持仓等）。许多方法都提供了过滤功能。以下是一些基本用例。

#### 获取数据 (Fetching data)

以下示例从缓存中获取数据（假设已分配了某些合约 ID 属性）。如果请求的数据不可用，这些方法将返回 `None`。

```python
last_quote = self.cache.quote_tick(self.instrument_id)
last_trade = self.cache.trade_tick(self.instrument_id)
last_bar = self.cache.bar(bar_type)
```

#### 获取执行对象 (Fetching execution objects)

以下示例显示了如何从缓存中获取单个订单和持仓对象：

```python
order = self.cache.order(client_order_id)
position = self.cache.position(position_id)
```

有关所有可用方法，请参阅 [`Cache` API 参考手册](/docs/python-api-latest/cache.html)。

### 投资组合访问 (Portfolio access)

交易系统的核心 `Portfolio`（投资组合）提供账户和持仓信息。以下显示了可用方法的常规轮廓。

#### 账户和持仓信息 (Account and positional information)

```python
import decimal

from nautilus_trader.accounting.accounts.base import Account
from nautilus_trader.model import Venue
from nautilus_trader.model import Currency
from nautilus_trader.model import Money
from nautilus_trader.model import InstrumentId

def account(self, venue: Venue) -> Account

def balances_locked(self, venue: Venue) -> dict[Currency, Money]
def margins_init(self, venue: Venue) -> dict[Currency, Money]
def margins_maint(self, venue: Venue) -> dict[Currency, Money]
def unrealized_pnls(self, venue: Venue) -> dict[Currency, Money]
def realized_pnls(self, venue: Venue) -> dict[Currency, Money]
def net_exposures(self, venue: Venue) -> dict[Currency, Money]

def unrealized_pnl(self, instrument_id: InstrumentId) -> Money
def realized_pnl(self, instrument_id: InstrumentId) -> Money
def net_exposure(self, instrument_id: InstrumentId) -> Money
def net_position(self, instrument_id: InstrumentId) -> decimal.Decimal

def is_net_long(self, instrument_id: InstrumentId) -> bool
def is_net_short(self, instrument_id: InstrumentId) -> bool
def is_flat(self, instrument_id: InstrumentId) -> bool
def is_completely_flat(self) -> bool
```

有关所有可用方法，请参阅 [`Portfolio` API 参考手册](/docs/python-api-latest/portfolio.html)。

#### 报告和分析 (Reports and analysis)

`Portfolio` 还公开了 `PortfolioAnalyzer`，它接受灵活的数据量（以适应不同的回溯窗口）。分析器跟踪并生成绩效指标 (Metrics) 和统计数据。

请参阅 [`PortfolioAnalyzer` API 参考手册](/docs/python-api-latest/analysis.html) 和 [投资组合统计](portfolio.md#portfolio-statistics) 指南。

### 交易命令 (Trading commands)

以下交易命令可用于订单管理。另请参阅[执行 (Execution)](../concepts/execution.md)指南，了解贯穿系统的完整流程。

#### 提交订单 (Submitting orders)

为了方便起见，每个 `Strategy` 的基类上都提供了一个 `OrderFactory`（订单工厂），减少了创建不同 `Order` 对象所需的样板代码量（尽管如果交易者愿意，仍然可以通过 `Order.__init__(...)` 构造函数直接初始化这些对象）。

`SubmitOrder`（提交订单）或 `SubmitOrderList`（提交订单列表）命令流向哪个组件执行取决于以下几点：

- 如果指定了 `emulation_trigger`（模拟触发器），命令将*首先*发送到 `OrderEmulator`（订单模拟器）。
- 如果指定了 `exec_algorithm_id`（执行算法 ID）（且没有 `emulation_trigger`），命令将*首先*发送到相关的 `ExecAlgorithm`（执行算法）。
- 否则，命令将*首先*发送到 `RiskEngine`（风险引擎）。

此示例提交一个 `LIMIT`（限价）买单用于模拟（见[模拟订单 (Emulated Orders)](orders.md#emulated-orders)）：

```python
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TriggerType
from nautilus_trader.model.orders import LimitOrder


def buy(self) -> None:
    """
    用户简单的买入方法（示例）。
    """
    order: LimitOrder = self.order_factory.limit(
        instrument_id=self.instrument_id,
        order_side=OrderSide.BUY,
        quantity=self.instrument.make_qty(self.trade_size),
        price=self.instrument.make_price(5000.00),
        emulation_trigger=TriggerType.LAST_PRICE,
    )

    self.submit_order(order)
```

:::info
您可以同时指定订单模拟和执行算法。在这种情况下，订单首先发送到 `OrderEmulator`，释放后路由到 `ExecAlgorithm`。
:::

此示例向 TWAP 执行算法提交一个 `MARKET`（市价）买单：

```python
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model import ExecAlgorithmId


def buy(self) -> None:
    """
    用户简单的买入方法（示例）。
    """
    order: MarketOrder = self.order_factory.market(
        instrument_id=self.instrument_id,
        order_side=OrderSide.BUY,
        quantity=self.instrument.make_qty(self.trade_size),
        time_in_force=TimeInForce.FOK,
        exec_algorithm_id=ExecAlgorithmId("TWAP"),
        exec_algorithm_params={"horizon_secs": 20, "interval_secs": 2.5},
    )

    self.submit_order(order)
```

#### 取消订单 (Canceling orders)

订单可以单独取消、批量取消，或取消针对某个合约 (Instrument) 的所有订单（可选侧过滤器）。

如果订单已经*关闭*或处于待取消状态，将记录一条警告。

如果订单当前处于*打开*状态，则状态将变为 `PENDING_CANCEL`。

`CancelOrder`（取消订单）、`CancelAllOrders`（取消所有订单）或 `BatchCancelOrders`（批量取消订单）命令流向哪个组件执行取决于以下几点：

- 如果订单当前处于模拟状态，命令将*首先*发送到 `OrderEmulator`。
- 如果指定了 `exec_algorithm_id`（且没有 `emulation_trigger`），并且订单在本地系统中仍然有效，则命令将*首先*发送到相关的 `ExecAlgorithm`。
- 否则，订单将*首先*发送到 `ExecutionEngine`（执行引擎）。

:::info
任何管理的 GTD 计时器也会在命令离开策略后被取消。
:::

以下显示了如何取消单个订单：

```python
self.cancel_order(order)
```

以下显示了如何取消一批订单：

```python
from nautilus_trader.model.orders import Order


my_order_list: list[Order] = [order1, order2, order3]
self.cancel_orders(my_order_list)
```

以下显示了如何取消所有订单：

```python
self.cancel_all_orders()
```

#### 修改订单 (Modifying orders)

在模拟状态下，或在场内 (Venue) 处于*打开*状态（如果支持）时，可以单独修改订单。

如果订单已经*关闭*或处于待取消状态，将记录一条警告。如果订单当前处于*打开*状态，则状态将变为 `PENDING_UPDATE`。

:::warning
为了使命令有效，至少必须有一个值与原始订单不同。
:::

`ModifyOrder`（修改订单）命令流向哪个组件执行取决于以下几点：

- 如果订单当前处于模拟状态，命令将*首先*发送到 `OrderEmulator`。
- 否则，订单将*首先*发送到 `RiskEngine`。

:::info
一旦订单处于执行算法的控制之下，它就不能由策略直接修改（只能取消）。
:::

以下显示了如何修改场内当前处于*打开*状态的 `LIMIT` 买单的数量：

```python
from nautilus_trader.model import Quantity


new_quantity: Quantity = Quantity.from_int(5)
self.modify_order(order, new_quantity)
```

:::info
价格和触发价格也可以修改（在模拟或场内支持时）。
:::

#### 离场 (Market exit)

`market_exit()` 方法提供了一种平滑退出所有持仓并取消策略所有订单的方法。策略在离场完成后仍保持运行，允许您稍后根据需要重新入场。

```python
self.market_exit()
```

离场过程：

1. 取消该策略所有打开和在途 (In-flight) 的订单。
2. 以市价单平掉所有开仓。
3. 定期检查（每隔 `market_exit_interval_ms`），直到所有订单解析且持仓平掉。
4. 一旦清空持仓 (Flat)，或者达到 `market_exit_max_attempts` 后，调用 `post_market_exit()`。

提供了两个用于自定义逻辑的钩子 (Hooks)：

- `on_market_exit()` - 离场过程开始时调用。
- `post_market_exit()` - 离场过程完成时调用。

```python
class MyStrategy(Strategy):
    def on_market_exit(self) -> None:
        self.log.info("开始执行离场操作...")

    def post_market_exit(self) -> None:
        self.log.info("离场操作已完成")
```

在离场期间，非只减仓 (reduce-only) 订单将被自动拒绝。对于订单列表，如果列表中的任何订单是非只减仓的，为了保留列表语义（例如，具有相互依赖关系的组合订单），整个列表都将被拒绝。

要检查是否正在进行离场（例如，为了跳过下单逻辑），请使用 `is_exiting()`：

```python
def on_quote_tick(self, tick: QuoteTick) -> None:
    if self.is_exiting():
        return  # 离场期间跳过订单逻辑
    # ... 正常的订单逻辑
```

要在策略停止时自动执行离场操作，请设置 `manage_stop=True`：

```python
config = StrategyConfig(manage_stop=True)
```

使用此选项，调用 `stop()` 将首先执行离场操作，一旦清空持仓，再停止策略。

`StrategyConfig` 中的配置选项：

- `manage_stop`（默认：False） - 如果为 True，`stop()` 会在停止前执行离场操作。
- `market_exit_interval_ms`（默认：100） - 离场完成检查之间的时间间隔。
- `market_exit_max_attempts`（默认：100） - 完成离场前的最大检查次数。
- `market_exit_time_in_force`（默认：None/GTC） - 用于平仓的市价单的有效期。
- `market_exit_reduce_only`（默认：True） - 用于平仓的市价单是否应为只减仓。

## 策略配置 (Strategy configuration)

一个独立的配置类提供了关于策略在何处以及如何实例化的充分灵活性。配置通过网络进行序列化，支持分布式回测 (Backtesting) 和远程实盘交易 (Live Trading)。

这是可选项。您可以跳过配置并直接向策略构造函数传递参数。如果您想要进行分布式回测或远程实盘交易，请定义一个配置。

以下是一个配置示例：

```python
from decimal import Decimal
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType
from nautilus_trader.model import InstrumentId
from nautilus_trader.trading.strategy import Strategy


# 配置定义
class MyStrategyConfig(StrategyConfig):
    instrument_id: InstrumentId   # 示例值: "ETHUSDT-PERP.BINANCE"
    bar_type: BarType             # 示例值: "ETHUSDT-PERP.BINANCE-15-MINUTE[LAST]-EXTERNAL"
    fast_ema_period: int = 10
    slow_ema_period: int = 20
    trade_size: Decimal
    order_id_tag: str


# 策略定义
class MyStrategy(Strategy):
    def __init__(self, config: MyStrategyConfig) -> None:
        # 始终初始化父类 Strategy
        # 在此之后，配置被存储并通过 `self.config` 可用
        super().__init__(config)

        # 自定义状态变量
        self.time_started = None
        self.count_of_processed_bars: int = 0

    def on_start(self) -> None:
        self.time_started = self.clock.utc_now()    # 记录策略启动的时间
        self.subscribe_bars(self.config.bar_type)   # 了解如何通过 `self.config` 暴露配置数据

    def on_bar(self, bar: Bar):
        self.count_of_processed_bars += 1           # 更新已处理 K 线的数量


# 使用特定值实例化配置。通过设置：
#   - InstrumentId - 我们参数化策略将交易的合约。
#   - BarType - 我们参数化策略将交易的 K 线数据。
config = MyStrategyConfig(
    instrument_id=InstrumentId.from_str("ETHUSDT-PERP.BINANCE"),
    bar_type=BarType.from_str("ETHUSDT-PERP.BINANCE-15-MINUTE[LAST]-EXTERNAL"),
    trade_size=Decimal(1),
    order_id_tag="001",
)

# 将配置传递给我们的交易策略。
strategy = MyStrategy(config=config)
```

通过 `self.config` 访问配置值。这在以下内容之间提供了清晰的分隔：

- 配置数据（通过 `self.config` 访问）：
  - 包含初始设置，定义策略的工作方式。
  - 示例：`self.config.trade_size`、`self.config.instrument_id`

- 策略状态变量（作为直接属性）：
  - 跟踪策略的任何自定义状态。
  - 示例：`self.time_started`、`self.count_of_processed_bars`

这种分隔使代码更易于理解和维护。

:::note
尽管定义一个只交易单个合约的策略通常是有意义的。但单个策略可以处理的合约数量仅受机器资源限制。
:::

### 托管 GTD 到期 (Managed GTD expiry)

策略可以为有效期为 GTD（*当日有效 Good 'till Date*）的订单管理到期。如果交易所/经纪商不支持此有效期选项，或者由于任何原因您希望由策略来管理此项，这可能是理想的选择。

要使用此选项，请将 `manage_gtd_expiry=True` 传递给您的 `StrategyConfig`。当提交有效期为 GTD 的订单时，策略将自动启动一个内部时间警报。一旦到达内部 GTD 时间警报，订单将被取消（如果尚未*关闭*）。

某些场内（如 Binance Futures）支持 GTD 有效期，因此为了避免在使用 `managed_gtd_expiry` 时发生冲突，您应该在执行客户端配置中设置 `use_gtd=False`。

### 多个策略

如果您打算运行同一策略的多个实例，且带有不同的配置（例如交易不同的合约），那么每个实例都需要一个唯一的策略 ID (Strategy ID) 和订单 ID 标签 (Order ID tag)。

如果未提供 `strategy_id`，平台将根据策略类名和订单 ID 标签构建策略 ID。标签可以通过 `order_id_tag` 提供；否则注册时会分配下一个数字标签，从 `000` 开始。例如，上述配置会产生 `MyStrategy-001` 的策略 ID。

如果同时提供了 `strategy_id` 和 `order_id_tag`，除非 ID 已以该标签结尾，否则 Rust 会将标签附加到运行时策略 ID。例如，`strategy_id=MyStrategy-PRIMARY` 且 `order_id_tag=ABC` 会变成 `MyStrategy-PRIMARY-ABC`。如果省略了 `order_id_tag`，Rust 将使用 `strategy_id` 的最后一个连字符分隔部分作为订单 ID 标签。

:::note
平台具有内置的安全措施：如果两个策略共享重复的策略 ID，注册期间会引发 `RuntimeError`，表明策略 ID 已被注册。
:::

原因在于系统必须能够识别各种命令和事件属于哪个策略。订单 ID 标签还能保持同一个交易者的不同策略所生成的客户端订单 ID 具有唯一性。

:::info Rust 实现 (Rust implementation)
Rust 将 `StrategyConfig` 视为不可变的构造输入。运行时 `StrategyId` 带有订单 ID 标签，符合 Python/Cython 行为。这使得执行单元注册、客户端订单 ID 生成、订单列表 ID 生成和持仓 ID 生成通过 `strategy_id.get_tag()` 保持一致。

如果省略 `strategy_id`，`order_id_tag` 将覆盖生成的后缀，例如 `MyStrategy-ABC`。
:::

有关更多详细信息，请参阅 [`StrategyId` API 参考手册](/docs/python-api-latest/model/identifiers.html)。

## 相关指南

- [执行单元 (Actors)](actors.md) - 策略扩展的基类。
- [事件 (Events)](events.md) - 事件类型和处理程序分发。
- [订单 (Orders)](orders.md) - 策略中的订单类型和管理。
- [回测 (Backtesting)](backtesting.md) - 使用历史数据测试策略。
