# 运行实盘交易 (Live Trading) (Rust)

`LiveNode` 通过适配器客户端连接到真实的场所 (Venue)。本指南以 OKX 为例，演示一个完整的实盘交易 (Live Trading) 设置。

有关实盘交易架构和对账的背景信息，请参阅[实盘交易 (Live Trading)](../concepts/live.md) 概念指南。有关项目设置和功能标志 (Feature flags)，请参阅 [Rust](../concepts/rust.md#project-setup) 概念指南。

## 依赖 (Dependencies)

将实盘 (live) crate、您的场所适配器和支持性 crate 添加到 `Cargo.toml` 中：

```toml
[dependencies]
nautilus-common = "0.55"
nautilus-live = "0.55"
nautilus-model = "0.55"
nautilus-okx = "0.55"
nautilus-trading = { version = "0.55", features = ["examples"] }

anyhow = "1"
dotenvy = "0.15"
log = "0.4"
tokio = { version = "1", features = ["full"] }
```

## 构建节点 (Build the node)

`LiveNode` 使用生成器模式 (Builder pattern)。添加您场所的数据和执行客户端工厂，配置日志记录并构建。

```rust
use log::LevelFilter;
use nautilus_common::{enums::Environment, logging::logger::LoggerConfig};
use nautilus_live::node::LiveNode;
use nautilus_model::identifiers::{AccountId, TraderId};
use nautilus_okx::{
    common::enums::OKXInstrumentType,
    config::{OKXDataClientConfig, OKXExecClientConfig},
    factories::{OKXDataClientFactory, OKXExecutionClientFactory},
};

let trader_id = TraderId::from("TESTER-001");
let account_id = AccountId::from("OKX-001");

let data_config = OKXDataClientConfig {
    instrument_types: vec![OKXInstrumentType::Swap],
    ..Default::default()
};

let exec_config = OKXExecClientConfig {
    trader_id,
    account_id,
    instrument_types: vec![OKXInstrumentType::Swap],
    ..Default::default()
};

let log_config = LoggerConfig {
    stdout_level: LevelFilter::Info,
    ..Default::default()
};

let mut node = LiveNode::builder(trader_id, Environment::Live)?
    .with_name("MY-NODE-001".to_string())
    .with_logging(log_config)
    .add_data_client(
        None,
        Box::new(OKXDataClientFactory::new()),
        Box::new(data_config),
    )?
    .add_exec_client(
        None,
        Box::new(OKXExecutionClientFactory::new()),
        Box::new(exec_config),
    )?
    .with_reconciliation(false) // 为简化起见；请在生产环境中启用
    .with_delay_post_stop_secs(5)
    .build()?;
```

:::warning
为了简单起见，此示例禁用了对账 (Reconciliation)。在生产环境中，请移除 `.with_reconciliation(false)`，以便引擎在启动时使缓存状态与场所对齐。请参阅[执行对账 (Execution reconciliation)](../concepts/live.md#execution-reconciliation)。
:::

## 添加策略 (Strategy) 并运行

```rust
use nautilus_model::{identifiers::InstrumentId, types::Quantity};
use nautilus_trading::examples::strategies::{
    GridMarketMaker, GridMarketMakerConfig,
};

let mut config = GridMarketMakerConfig::new(
    InstrumentId::from("ETH-USDT-SWAP.OKX"),
    Quantity::from("0.10"),
)
    .with_num_levels(3)
    .with_grid_step_bps(100)
    .with_skew_factor(0.5)
    .with_requote_threshold_bps(10)
    .with_expire_time_secs(8)
    .with_on_cancel_resubmit(true);

// OKX 拒绝客户端订单 ID 中的连字符
config.base.use_hyphens_in_client_order_ids = false;

let strategy = GridMarketMaker::new(config);

node.add_strategy(strategy)?;
node.run().await?;
```

节点将一直运行，直到中断 (Ctrl+C) 或通过程序化方式关闭。

## 环境变量 (Environment variables)

OKX 从环境变量中读取 API 凭证。使用 `dotenvy` 配合 `.env` 文件，或者在您的 shell 中设置它们：

```bash
export OKX_API_KEY="your_api_key"
export OKX_API_SECRET="your_api_secret"
export OKX_API_PASSPHRASE="your_passphrase"
```

对于模拟交易，请在两个配置结构体中设置 `environment: OKXEnvironment::Demo`，并使用 OKX 提供的模拟 API 凭据。

每个适配器都在其对应的[集成指南 (Integration Guide)](../integrations/)中记录了其所需的变量。

## 异步运行时 (Async runtime)

`LiveNode::run()` 是异步的，需要 Tokio 运行时。在您的 `main` 函数上使用 `#[tokio::main]`：

```rust
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    dotenvy::dotenv().ok();

    // ... 节点设置 ...

    node.run().await?;
    Ok(())
}
```

## 适配器示例 (Adapter examples)

大多数适配器都包含具有数据测试器和执行测试器的可运行示例：

| 适配器 | 示例目录 |
|--------------|--------------------------------------------|
| Architect AX | `crates/adapters/architect_ax/examples/` |
| Betfair | `crates/adapters/betfair/examples/` |
| Binance | `crates/adapters/binance/examples/` |
| BitMEX | `crates/adapters/bitmex/examples/` |
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
