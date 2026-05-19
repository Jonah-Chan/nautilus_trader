# 订单簿 (Order Book)

NautilusTrader 提供了一个使用 Rust 实现的高性能订单簿 (Order Book)，能够根据 L1 到 L3 的数据维护完整的订单簿状态。`OrderBook` 是跟踪公共市场深度的主要组件，而 `OwnOrderBook` 则单独跟踪你自己的订单，从而实现显示真实可用流动性的过滤视图。

:::note
本指南记录的是 Rust API。这些类型也通过 PyO3 绑定（`nautilus_pyo3.OrderBook`，`nautilus_pyo3.OwnOrderBook`）在 Python 中可用。由 `cache.order_book()` 返回的 v1 遗留 Cython `OrderBook` (`nautilus_trader.model.book.OrderBook`) 具有相似但不完全相同的接口。有关差异，请参阅 API 参考。
:::

## 订单簿类型 (Book types)

在回测和实盘交易中，`OrderBook` 实例都按合约 (Instrument) 进行维护：

- `L3_MBO`：**逐单行情 (Market by order)** 数据。跟踪每个价格层级的每个订单，通过订单 ID 键控。
- `L2_MBP`：**逐价行情 (Market by price)** 数据。按价格层级聚合订单（每个价格一个条目）。
- `L1_MBP`：**盘口 (Top-of-book)** 数据，也称为最佳买卖价 (Best Bid and Offer, BBO)。仅捕获最佳价格。

:::note
盘口数据如 `QuoteTick`、`TradeTick` 和 `Bar` 也可以维护 `L1_MBP` 订单簿。
:::

## 订阅订单簿数据

策略 (Strategy) 和 Actor 通过以下方法订阅订单簿更新。订阅和处理器属于 Python 策略/Actor 层：

```python
# L3/L2 增量更新 (Incremental deltas)
self.subscribe_order_book_deltas(instrument_id)

# 聚合深度快照（最多 10 档）
self.subscribe_order_book_depth(instrument_id)

# 定时定间隔的完整订单簿快照
self.subscribe_order_book_at_interval(instrument_id, interval_ms=1000)
```

每种订阅类型都会将数据交付给相应的处理器：

```python
def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
    ...

def on_order_book_depth(self, depth: OrderBookDepth10) -> None:
    ...

def on_order_book(self, order_book: OrderBook) -> None:
    ...
```

## 访问订单簿

`OrderBook` 暴露了访问盘口信息的接口：

```rust
let best_bid: Option<Price> = book.best_bid_price();
let best_ask: Option<Price> = book.best_ask_price();
let spread: Option<f64> = book.spread();
let midpoint: Option<f64> = book.midpoint();
```

## 分析方法 (Analysis methods)

`OrderBook` 支持市场深度分析和执行模拟：

```rust
// 给定数量的平均成交价
let avg_px = book.get_avg_px_for_quantity(quantity, OrderSide::Buy);

// 目标名义价值 (Notional) 的平均价格和数量
let (price, qty, exposure) =
    book.get_avg_px_qty_for_exposure(target_exposure, OrderSide::Buy);

// 以该价格或更好价格可用的累计数量
let qty = book.get_quantity_for_price(price, OrderSide::Buy);

// 仅在特定价格层级的数量
let qty = book.get_quantity_at_level(price, OrderSide::Buy, 2);

// 对订单簿模拟成交
let fills: Vec<(Price, Quantity)> = book.simulate_fills(&order);

// 无论订单数量如何，所有穿过的价格档位
let levels = book.get_all_crossed_levels(OrderSide::Buy, price, 2);
```

## 完整性检查 (Integrity checks)

`book_check_integrity` 函数验证订单簿状态与其类型是否一致：

- **L1_MBP**：买卖双方各不超过一档价格。
- **L2_MBP**：每个价格层级不超过一个订单（已聚合）。
- **L3_MBO**：无结构限制（任何层级可以有任意数量的订单）。
- **所有类型**：最佳买价不得超过最佳卖价（交叉盘口，Crossed book）。锁定市场（买价 == 卖价）被认为是有效的。

这些检查在应用增量更新 (Delta) 期间在内部运行。进场增量的合约 ID 也会根据订单簿的合约 ID 进行验证，如果不匹配，则返回 `BookIntegrityError::InstrumentMismatch`。

## 漂亮打印 (Pretty printing)

`OrderBook` 和 `OwnOrderBook` 都提供了 `pprint` 方法，将订单簿呈现为人可读的表格：

```rust
book.pprint(5, None);
book.pprint(5, Some(Decimal::new(1, 2))); // group_size = 0.01
```

对于 Tick 大小较细的合约，`group_size` 参数会将价格档位合并到较粗的组中。输出是一个格式化的表格，买价在左侧，价格在中间，卖价在右侧。

## 自身订单簿 (Own order book)

`OwnOrderBook` 独立于公共订单簿跟踪你自己正在运行的订单。做市和其他报价策略使用它来通过减去自己的订单来估算每个价格层级的可用净流动性。

当启用 `manage_own_order_books` 时，执行引擎会维护自身订单簿。缓存会随着订单事件改变状态而更新现有的自身订单簿。符合条件的订单应具有价格，且不使用 `IOC` 或 `FOK` 的有效期限。即使订单不符合跟踪条件，终态事件仍可能清理现有的自身订单簿条目。

### 订单生命周期

`OwnOrderBook` 通过订单的生命周期对其进行跟踪。订单在提交或从对账中实例化时添加，随状态变更到达而更新，并在关闭时移除。更新包括 `accepted`、`pending update`、`pending cancel`、`partially filled`、`filled`、`canceled`、`expired`、`rejected` 和 `denied` 等订单模型支持的状态。

每个 `OwnBookOrder` 携带：

- `client_order_id`：用于与缓存状态同步自身订单簿的客户端订单 ID。
- `venue_order_id`：分配后的交易场所订单 ID。
- `side`、`price` 和 `size`：订单方向以及剩余的自身订单簿价格层级。
- `order_type` 和 `time_in_force`：过滤器和诊断程序使用的订单类型元数据。
- `status`：当前订单状态，如 `SUBMITTED`、`ACCEPTED` 或 `PENDING_CANCEL`。
- `ts_last`：应用于该自身订单簿订单的最新订单事件的时间戳。
- `ts_accepted`：订单被交易场所接受的时间戳。
- `ts_submitted`：订单被提交的时间戳。
- `ts_init`：订单被初始化的时间戳。

这些字段允许过滤后的视图按状态和接受时间包含或排除自身订单（参见[状态和时间过滤](#状态和时间过滤)）。

### 审计 (Auditing)

`audit_open_orders` 方法根据一组有效的客户端订单 ID 校验自身订单簿。任何不在提供集合中的自身订单簿订单都将被移除并记录为审计错误。`Cache::audit_own_order_books` 从未平仓和在途订单中构建此集合，以便在正常的交易场所延迟窗口期间不会移除已提交的订单。实盘系统可以通过自身订单簿审计间隔定期运行此审计。

### 查询

```rust
// 检查特定订单是否在被跟踪
let in_book = own_book.is_order_in_book(&client_order_id);

// 获取每个方向所有被跟踪的订单 ID
let bid_ids = own_book.bid_client_order_ids();
let ask_ids = own_book.ask_client_order_ids();

// 每个价格层级的聚合数量
let bid_qty = own_book.bid_quantity(None, None, None, None, None);
let ask_qty = own_book.ask_quantity(None, None, None, None, None);

// 漂亮打印
own_book.pprint(5, None);
```

### 过滤后的视图 (Filtered views)

从公共订单簿中减去你自己的订单，以查看净可用流动性：

```rust
// 价格 -> 数量的过滤映射（已减去自己的订单）
let net_bids = book.bids_filtered_as_map(Some(10), Some(&own_book), None, None, None);
let net_asks = book.asks_filtered_as_map(Some(10), Some(&own_book), None, None, None);

// 具有所有分析方法的完整过滤后的 OrderBook
let filtered = book.filtered_view(Some(&own_book), Some(10), None, None, None);
let avg_px = filtered.get_avg_px_for_quantity(quantity, OrderSide::Buy);
```

`filtered_view` 方法返回一个新的 `OrderBook`，其中已减去你自己的规模，从而可以对净订单簿访问全套分析方法（`spread`、`midpoint`、`get_avg_px_for_quantity` 等）。

### 状态和时间过滤 (Status and time filtering)

过滤后的视图支持对自身订单进行可选的状态和基于时间的过滤：

```rust
let status = Some(AHashSet::from([OrderStatus::Accepted]));

// 仅减去已接受 (ACCEPTED) 的订单（忽略 SUBMITTED, PENDING_CANCEL 等）
let filtered = book.filtered_view(Some(&own_book), None, status, None, None);
```

`accepted_buffer_ns` 参数提供了一个宽限期：设置后，仅包含 `ts_accepted + buffer <= now` 的订单。这排除了最近接受但尚未出现在公共订单簿数据流中的订单。无论订单状态如何，该缓冲都适用于 `ts_accepted` 字段。结合状态过滤器，还可以排除非接受状态的订单。

```rust
// 仅减去至少 500ms 前被接受的订单
let filtered = book.filtered_view(
    Some(&own_book),
    None,
    None,
    Some(500_000_000),
    Some(clock.timestamp_ns()),
);
```

## 二元市场 (Binary markets)

对于二元/预测市场（例如 Polymarket），合约具有两个互补的方向（YES 和 NO），价格总和为 1.0。在 NO 方向以 0.40 出价在经济上等同于在 YES 方向以 0.60 卖出。

`OwnOrderBook::combined_with_opposite` 方法处理这种转换，将来自两个方向的订单合并到单个视图中：

```rust
let yes_own = own_yes_book
    .cloned()
    .unwrap_or_else(|| OwnOrderBook::new(yes_instrument_id));

let no_own = own_no_book
    .cloned()
    .unwrap_or_else(|| OwnOrderBook::new(no_instrument_id));

// 将 NO 方向订单与奇偶校验价格转换 (1 - price) 合并
let combined = yes_own.combined_with_opposite(&no_own).unwrap();

// 使用合并后的自身订单簿过滤公共的 YES 订单簿
let filtered = book.filtered_view(Some(&combined), None, None, None, None);
```

转换工作原理如下：

- NO 方向以价格 P 卖出在合并后的订单簿中变为价格 1 - P 买入。
- NO 方向以价格 P 买入在合并后的订单簿中变为价格 1 - P 卖出。

这提供了你在市场双方持有的自身流动性的完整图景。
