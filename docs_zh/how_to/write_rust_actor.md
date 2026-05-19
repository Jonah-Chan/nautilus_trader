# 编写执行器 (Actor) (Rust)

执行器 (Actor) 接收市场数据、自定义数据/信号和系统事件，但不管理订单。本指南将逐步介绍如何构建一个 `SpreadMonitor`，它订阅报价 (Quote) 并记录买卖价差 (Bid-ask spread)。

有关执行器 (Actor)、trait 和处理程序调度的背景信息，请参阅[执行器 (Actors)](../concepts/actors.md) 和 [Rust](../concepts/rust.md) 概念指南。

## 定义结构体 (Define the struct)

执行器 (Actor) 拥有一个 `DataActorCore` 及其所需的任何状态。`core` 通过 `Deref` 提供订阅方法、缓存 (Cache) 访问和时钟访问。

```rust
use nautilus_common::{nautilus_actor, actor::{DataActor, DataActorConfig, DataActorCore}};
use nautilus_model::{data::QuoteTick, identifiers::{ActorId, InstrumentId}};

pub struct SpreadMonitor {
    core: DataActorCore,
    instrument_id: InstrumentId,
}
```

## 实现构造函数 (Implement the constructor)

使用执行器 ID 创建 `DataActorConfig`，然后将其传递给 `DataActorCore::new`。配置字段使用带有默认值的 `Option`，因此除了执行器 ID 之外，`..Default::default()` 涵盖了所有内容。

```rust
impl SpreadMonitor {
    pub fn new(instrument_id: InstrumentId) -> Self {
        let config = DataActorConfig {
            actor_id: Some(ActorId::from("SPREAD_MON-001")),
            ..Default::default()
        };
        Self {
            core: DataActorCore::new(config),
            instrument_id,
        }
    }
}
```

## 连接核心并实现 Debug (Wire up the core and implement Debug)

`nautilus_actor!` 宏生成 `Deref<Target = DataActorCore>` 和 `DerefMut` 实现，使您的结构体能够直接访问订阅方法、缓存 (Cache) 和时钟。默认情况下，它委托给名为 `core` 的字段；如果使用不同的字段名称，请传递第二个参数。

`Debug` 是 `DataActor` 上的 trait 约束（由 blanket `Component` 实现要求），因此请手动实现或派生 (derive) 它。

```rust
nautilus_actor!(SpreadMonitor);

impl std::fmt::Debug for SpreadMonitor {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SpreadMonitor").finish()
    }
}
```

## 实现 DataActor trait (Implement the DataActor trait)

覆盖处理程序方法以接收数据。所有处理程序都有默认的空操作实现，因此您只需覆盖所需的处理程序。每个处理程序返回 `anyhow::Result<()>`。

```rust
impl DataActor for SpreadMonitor {
    fn on_start(&mut self) -> anyhow::Result<()> {
        self.subscribe_quotes(self.instrument_id, None, None);
        Ok(())
    }

    fn on_quote(&mut self, quote: &QuoteTick) -> anyhow::Result<()> {
        let spread = quote.ask_price.as_f64() - quote.bid_price.as_f64();
        log::info!("Spread: {spread:.5}");
        Ok(())
    }
}
```

由于对 `DataActorCore` 的 `Deref`，`subscribe_quotes` 可以直接在 `self` 上使用。有关所有可用处理程序的信息，请参阅[处理程序表](../concepts/rust.md#handler-methods)。

## 注册执行器 (Actor) (Register the actor)

使用 `BacktestEngine`：

```rust
let actor = SpreadMonitor::new(instrument_id);
engine.add_actor(actor)?;
```

使用 `LiveNode`：

```rust
let actor = SpreadMonitor::new(instrument_id);
node.add_actor(actor)?;
```

## 防护安全 (Guard safety)

当系统向您的执行器 (Actor) 发送消息时，它会从注册表中获取一个短寿命的 `ActorRef` 防护 (Guard)。您不需要直接管理这些防护 (Guard)。如果您在回调中编写访问其他执行器 (Actor) 的代码，请遵循以下规则：

- 每次都通过 ID 查找执行器 (Actor)；不要缓存 `ActorRef`。
- 在作用域结束前释放防护 (Guard)；永远不要将其存储在字段中。
- 永远不要跨 `.await` 点持有防护 (Guard)。

`DataActorCore` 上的订阅方法通过捕获执行器 ID 并在回调闭包内执行查找来正确处理此问题。有关完整的线程和注册表模型，请参阅[运行时不变性 (Runtime invariants)](../developer_guide/rust.md#runtime-invariants)。

## 完整示例 (Full example)

请参阅 [`BookImbalanceActor`](https://github.com/nautechsystems/nautilus_trader/tree/develop/crates/trading/src/examples/actors/imbalance)，了解一个更完整的执行器 (Actor)，它可以跟踪每个合约 (Instrument) 的状态并在停止时打印摘要。
