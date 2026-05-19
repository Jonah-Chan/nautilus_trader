# 运行回测 (Backtest) (Rust)

Nautilus 为回测 (Backtest) 提供了两个 Rust API：`BacktestEngine`（低级）和 `BacktestNode`（带有目录流的高级）。本指南涵盖了这两者。

有关回测 (Backtest) 概念、填充模型和撮合引擎行为的背景信息，请参阅[回测 (Backtesting)](../concepts/backtesting.md) 概念指南。
有关项目设置和功能标志 (Feature flags)，请参阅 [Rust](../concepts/rust.md#project-setup) 概念指南。

## 依赖 (Dependencies)

将以下内容添加到您的 `Cargo.toml` 中。只有在使用高级 `BacktestNode` API 时才需要 `streaming` 和 `nautilus-persistence` 条目。

```toml
[dependencies]
nautilus-backtest = { version = "0.55", features = ["streaming"] }
nautilus-execution = "0.55"
nautilus-model = { version = "0.55", features = ["stubs"] }
nautilus-persistence = "0.55"
nautilus-trading = { version = "0.55", features = ["examples"] }

ahash = "0.8"
anyhow = "1"
tempfile = "3"
ustr = "1"
```

如果您只需要低级 `BacktestEngine`，请删去 `streaming`、`nautilus-persistence`、`tempfile` 和 `ustr`。

## BacktestEngine（低级 API）

低级 API 提供直接控制：您构建引擎、添加场所 (Venue) 和合约 (Instrument)、将数据加载到内存中、注册策略 (Strategy) 并运行。

### 1. 创建引擎

```rust
use nautilus_backtest::{config::BacktestEngineConfig, engine::BacktestEngine};

let mut engine = BacktestEngine::new(BacktestEngineConfig::default())?;
```

### 2. 添加场所 (Venue)

`SimulatedVenueConfig` 使用 `bon::Builder`：仅需设置必填字段，其他所有设置都将回退到记录的默认值。

```rust
use nautilus_backtest::config::SimulatedVenueConfig;
use nautilus_model::{
    enums::{AccountType, BookType, OmsType},
    identifiers::Venue,
    types::Money,
};

engine.add_venue(
    SimulatedVenueConfig::builder()
        .venue(Venue::from("SIM"))
        .oms_type(OmsType::Hedging)
        .account_type(AccountType::Margin)
        .book_type(BookType::L1_MBP)
        .starting_balances(vec![Money::from("1_000_000 USD")])
        .build(),
)?;
```

通过链式调用 setter 来覆盖任何默认值，例如 `.reject_stop_orders(false)` 或 `.allow_cash_borrowing(true)`。

### 3. 添加合约 (Instrument) 和数据

```rust
use nautilus_model::instruments::{
    Instrument, InstrumentAny, stubs::audusd_sim,
};

let instrument = InstrumentAny::CurrencyPair(audusd_sim());
let instrument_id = instrument.id();
engine.add_instrument(&instrument)?;

let quotes = generate_quotes(instrument_id); // 您的数据加载函数
engine.add_data(quotes, None, true, true)?;
```

### 4. 注册策略 (Strategy) 并运行

```rust
use nautilus_model::types::Quantity;
use nautilus_trading::examples::strategies::EmaCross;

let strategy = EmaCross::new(
    instrument_id,
    Quantity::from("100000"),
    10, // 快速 EMA 周期
    20, // 慢速 EMA 周期
);

engine.add_strategy(strategy)?;
engine.run(None, None, None, false)?;
```

### 运行完整示例

```bash
cargo run -p nautilus-backtest --features examples --example engine-ema-cross
```

源码：
[`crates/backtest/examples/engine_ema_cross.rs`](https://github.com/nautechsystems/nautilus_trader/tree/develop/crates/backtest/examples/engine_ema_cross.rs)

## BacktestNode（高级 API）

高级 API 从 `ParquetDataCatalog` 加载数据，并以可配置的分块大小进行流式传输。需要在 `nautilus-backtest` 上开启 `streaming` 功能。

### 1. 将数据写入目录 (Catalog)

```rust
use nautilus_model::instruments::{
    Instrument, InstrumentAny, stubs::audusd_sim,
};
use nautilus_persistence::backend::catalog::ParquetDataCatalog;
use tempfile::TempDir;

let instrument = InstrumentAny::CurrencyPair(audusd_sim());
let instrument_id = instrument.id();
let quotes = generate_quotes(instrument_id);

let temp_dir = TempDir::new()?;
let catalog_path = temp_dir.path().to_str()
    .context("temp dir path is not valid UTF-8")?
    .to_string();
let catalog = ParquetDataCatalog::new(
    temp_dir.path(), None, None, None, None,
);

catalog.write_instruments(vec![instrument])?;
catalog.write_to_parquet(quotes, None, None, None)?;
```

### 2. 配置运行

```rust
use nautilus_backtest::config::{
    BacktestDataConfig, BacktestEngineConfig,
    BacktestRunConfig, BacktestVenueConfig, NautilusDataType,
};
use nautilus_model::enums::{AccountType, BookType, OmsType};
use ustr::Ustr;

let venue_config = BacktestVenueConfig::new(
    Ustr::from("SIM"),
    OmsType::Hedging,
    AccountType::Margin,
    BookType::L1_MBP,
    None, // 路由 (routing)
    None, // 冻结账户 (frozen_account)
    None, // 拒绝止损单 (reject_stop_orders)
    None, // 支持 GTD 订单 (support_gtd_orders)
    None, // 支持条件订单 (support_contingent_orders)
    None, // 使用持仓 ID (use_position_ids)
    None, // 使用随机 ID (use_random_ids)
    None, // 使用仅减仓 (use_reduce_only)
    None, // 柱线执行 (bar_execution)
    None, // 柱线自适应高低价下单 (bar_adaptive_high_low_ordering)
    None, // 交易执行 (trade_execution)
    None, // 使用市价单确认 (use_market_order_acks)
    None, // 流动性消耗 (liquidity_consumption)
    None, // 允许现金借贷 (allow_cash_borrowing)
    None, // 队列位置 (queue_position)
    None, // OTO 触发模式 (oto_trigger_mode)
    vec!["1_000_000 USD".to_string()],
    None, // 基准货币 (base_currency)
    None, // 默认杠杆 (default_leverage)
    None, // 杠杆 (leverages)
    None, // 价格保护点数 (price_protection_points)
);

let data_config = BacktestDataConfig::new(
    NautilusDataType::QuoteTick,
    catalog_path,
    None, // catalog_fs_protocol
    None, // catalog_fs_storage_options
    Some(instrument_id),
    None, // 合约 (instrument) ID 列表
    None, // 开始时间 (start_time)
    None, // 结束时间 (end_time)
    None, // 过滤表达式 (filter_expr)
    None, // 客户端 ID (client_id)
    None, // 元数据 (metadata)
    None, // 柱线规范 (bar_spec)
    None, // 柱线类型 (bar_types)
    None, // 优化文件加载 (optimize_file_loading)
);

let run_config = BacktestRunConfig::new(
    Some("ema-cross-run".to_string()),
    vec![venue_config],
    vec![data_config],
    BacktestEngineConfig::default(),
    Some(100), // 以 100 为分块大小进行流式传输
    None,      // 完成后释放 (dispose_on_completion)
    None,      // 开始 (start)
    None,      // 结束 (end)
);
```

### 3. 构建、添加策略 (Strategy) 并运行

```rust
use nautilus_backtest::node::BacktestNode;
use nautilus_model::types::Quantity;
use nautilus_trading::examples::strategies::EmaCross;

let mut node = BacktestNode::new(vec![run_config])?;
node.build()?;

let engine = node.get_engine_mut("ema-cross-run")
    .context("engine not found for run config ID")?;
let strategy = EmaCross::new(
    instrument_id,
    Quantity::from("100000"),
    10,
    20,
);
engine.add_strategy(strategy)?;

node.run()?;
```

### 运行完整示例

```bash
cargo run -p nautilus-backtest --features examples,streaming --example node-ema-cross
```

源码：
[`crates/backtest/examples/node_ema_cross.rs`](https://github.com/nautechsystems/nautilus_trader/tree/develop/crates/backtest/examples/node_ema_cross.rs)
