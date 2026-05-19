# 缓存 (Cache)

`缓存 (Cache)` 是一个中央内存数据库，存储并管理所有与交易相关的数据，从市场数据到订单历史，再到自定义计算结果。

缓存服务于多个目的：

1. **存储市场数据 (Market data)**：
   - 存储最近的市场历史（例如，订单簿、报价、成交、K线）。
   - 让您可以访问策略所需的当前和历史市场数据。

2. **跟踪交易数据**：
   - 维护完整的 `订单 (Order)` 历史和当前执行状态。
   - 跟踪所有 `持仓 (Position)` 和 `账户 (Account)` 信息。
   - 存储 `合约 (Instrument)` 定义和 `货币 (Currency)` 信息。

3. **存储自定义数据**：
   - 您可以在 `缓存 (Cache)` 中存储任何用户定义的对象或数据以便后续使用。
   - 实现不同策略之间的数据共享。

## 缓存工作原理

**内置类型**：

- 系统会自动将流经的数据添加到 `缓存 (Cache)` 中。
- 在实盘上下文中，引擎异步应用更新，因此您可能会在事件发生与其出现在 `缓存 (Cache)` 之间看到短暂的延迟。
- 对于报价 (Quotes)、成交 (Trades) 和 K线 (Bars)，`DataEngine` 在发布给订阅者之前先将其写入 `缓存 (Cache)`，因此当您的处理程序运行时，缓存中已经有了最新值。订单簿增量 (Order book deltas) 和深度快照直接发布而不写入缓存；订单簿状态通过 `BookUpdater` 订阅单独维护：

```mermaid
flowchart LR
    data[数据]
    engine[数据引擎 (DataEngine)]
    cache[缓存 (Cache)]
    callback["策略回调:<br/>on_quote_tick(...)"]

    data --> engine --> cache --> callback
```

有关完整的分步追踪，请参阅 [数据流：报价逐笔数据的生命周期 (Data flow: life of a quote tick)](architecture.md#data-flow-life-of-a-quote-tick)。

### 基础示例

在策略中，您可以通过 `self.cache` 访问 `缓存 (Cache)`。以下是一个典型示例：

:::note 注意
在 `Strategy` 类中，`self` 指代策略实例。
:::

```python
def on_bar(self, bar: Bar) -> None:
    # 当前 K线由参数 'bar' 提供

    # 从缓存中获取历史 K线
    last_bar = self.cache.bar(self.bar_type, index=0)        # 最近的 K线（实际上与 'bar' 参数相同）
    previous_bar = self.cache.bar(self.bar_type, index=1)    # 上一根 K线
    third_last_bar = self.cache.bar(self.bar_type, index=2)  # 倒数第三根 K线

    # 获取当前持仓信息
    if self.last_position_opened_id is not None:
        position = self.cache.position(self.last_position_opened_id)
        if position.is_open:
            # 检查持仓详情
            current_pnl = position.unrealized_pnl

    # 获取我们合约的所有活动订单
    open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
```

## 配置

使用 `CacheConfig` 类来配置 `缓存 (Cache)` 的行为和容量。您可以将此配置提供给 `BacktestEngine` 或 `TradingNode`，具体取决于您的 [环境上下文 (Environment context)](architecture.md#environment-contexts)。

以下是配置 `缓存 (Cache)` 的基础示例：

```python
from nautilus_trader.config import CacheConfig, BacktestEngineConfig, TradingNodeConfig

# 用于回测
engine_config = BacktestEngineConfig(
    cache=CacheConfig(
        tick_capacity=10_000,  # 每个合约存储最后 10,000 条逐笔数据
        bar_capacity=5_000,    # 每种 K线类型存储最后 5,000 根 K线
    ),
)

# 用于实盘交易
node_config = TradingNodeConfig(
    cache=CacheConfig(
        tick_capacity=10_000,
        bar_capacity=5_000,
    ),
)
```

:::tip 提示
默认情况下，`缓存 (Cache)` 为每种 K线类型保留最后 10,000 根 K线，并为每个合约保留 10,000 条成交逐笔数据。这些限制在内存使用和数据可用性之间提供了良好的平衡。如果您的策略需要更多历史数据，请增加这些值。
:::

### 配置选项

`CacheConfig` 类支持以下参数：

```python
from nautilus_trader.config import CacheConfig

cache_config = CacheConfig(
    database: DatabaseConfig | None = None,  # 用于持久化的数据库配置
    encoding: str = "msgpack",               # 数据编码格式（'msgpack' 或 'json'）
    timestamps_as_iso8601: bool = False,     # 将时间戳存储为 ISO8601 字符串
    buffer_interval_ms: int | None = None,   # 批量操作的缓冲间隔
    bulk_read_batch_size: int | None = None, # 批量读取的批次大小（例如 MGET）
    use_trader_prefix: bool = True,          # 在键中使用交易者前缀
    use_instance_id: bool = False,           # 在键中包含实例 ID
    flush_on_start: bool = False,            # 启动时清空数据库
    drop_instruments_on_reset: bool = True,  # 重置时清除合约
    tick_capacity: int = 10_000,             # 每个合约存储的最大逐笔数据量
    bar_capacity: int = 10_000,              # 每种 K线类型存储的最大 K线数量
)
```

:::note 注意
每种 K线类型维护其独立的容量。例如，如果您同时使用 1 分钟和 5 分钟 K线，每种类型最多存储 `bar_capacity` 根 K线。当达到 `bar_capacity` 时，`缓存 (Cache)` 会自动移除最旧的数据。
:::

### 数据库配置 (Database configuration)

为了在系统重启之间保持数据，您可以配置数据库后端。

何时使用持久化 (Persistence) 是有用的？

- **长期运行的系统**：如果您希望数据在系统重启、升级或意外故障后仍然存在，配置数据库有助于从中断处恢复。
- **历史洞察**：当您需要保留过去的交易数据进行详细的盘后分析或审计时。
- **多节点或分布式设置**：如果多个服务或节点需要访问相同的状态，持久化存储有助于确保数据共享和一致性。

```python
from nautilus_trader.config import DatabaseConfig

config = CacheConfig(
    database=DatabaseConfig(
        type="redis",            # 数据库类型
        host="localhost",        # 数据库主机
        port=6379,               # 数据库端口
        connection_timeout=2,    # 连接超时（秒）
        response_timeout=2,      # 响应超时（秒）
    ),
)
```

## 使用缓存

### 访问市场数据

`缓存 (Cache)` 提供了访问订单簿 (Order books)、报价 (Quotes)、成交 (Trades) 和 K线 (Bars) 的完整接口。缓存中的所有市场数据都使用反向索引，因此最近的条目位于索引 0。

#### K线 (Bar) 访问

```python
# 获取某种 K线类型的所有缓存 K线列表
bars = self.cache.bars(bar_type)  # 返回 list[Bar] 或空列表（如果未找到）

# 获取最近的一根 K线
latest_bar = self.cache.bar(bar_type)  # 返回 Bar 或 None（如果不存在）

# 通过索引获取特定的历史 K线（0 = 最近）
second_last_bar = self.cache.bar(bar_type, index=1)  # 返回 Bar 或 None

# 检查 K线是否存在并获取数量
bar_count = self.cache.bar_count(bar_type)  # 返回指定 K线类型的缓存数量
has_bars = self.cache.has_bars(bar_type)    # 返回布尔值，指示是否存在指定类型的 K线
```

#### 报价逐笔数据 (Quote ticks)

```python
# 获取报价
quotes = self.cache.quote_ticks(instrument_id)                     # 返回 list[QuoteTick] 或空列表
latest_quote = self.cache.quote_tick(instrument_id)                # 返回 QuoteTick 或 None
second_last_quote = self.cache.quote_tick(instrument_id, index=1)  # 返回 QuoteTick 或 None

# 检查报价可用性
quote_count = self.cache.quote_tick_count(instrument_id)  # 返回此合约在缓存中的报价数量
has_quotes = self.cache.has_quote_ticks(instrument_id)    # 返回布尔值，指示此合约是否存在报价
```

#### 成交逐笔数据 (Trade ticks)

```python
# 获取成交
trades = self.cache.trade_ticks(instrument_id)         # 返回 list[TradeTick] 或空列表
latest_trade = self.cache.trade_tick(instrument_id)    # 返回 TradeTick 或 None
second_last_trade = self.cache.trade_tick(instrument_id, index=1)  # 返回 TradeTick 或 None

# 检查成交可用性
trade_count = self.cache.trade_tick_count(instrument_id)  # 返回此合约在缓存中的成交数量
has_trades = self.cache.has_trade_ticks(instrument_id)    # 返回布尔值，指示是否存在成交
```

#### 订单簿 (Order book)

```python
# 获取当前订单簿
book = self.cache.order_book(instrument_id)  # 返回 OrderBook 或 None

# 检查订单簿是否存在
has_book = self.cache.has_order_book(instrument_id)  # 返回布尔值，指示是否存在订单簿

# 获取订单簿更新次数
update_count = self.cache.book_update_count(instrument_id)  # 返回接收到的更新次数
```

#### 价格访问

```python
from nautilus_trader.core.rust.model import PriceType

# 根据类型获取当前价格；返回 Price 或 None。
price = self.cache.price(
    instrument_id=instrument_id,
    price_type=PriceType.MID,  # 选项: BID, ASK, MID, LAST
)
```

#### K线类型 (Bar types)

```python
from nautilus_trader.core.rust.model import PriceType, AggregationSource

# 获取合约的所有可用 K线类型；返回 list[BarType]。
bar_types = self.cache.bar_types(
    instrument_id=instrument_id,
    price_type=PriceType.LAST,  # 选项: BID, ASK, MID, LAST
    aggregation_source=AggregationSource.EXTERNAL,
)
```

#### 简单示例

```python
class MarketDataStrategy(Strategy):
    def on_start(self):
        # 订阅 1 分钟 K线
        self.bar_type = BarType.from_str(f"{self.instrument_id}-1-MINUTE-LAST-EXTERNAL")  # 示例 instrument_id = "EUR/USD.FXCM"
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar) -> None:
        bars = self.cache.bars(self.bar_type)[:3]
        if len(bars) < 3:   # 等待直到至少有 3 根 K线
            return

        # 访问最后 3 根 K线进行分析
        current_bar = bars[0]    # 最近的 K线
        prev_bar = bars[1]       # 倒数第二根 K线
        prev_prev_bar = bars[2]  # 倒数第三根 K线

        # 获取最新报价和成交
        latest_quote = self.cache.quote_tick(self.instrument_id)
        latest_trade = self.cache.trade_tick(self.instrument_id)

        if latest_quote is not None:
            current_spread = latest_quote.ask_price - latest_quote.bid_price
            self.log.info(f"Current spread: {current_spread}")
```

### 交易对象

`缓存 (Cache)` 提供了对系统中所有交易对象的访问，包括：

- 订单 (Orders)
- 持仓 (Positions)
- 账户 (Accounts)
- 合约 (Instruments)

#### 订单 (Orders)

您可以通过多种方法访问和查询订单，并支持按场馆 (Venue)、策略、合约和订单方向进行灵活过滤。

##### 基础订单访问

```python
# 通过客户端订单 ID 获取特定订单
order = self.cache.order(ClientOrderId("O-123"))

# 获取系统中的所有订单
orders = self.cache.orders()

# 根据特定标准过滤订单
orders_for_venue = self.cache.orders(venue=venue)                       # 特定场馆的所有订单
orders_for_strategy = self.cache.orders(strategy_id=strategy_id)        # 特定策略的所有订单
orders_for_instrument = self.cache.orders(instrument_id=instrument_id)  # 特定合约的所有订单
```

##### 订单状态查询

```python
# 根据当前状态获取订单
open_orders = self.cache.orders_open()                       # 目前在场馆处于活动状态的订单
closed_orders = self.cache.orders_closed()                   # 已完成生命周期的订单
emulated_orders = self.cache.orders_emulated()               # 由系统在本地模拟的订单
inflight_orders = self.cache.orders_inflight()               # 已提交（或修改）至场馆但尚未确认的订单
local_active_orders = self.cache.orders_active_local()       # 仍在本地管理的订单（已初始化、已模拟或已释放）

# 检查特定订单状态
exists = self.cache.order_exists(client_order_id)            # 检查缓存中是否存在给定 ID 的订单
is_open = self.cache.is_order_open(client_order_id)          # 检查订单目前是否开启
is_closed = self.cache.is_order_closed(client_order_id)      # 检查订单是否关闭
is_emulated = self.cache.is_order_emulated(client_order_id)  # 检查订单是否正在本地模拟
is_inflight = self.cache.is_order_inflight(client_order_id)  # 检查订单是否已提交或修改但尚未确认
is_active_local = self.cache.is_order_active_local(client_order_id)  # 检查订单是否仍在本地管理
```

##### 订单统计

```python
# 获取不同状态订单的数量
open_count = self.cache.orders_open_count()                  # 开启订单数量
closed_count = self.cache.orders_closed_count()              # 关闭订单数量
emulated_count = self.cache.orders_emulated_count()          # 模拟订单数量
inflight_count = self.cache.orders_inflight_count()          # 在途 (Inflight) 订单数量
local_active_count = self.cache.orders_active_local_count()  # 本地活动订单数量（已初始化、已模拟或已释放）
total_count = self.cache.orders_total_count()                # 系统中的订单总数

# 获取经过过滤的订单数量
buy_orders_count = self.cache.orders_open_count(side=OrderSide.BUY)  # 目前开启的买单数量
venue_orders_count = self.cache.orders_total_count(venue=venue)      # 给定场馆的订单总数
```

#### 持仓 (Positions)

`缓存 (Cache)` 维护了所有持仓的记录，并提供了多种查询方式。

##### 持仓访问

```python
# 通过 ID 获取特定持仓
position = self.cache.position(PositionId("P-123"))

# 根据状态获取持仓
all_positions = self.cache.positions()            # 系统中的所有持仓
open_positions = self.cache.positions_open()      # 所有当前开启的持仓
closed_positions = self.cache.positions_closed()  # 所有已关闭的持仓

# 根据各种标准过滤持仓
venue_positions = self.cache.positions(venue=venue)                       # 特定场馆的持仓
instrument_positions = self.cache.positions(instrument_id=instrument_id)  # 特定合约的持仓
strategy_positions = self.cache.positions(strategy_id=strategy_id)        # 特定策略的持仓
long_positions = self.cache.positions(side=PositionSide.LONG)             # 所有多头持仓
```

##### 持仓状态查询

```python
# 检查持仓状态
exists = self.cache.position_exists(position_id)        # 检查是否存在给定 ID 的持仓
is_open = self.cache.is_position_open(position_id)      # 检查持仓是否开启
is_closed = self.cache.is_position_closed(position_id)  # 检查持仓是否关闭

# 获取持仓与订单的关系
orders = self.cache.orders_for_position(position_id)       # 与特定持仓相关的所有订单
position = self.cache.position_for_order(client_order_id)  # 查找与特定订单关联的持仓
```

##### 持仓统计

```python
# 获取不同状态的持仓数量
open_count = self.cache.positions_open_count()      # 目前开启的持仓数量
closed_count = self.cache.positions_closed_count()  # 已关闭的持仓数量
total_count = self.cache.positions_total_count()    # 系统中的持仓总数

# 获取经过过滤的持仓数量
long_positions_count = self.cache.positions_open_count(side=PositionSide.LONG)              # 开启的多头持仓数量
instrument_positions_count = self.cache.positions_total_count(instrument_id=instrument_id)  # 给定合约的持仓数量
```

#### 账户 (Accounts)

```python
# 访问账户信息
account = self.cache.account(account_id)       # 通过 ID 检索账户
account = self.cache.account_for_venue(venue)  # 检索特定场馆的账户
account_id = self.cache.account_id(venue)      # 检索场馆的账户 ID
```

#### 合约和货币 (Instruments and currencies)

##### 合约 (Instruments)

```python
# 获取合约信息
instrument = self.cache.instrument(instrument_id) # 通过 ID 检索特定合约
all_instruments = self.cache.instruments()        # 检索缓存中的所有合约

# 获取经过过滤的合约
venue_instruments = self.cache.instruments(venue=venue)              # 特定场馆的合约
instruments_by_underlying = self.cache.instruments(underlying="ES")  # 按标的资产检索合约

# 获取合约标识符
instrument_ids = self.cache.instrument_ids()                   # 获取所有合约 ID
venue_instrument_ids = self.cache.instrument_ids(venue=venue)  # 获取特定场馆的合约 ID
```

### 清除缓存数据 (Purging cached data)

长期运行的会话会积累关闭的订单、关闭的持仓、账户事件和未使用的合约。缓存公开了针对性的和批量的清除方法，以便策略和实盘交易引擎可以在不重启系统的情况下限制内存增长。

#### 针对性清除

使用这些方法删除单个实体。如果实体仍处于活动状态，则拒绝清除。

- `cache.purge_order(client_order_id)`：移除订单及所有以该订单为键的索引条目。跳过开启的订单。
- `cache.purge_position(position_id)`：移除持仓、其快照及以该持仓为键的索引条目。跳过开启的持仓。
- `cache.purge_instrument(instrument_id)`：移除合约及每个合约的映射（订单簿、报价、成交、标记/指数/资金价格、合约状态、希腊字母，以及引用该合约的 K线）。当任何关联订单处于非终端状态（任何尚未达到关闭状态的订单，包括已初始化、已提交、已接受、已模拟、已释放和在途订单）或任何关联持仓未关闭时，跳过清除。

```python
class HousekeepingStrategy(Strategy):
    def on_start(self) -> None:
        # 删除不再在观察列表 (Watchlist) 中的合约。
        for instrument_id in self.cache.instrument_ids(venue=self.venue):
            if instrument_id not in self.watchlist:
                self.cache.purge_instrument(instrument_id)
```

:::warning 警告
`purge_instrument` 旨在用于具有自己生命周期逻辑的执行器和策略，以决定何时不再需要某个合约。清除另一个组件仍依赖的合约会导致合约查找失败并丢失市场数据历史。活动订阅属于数据引擎，因此如果您不再需要更新，请在清除之前取消订阅。
:::

#### 批量清除

使用这些方法根据时间清理旧条目。它们接收当前时间戳和以秒为单位的缓冲或回溯窗口。

- `cache.purge_closed_orders(ts_now, buffer_secs)`：关闭时间早于 `buffer_secs` 的已关闭订单。
- `cache.purge_closed_positions(ts_now, buffer_secs)`：关闭时间早于 `buffer_secs` 的已关闭持仓。
- `cache.purge_account_events(ts_now, lookback_secs)`：早于 `lookback_secs` 的账户状态事件。值为 `0` 将清除所有事件。

#### 实盘交易中的自动清除

`LiveExecEngineConfig` 通过定时器调度批量清除。设置间隔以启用循环，设置缓冲或回溯以控制保护最近条目的时间。以下默认值适用于大多数实盘会话：

```python
from nautilus_trader.config import LiveExecEngineConfig

exec_engine = LiveExecEngineConfig(
    purge_closed_orders_interval_mins=15,
    purge_closed_orders_buffer_mins=60,
    purge_closed_positions_interval_mins=15,
    purge_closed_positions_buffer_mins=60,
    purge_account_events_interval_mins=15,
    purge_account_events_lookback_mins=60,
)
```

60 分钟的缓冲区可在对账期间保留最近的活动，同时修剪长尾增长。对于 HFT（高频交易）会话，请调低这些值；如果您需要更长的分析历史回溯，请调高这些值。有关完整参数参考，请参阅 [配置实盘交易：内存管理 (Configure live trading: memory management)](../how_to/configure_live_trading.md)。

:::note 注意
合约清除没有自动循环，因为删除合约的正确时间取决于策略状态而非时间。请从拥有合约生命周期的执行器或策略中调用 `cache.purge_instrument`。
:::

---

### 自定义数据

除了内置的市场数据和交易对象外，`缓存 (Cache)` 还可以存储和检索自定义数据类型。使用它在系统组件（主要是执行器和策略）之间共享任何用户定义的数据。

#### 基础存储和检索

```python
# 在策略方法内部调用此代码（`self` 指代策略）

# 存储数据
self.cache.add(key="my_key", value=b"some binary data")

# 检索数据
stored_data = self.cache.get("my_key")  # 返回 bytes 或 None
```

对于更复杂的用例，`缓存 (Cache)` 可以存储继承自 `nautilus_trader.core.Data` 基类的自定义数据对象。

:::warning 警告
`缓存 (Cache)` 并非设计为完整数据库的替代品。对于大数据集或复杂的查询需求，请考虑使用专门的数据库系统。
:::

## 最佳实践和常见问题

### 缓存 vs. 投资组合 (Portfolio) 的使用

在 NautilusTrader 中，`缓存 (Cache)` 和 `投资组合 (Portfolio)` 组件服务于不同但互补的目的：

**缓存 (Cache)**：

- 维护交易系统的历史知识和当前状态。
- 当本地状态改变时立即更新（例如，在提交之前初始化订单）。
- 当外部事件发生时异步更新（例如，当订单成交时）。
- 提供交易活动和市场数据的完整历史。
- 将策略接收到的每个事件都保留在缓存中。

**投资组合 (Portfolio)**：

- 聚合持仓、风险敞口和账户信息。
- 提供不带历史记录的当前状态。

**示例**：

```python
class MyStrategy(Strategy):
    def on_position_changed(self, event: PositionEvent) -> None:
        # 当您需要历史视角时使用缓存
        position_history = self.cache.position_snapshots(event.position_id)

        # 当您需要实时的当前状态时使用投资组合
        current_exposure = self.portfolio.net_exposure(event.instrument_id)
```

### 缓存 vs. 策略变量

在 `缓存 (Cache)` 中存储数据还是使用策略变量取决于您的具体需求：

**缓存存储**：

- 用于需要在策略之间共享的数据。
- 最适用于需要在系统重启之间保持的数据。
- 作为所有组件均可访问的中央数据库。
- 理想用于需要在策略重置后继续存在的数据。

**策略变量**：

- 用于策略特定的计算。
- 更适用于临时值和中间结果。
- 提供更快的访问速度和更好的封装。
- 最适用于仅您的策略需要的数据。

**示例**：

以下示例展示了如何在 `缓存 (Cache)` 中存储数据，以便多个策略可以访问相同的信息。

```python
import pickle

class MyStrategy(Strategy):
    def on_start(self):
        # 准备要与其他策略共享的数据
        shared_data = {
            "last_reset": self.clock.timestamp_ns(),
            "trading_enabled": True,
            # 包含您希望其他策略读取的任何其他字段
        }

        # 使用描述性键将其存储在缓存中
        # 这样，多个策略可以调用 self.cache.get("shared_strategy_info")
        # 来检索相同的数据
        self.cache.add("shared_strategy_info", pickle.dumps(shared_data))
```

另一个策略可以按如下方式检索缓存的数据：

```python
import pickle

class AnotherStrategy(Strategy):
    def on_start(self):
        # 使用相同的键加载共享数据
        data_bytes = self.cache.get("shared_strategy_info")
        if data_bytes is not None:
            shared_data = pickle.loads(data_bytes)
            self.log.info(f"Shared data retrieved: {shared_data}")
```

## 相关指南

- [数据 (Data)](data.md) - 存储在缓存中的数据类型。
- [策略 (Strategies)](strategies.md) - 策略访问缓存以获取市场数据和状态。
- [报告 (Reports)](reports.md) - 从缓存数据生成报告。
