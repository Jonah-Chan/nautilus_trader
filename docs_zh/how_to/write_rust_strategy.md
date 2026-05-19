# 编写策略 (Strategy) (Rust)

策略 (Strategy) 在执行器 (Actor) 的基础上扩展了订单管理功能。本指南将逐步介绍如何构建一个简单的策略 (Strategy)，它订阅报价 (Quote) 并提交市价单 (Market order)。请先阅读[编写执行器 (Actor) (Rust)](write_rust_actor.md)。

有关策略概念和订单管理的背景信息，请参阅[策略 (Strategies)](../concepts/strategies.md) 和 [Rust](../concepts/rust.md) 概念指南。

## 定义结构体 (Define the struct)

策略 (Strategy) 拥有一个 `StrategyCore` 而不是 `DataActorCore`。`StrategyCore` 包装了 `DataActorCore` 并添加了 `OrderFactory`（订单工厂）、`OrderManager`（订单管理器）和投资组合 (Portfolio) 集成。

```rust
use nautilus_common::actor::DataActor;
use nautilus_model::{
    data::QuoteTick,
    enums::OrderSide,
    identifiers::{InstrumentId, StrategyId},
    types::Quantity,
};
use nautilus_trading::{nautilus_strategy, strategy::{Strategy, StrategyConfig, StrategyCore}};

pub struct MyStrategy {
    core: StrategyCore,
    instrument_id: InstrumentId,
    trade_size: Quantity,
}
```

## 实现构造函数 (Implement the constructor)

`StrategyConfig` 接受 `strategy_id`（策略 ID）和 `order_id_tag`（订单 ID 标签）。标签会附加到此策略的所有客户端订单 ID 中，从而防止多个策略交易同一合约 (Instrument) 时发生冲突。

```rust
impl MyStrategy {
    pub fn new(instrument_id: InstrumentId) -> Self {
        let config = StrategyConfig {
            strategy_id: Some(StrategyId::from("MY_STRAT-001")),
            order_id_tag: Some("001".to_string()),
            ..Default::default()
        };
        Self {
            core: StrategyCore::new(config),
            instrument_id,
            trade_size: Quantity::from("1.0"),
        }
    }
}
```

## 连接核心并实现 Debug (Wire up the core and implement Debug)

`nautilus_strategy!` 宏生成 `Deref<Target = DataActorCore>`、`DerefMut` 和 `Strategy` trait 实现（即 `core()`/`core_mut()` 访问器）。默认情况下，它委托给名为 `core` 的字段；如果使用不同的字段名称，请传递第二个参数。

`Debug` 是 `DataActor` 上的 trait 约束，因此请手动实现或派生 (derive) 它。

```rust
nautilus_strategy!(MyStrategy);

impl std::fmt::Debug for MyStrategy {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("MyStrategy").finish()
    }
}
```

## 实现 DataActor trait (Implement the DataActor trait)

数据处理方式与执行器 (Actor) 相同。在 `on_start` 中订阅，在处理程序中做出响应。

```rust
impl DataActor for MyStrategy {
    fn on_start(&mut self) -> anyhow::Result<()> {
        self.subscribe_quotes(self.instrument_id, None, None);
        Ok(())
    }

    fn on_quote(&mut self, quote: &QuoteTick) -> anyhow::Result<()> {
        let order = self.core.order_factory().market(
            self.instrument_id,
            OrderSide::Buy,
            self.trade_size,
            None, None, None, None, None, None, None,
        );
        self.submit_order(order, None, None)?;
        Ok(())
    }
}
```

`self.core.order_factory()` 构建订单对象。可用方法包括：`market`（市价单）、`limit`（限价单）、`stop_market`（止损市价单）、`stop_limit`（止损限价单）、`market_if_touched`（触及市价单）、`limit_if_touched`（触及限价单）和 `trailing_stop_market`（移动止损市价单）。

通过宏生成的 `Strategy` trait 实现，`submit_order` 可以在 `self` 上直接使用。

## 覆盖策略 (Strategy) 钩子 (Override Strategy hooks)

要覆盖 `Strategy` trait 的方法（例如订单或持仓事件处理程序），请在块中传递它们。宏会自动生成 `core()` 和 `core_mut()`；不要在块中重新定义它们。

```rust
nautilus_strategy!(MyStrategy, {
    fn on_order_rejected(&mut self, event: OrderRejected) {
        log::warn!("Order rejected: {}", event.reason);
    }
});
```

## 订单管理方法 (Order management methods)

`Strategy` trait 通过 `StrategyCore` 提供这些方法：

| 方法 | 动作 |
|-----------------------|-------------------------------------------|
| `submit_order` | 向场所提交新订单。 |
| `submit_order_list` | 提交条件订单列表。 |
| `modify_order` | 修改价格、数量或触发价格。 |
| `cancel_order` | 撤销特定订单。 |
| `cancel_orders` | 撤销一组过滤后的订单。 |
| `cancel_all_orders` | 撤销某合约 (Instrument) 的所有订单。 |
| `close_position` | 以市价单平仓。 |
| `close_all_positions` | 关闭所有未平仓持仓 (Position)。 |

## 完整示例 (Full examples)

- [`EmaCross`](https://github.com/nautechsystems/nautilus_trader/tree/develop/crates/trading/src/examples/strategies/ema_cross)：集成了指标的双 EMA 交叉策略。
- [`GridMarketMaker`](https://github.com/nautechsystems/nautilus_trader/tree/develop/crates/trading/src/examples/strategies/grid_mm)：具有可配置层级和重新报价功能的网格做市策略。
