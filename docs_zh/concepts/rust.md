# Rust

Nautilus 在 `crates/` 目录下提供了一套完整的 Rust 实现。你可以无需 Python，直接使用 Rust 编写 Actor、策略 (Strategy)、运行回测 (Backtest) 以及进行实盘交易 (Live trading)。领域模型 (Domain model) 在所有路径中共享，且 v2 PyO3 路径直接在 Rust 引擎上运行 Python 策略。

:::warning
Rust API 仍处于活跃开发阶段。方法签名和 Trait 要求可能会在版本之间发生变化。
:::

## 系统实现 (System implementations)

Nautilus 具有三种实现。了解每种实现的定位有助于你根据使用场景选择最合适的一种。

- **v1 遗留版本 (v1 legacy)**：位于 `nautilus_trader/` 下的 Cython/Python 类。功能最为齐全，组件覆盖面最广。
- **v2 Rust**：位于 `crates/` 下的纯 Rust 实现。无需 Python 环境即可运行。
- **v2 PyO3**：通过 PyO3 绑定在 Rust 核心引擎上运行的 Python 用户组件（Actor、策略）。结合了 Python 的易用性与 Rust 引擎的高性能。

### 功能矩阵 (Capability matrix)

| 组件 | v1 遗留版本 (Cython) | v2 Rust | v2 PyO3 (基于 Rust 的 Python) |
|-----------------------|--------------------|----------------|--------------------------|
| 策略 (Strategy) | ✓ | ✓ | ✓ |
| Actor | ✓ | ✓ | ✓ |
| 数据引擎 (DataEngine) | ✓ | ✓ | ✓ |
| 执行引擎 (ExecutionEngine) | ✓ | ✓ | ✓ |
| 风险引擎 (RiskEngine) | ✓ | ✓ | ✓ |
| 回测引擎 (BacktestEngine) | ✓ | ✓ | ✓ |
| 回测节点 (BacktestNode) | ✓ | ✓ | ✓ |
| 实盘节点 (LiveNode) | ✓ | ✓ | ✓ |
| 订单模拟器 (OrderEmulator) | ✓ | ✓ | ✓ |
| 撮合引擎 (Matching engine) | ✓ | ✓ | ✓ |
| 投资组合 (Portfolio) | ✓ | ✓ | ✓ |
| 账户 (Accounts) | ✓ | ✓ | ✓ |
| 缓存 (Cache) | ✓ | ✓ | ✓ |
| 消息总线 (MessageBus) | ✓ | ✓ | ✓ |
| 数据目录 (Data catalog) | ✓ | ✓ | ✓ |
| 指标 (Indicators) | ✓ | ✓ | ✓ |
| 执行算法 (Exec algorithms) | TWAP | TWAP | TWAP |
| 控制器 (Controller) | ✓ | - | - |
| 绩效报告 (Tearsheets) | ✓ | - | - |
| 配置序列化 (Config serialization) | ✓ | - | - |

### 适配器 (Adapters)

| 适配器 | v1 遗留版本 (Cython) | v2 Rust | v2 PyO3 |
|---------------------|--------------------|---------|---------|
| Architect AX | ✓ | ✓ | ✓ |
| Betfair | ✓ | ✓ | ✓ |
| Binance | ✓ | ✓ | ✓ |
| BitMEX | ✓ | ✓ | ✓ |
| Bybit | ✓ | ✓ | ✓ |
| Databento | ✓ | ✓ | ✓ |
| Deribit | ✓ | ✓ | ✓ |
| dYdX | ✓ | ✓ | ✓ |
| Hyperliquid | ✓ | ✓ | ✓ |
| Interactive Brokers | ✓ | - | - |
| Kraken | ✓ | ✓ | ✓ |
| OKX | ✓ | ✓ | ✓ |
| Polymarket | ✓ | ✓ | ✓ |
| Sandbox | ✓ | ✓ | ✓ |
| Tardis | ✓ | ✓ | ✓ |

### 路径选择 (Choosing a path)

- **v1 遗留版本** 目前最为完整。如果你需要控制器 (Controller)、绩效报告 (Tearsheets)、盈透证券 (Interactive Brokers) 或配置序列化功能，请使用此版本。
- **v2 Rust** 在没有 Python 运行时的环境下提供原生性能。所有的核心交易功能都已可用。适用于对延迟敏感的部署，或者更倾向于使用编译型语言的团队。
- **v2 PyO3**：Python 用户组件（Actor、策略）在 Rust 核心引擎上运行，在保持 Python 开发体验的同时，利用 Rust 的性能进行数据处理和执行。

## 项目设置 (Project setup)

Nautilus 的 Crate 已发布到 [crates.io](https://crates.io/crates/nautilus-backtest)。将它们添加到你的 `Cargo.toml` 中：

```toml
[dependencies]
nautilus-backtest = "0.55"
nautilus-common = "0.55"
nautilus-execution = "0.55"
nautilus-model = { version = "0.55", features = ["stubs"] }
nautilus-trading = { version = "0.55", features = ["examples"] }

anyhow = "1"
log = "0.4"
```

对于实盘交易，请添加实盘 Crate 以及对应交易场所的适配器：

```toml
[dependencies]
nautilus-live = "0.55"
nautilus-okx = "0.55"
```

要跟踪最新的开发分支，请将所有 Nautilus 依赖项指向同一个 Git 源，以避免 crates.io 版本与 Git 版本之间的类型不匹配：

```toml
[dependencies]
nautilus-backtest = { git = "https://github.com/nautechsystems/nautilus_trader.git", branch = "develop" }
nautilus-common = { git = "https://github.com/nautechsystems/nautilus_trader.git", branch = "develop" }
nautilus-execution = { git = "https://github.com/nautechsystems/nautilus_trader.git", branch = "develop" }
nautilus-model = { git = "https://github.com/nautechsystems/nautilus_trader.git", branch = "develop", features = ["stubs"] }
nautilus-trading = { git = "https://github.com/nautechsystems/nautilus_trader.git", branch = "develop", features = ["examples"] }
```

支持的最低 Rust 版本 (MSRV) 为 **1.95.0**。

### 特性标志 (Feature flags)

| 标志 | Crate | 作用 |
|------------------|---------------------|---------------------------------------------------------------|
| `high-precision` | `nautilus-model` | 16 位固定精度（默认为 9 位）。加密货币交易必需。 |
| `stubs` | `nautilus-model` | 测试合约存根 (`audusd_sim` 等)。 |
| `examples` | `nautilus-trading` | 示例策略 (`EmaCross`, `GridMarketMaker`)。 |
| `streaming` | `nautilus-backtest` | 通过 `BacktestNode` 进行基于目录的数据流传输。 |
| `defi` | `nautilus-model` | DeFi 数据类型。包含 `high-precision`。 |

:::tip
标准的 9 位精度可以处理大多数传统金融合约。对于价格可能有很多小数位（例如 `0.00000001`）的加密货币交易所，请启用 `high-precision`。
:::

## Actor

Actor 接收市场数据、自定义数据/信号和系统事件，但不管理订单。实现 `DataActor` trait，并通过 `Deref`/`DerefMut` 将你的结构体绑定到 `DataActorCore`。你的结构体还必须实现 `Debug`（这是 `Component` 通用实现的要求）。核心组件直接在你的结构体上提供了订阅方法、缓存访问和时钟访问。

### 处理器方法 (Handler methods)

通过重写 `DataActor` trait 上的任何处理器来接收相应的数据或事件。所有处理器都有默认的空实现，因此你只需重写所需的部分。

| 处理器 | 接收内容 |
|------------------------|---------------------------|
| `on_start` | Actor 已启动。 |
| `on_stop` | Actor 已停止。 |
| `on_quote` | `QuoteTick` |
| `on_trade` | `TradeTick` |
| `on_bar` | `Bar` |
| `on_book_deltas` | `OrderBookDeltas` |
| `on_book` | `OrderBook` (按时间间隔) |
| `on_instrument` | `InstrumentAny` |
| `on_mark_price` | `MarkPriceUpdate` |
| `on_index_price` | `IndexPriceUpdate` |
| `on_funding_rate` | `FundingRateUpdate` |
| `on_option_greeks` | `OptionGreeks` |
| `on_option_chain` | `OptionChainSlice` |
| `on_instrument_status` | `InstrumentStatus` |
| `on_order_filled` | `OrderFilled` |
| `on_order_canceled` | `OrderCanceled` |
| `on_time_event` | `TimeEvent` |

有关分步演练，请参阅 [编写 Actor (Rust)](../how_to/write_rust_actor.md) 操作指南。有关完整示例，请参阅 [`BookImbalanceActor`](https://github.com/nautechsystems/nautilus_trader/tree/develop/crates/trading/src/examples/actors/imbalance)。

## 策略 (Strategies)

策略通过订单管理功能扩展了 Actor。需要同时实现 `DataActor`（用于数据处理）和 `Strategy`（用于访问 `StrategyCore`）。`StrategyCore` 封装了 `DataActorCore`，并增加了 `OrderFactory`（订单工厂）、`OrderManager`（订单管理器）和投资组合集成。

### 订单管理 (Order management)

`Strategy` trait 通过 `StrategyCore` 提供订单相关方法：

| 方法 | 操作 |
|-----------------------|-------------------------------------------|
| `submit_order` | 向交易场所提交新订单。 |
| `submit_order_list` | 提交一组关联订单。 |
| `modify_order` | 修改价格、数量或触发价格。 |
| `cancel_order` | 取消特定订单。 |
| `cancel_orders` | 取消一组经过过滤的订单。 |
| `cancel_all_orders` | 取消合约的所有订单。 |
| `close_position` | 通过市价单平仓。 |
| `close_all_positions` | 关闭所有未平仓头寸。 |

`OrderFactory`（通过 `self.core.order_factory()` 访问）用于构建订单对象：`market`（市价单）、`limit`（限价单）、`stop_market`（止损市价单）、`stop_limit`（止损限价单）、`market_if_touched`（触及市价单）、`limit_if_touched`（触及限价单）以及 `trailing_stop_market`（追踪止损市价单）。

有关分步演练，请参阅 [编写策略 (Rust)](../how_to/write_rust_strategy.md) 操作指南。有关完整示例，请参阅 [`EmaCross`](https://github.com/nautechsystems/nautilus_trader/tree/develop/crates/trading/src/examples/strategies/ema_cross) 和 [`GridMarketMaker`](https://github.com/nautechsystems/nautilus_trader/tree/develop/crates/trading/src/examples/strategies/grid_mm)。

### 运行 Rust 组件 (Running Rust components)

Rust 策略和 Actor 可以通过三种路径运行。以下示例使用策略，但同样的模式也适用于 Actor，只需通过 `add_actor`（纯 Rust）和 `add_native_actor`（来自 Python）即可。

#### 纯 Rust (Pure Rust)

在 Rust 中编写你的策略和 `main` 函数，然后通过 `cargo build` 构建一个独立的二进制文件。此路径不需要 Python 运行时。

```rust
let strategy = GridMarketMaker::new(config);
node.add_strategy(strategy)?;
node.run().await?;
```

有关完整演练，请参阅 [运行实盘交易 (Rust)](../how_to/run_rust_live_trading.md)。

#### 来自 Python 的原生配置 (Native config from Python)

将配置传递给 `add_native_strategy`，以便从 Python 注册内置的 Rust 策略。Rust 侧构造该策略并将其注册到引擎中。Python 提供配置，所有的执行都在 Rust 中进行。

```python
from nautilus_trader.core.nautilus_pyo3.trading import GridMarketMakerConfig

config = GridMarketMakerConfig(
    instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
    max_position=Quantity.from_str("10.0"),
    trade_size=Quantity.from_str("0.1"),
    num_levels=5,
    grid_step_bps=15,
)

node.add_native_strategy(config)
```

内置策略配置：

| 配置 | 策略 |
|-------------------------|-----------------------|
| `EmaCrossConfig` | `EmaCross` |
| `GridMarketMakerConfig` | `GridMarketMaker` |
| `DeltaNeutralVolConfig` | `DeltaNeutralVol` |

内置 Actor 配置（通过 `add_native_actor`）：

| 配置 | Actor |
|----------------------------|-----------------------|
| `BookImbalanceActorConfig` | `BookImbalanceActor` |

从源码编译的用户可以在此路径中添加自己的组件。添加一个 `#[pyclass]` 配置并在 `add_native_strategy` 或 `add_native_actor` 中添加分发逻辑。该组件随后即可从 Python 中使用，而无需在该类型上使用 PyO3 包装器。

#### 插件加载 (Plugin loading) (计划中)

未来的插件系统将在运行时加载编译好的共享库。用户将策略和 Actor 编译为 `cdylib` crate，节点无需重新编译即可加载它们。此路径目前尚未可用。

## 回测 (Backtesting)

有关这两个 API 的带注释的演练，请参阅 [运行回测 (Rust)](../how_to/run_rust_backtest.md) 操作指南。

### `BacktestEngine` (低层 API)

构造引擎，添加交易场所和合约，加载数据，注册策略并运行。查看完整的工作示例：

```bash
cargo run -p nautilus-backtest --features examples --example engine-ema-cross
```

源码：[`crates/backtest/examples/engine_ema_cross.rs`](https://github.com/nautechsystems/nautilus_trader/tree/develop/crates/backtest/examples/engine_ema_cross.rs)

### `BacktestNode` (高层 API)

从 `ParquetDataCatalog` 加载数据，并支持以可配置的分块大小进行流式传输。需要在 `nautilus-backtest` 上启用 `streaming` 特性。查看完整的工作示例：

```bash
cargo run -p nautilus-backtest --features examples,streaming --example node-ema-cross
```

源码：[`crates/backtest/examples/node_ema_cross.rs`](https://github.com/nautechsystems/nautilus_trader/tree/develop/crates/backtest/examples/node_ema_cross.rs)

## 实盘交易 (Live trading)

有关带注释的演练，请参阅 [运行实盘交易 (Rust)](../how_to/run_rust_live_trading.md) 操作指南。

`LiveNode` 通过适配器客户端连接到真实的交易场所。使用构建器模式 (Builder pattern) 配置数据和执行客户端，然后调用 `run()` 启动异步事件循环。每个适配器都提供自己的工厂和配置类型。

| 适配器 | 示例 |
|----------------|----------------------------------------------------------|
| Architect AX | `crates/adapters/architect_ax/examples/` |
| Betfair | `crates/adapters/betfair/examples/` |
| Binance | `crates/adapters/binance/examples/` |
| BitMEX | `crates/adapters/bitmex/examples/` |
| Blockchain | `crates/adapters/blockchain/examples/` |
| Bybit | `crates/adapters/bybit/examples/` |
| Databento | `crates/adapters/databento/examples/` |
| Deribit | `crates/adapters/deribit/examples/` |
| dYdX | `crates/adapters/dydx/examples/` |
| Hyperliquid | `crates/adapters/hyperliquid/examples/` |
| Kraken | `crates/adapters/kraken/examples/` |
| OKX | `crates/adapters/okx/examples/` |
| Polymarket | `crates/adapters/polymarket/examples/` |
| Sandbox | `crates/adapters/sandbox/examples/` |
| Tardis | `crates/adapters/tardis/examples/` |

大多数适配器都包含 `node_data_tester.rs` 和 `node_exec_tester.rs` 示例。这些示例用于测试在真实交易场所中的数据请求、流传输和订单执行。

## 相关指南 (Related guides)

- [编写 Actor (Rust)](../how_to/write_rust_actor.md) - 分步 Actor 演练。
- [编写策略 (Rust)](../how_to/write_rust_strategy.md) - 分步策略演练。
- [运行回测 (Rust)](../how_to/run_rust_backtest.md) - BacktestEngine 和 BacktestNode 用法。
- [运行实盘交易 (Rust)](../how_to/run_rust_live_trading.md) - LiveNode 设置和交易场所连接。
- [架构 (Architecture)](architecture.md) - 系统设计与数据/执行流程。
- [Actor (Actors)](actors.md) - Actor 概念（同时适用于 Python 和 Rust）。
- [策略 (Strategies)](strategies.md) - 策略概念和处理器参考。
- [事件 (Events)](events.md) - 事件类型和处理器分发。
- [回测 (Backtesting)](backtesting.md) - 回测概念和撮合引擎行为。
