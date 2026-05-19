# 配置实盘交易节点 (Configure a Live Trading Node)

为实盘市场连接设置 `TradingNode`。有关实盘交易架构和对账的背景信息，请参阅[实盘交易 (Live Trading)](../concepts/live.md) 概念指南。

:::danger[不建议在 Jupyter notebook 中进行实盘交易]
不要在 Jupyter notebook 中运行实盘交易 (Live Trading) 节点。事件循环 (Event loop) 冲突和操作风险使其不适用：

- Jupyter 运行自己的 asyncio 事件循环 (Event loop)，这与 `TradingNode` 的事件循环 (Event loop) 冲突。
- 像 `nest_asyncio` 这样的变通方法并非生产级。
- 单元格可能会乱序运行，内核可能会崩溃，状态可能会丢失。
- Notebook 缺乏生产交易所需的日志记录、监控和优雅停机。

请在回测 (Backtest)、分析和实验中使用 Jupyter。对于实盘交易 (Live Trading)，请将节点作为独立的 Python 脚本或服务运行。
:::

:::warning[每个进程一个 TradingNode]
由于全局单例状态，不支持在同一进程中并发运行多个 `TradingNode` 实例。
请将多个策略 (Strategy) 添加到单个节点，或者在单独的进程中运行其他节点以进行并行执行。

详情请参阅[进程和线程 (Processes and threads)](../concepts/architecture.md#processes-and-threads)。
:::

:::warning[不要阻塞事件循环]
事件循环 (Event loop) 线程上的用户代码（策略回调、执行器处理程序、`on_event` 方法）必须快速返回。这适用于 Python 和 Rust。诸如模型推理、繁重计算或同步 I/O 之类的阻塞操作会导致错过成交 (Fills)、数据陈旧以及订单提交延迟。请将耗时较长的工作移交给执行器 (Executor) 或单独的线程/进程。
:::

:::info[平台差异]
Windows 信号处理与类 Unix 系统不同。如果您在 Windows 上运行，请阅读有关 [Windows 信号处理](#windows-signal-handling)的说明，以获取有关优雅停机行为和 Ctrl+C (SIGINT) 支持的指导。
:::

## TradingNodeConfig

`TradingNodeConfig` 继承自 `NautilusKernelConfig` 并添加了实盘特定的选项。
有关配置结构体如何处理默认值和 `Option<T>` 语义的背景信息，请参阅[配置 (Configuration)](../concepts/configuration.md) 概念指南。

```python
from nautilus_trader.config import TradingNodeConfig

config = TradingNodeConfig(
    trader_id="MyTrader-001",

    # 组件配置
    cache=CacheConfig(),
    message_bus=MessageBusConfig(),
    data_engine=LiveDataEngineConfig(),
    risk_engine=LiveRiskEngineConfig(),
    exec_engine=LiveExecEngineConfig(),
    portfolio=PortfolioConfig(),

    # 客户端配置
    data_clients={
        "BINANCE": BinanceDataClientConfig(),
    },
    exec_clients={
        "BINANCE": BinanceExecClientConfig(),
    },
)
```

### 核心配置参数

| 设置 | 默认值 | 描述 |
|--------------------------|--------------|---------------------------------------------|
| `trader_id` | "TRADER-001" | 唯一的交易员标识符（名称-标签格式）。 |
| `instance_id` | `None` | 可选的唯一实例标识符。 |
| `timeout_connection` | 30.0 | 连接超时（秒）。 |
| `timeout_reconciliation` | 10.0 | 对账 (Reconciliation) 超时（秒）。 |
| `timeout_portfolio` | 10.0 | 投资组合 (Portfolio) 初始化超时。 |
| `timeout_disconnection` | 10.0 | 断开连接超时。 |
| `timeout_post_stop` | 5.0 | 停止后清理超时。 |

### 缓存数据库配置 (Cache database configuration)

```python
from nautilus_trader.config import CacheConfig
from nautilus_trader.config import DatabaseConfig

cache_config = CacheConfig(
    database=DatabaseConfig(
        host="localhost",
        port=6379,
        username="nautilus",
        password="pass",
        connection_timeout=2,
        response_timeout=2,
    ),
    encoding="msgpack",  # 或 "json"
    timestamps_as_iso8601=True,
    buffer_interval_ms=100,
    flush_on_start=False,
)
```

### 消息总线配置 (MessageBus configuration)

```python
from nautilus_trader.config import MessageBusConfig
from nautilus_trader.config import DatabaseConfig

message_bus_config = MessageBusConfig(
    database=DatabaseConfig(
        connection_timeout=2,
        response_timeout=2,
    ),
    timestamps_as_iso8601=True,
    use_instance_id=False,
    types_filter=[QuoteTick, TradeTick],  # 过滤特定的消息类型
    stream_per_topic=False,
    autotrim_mins=30,  # 自动消息修剪
    heartbeat_interval_secs=1,
)
```

## 多场所配置 (Multi-venue configuration)

一个节点可以连接到多个场所 (Venue)。此示例为 Binance 配置了现货和期货市场：

```python
config = TradingNodeConfig(
    trader_id="MultiVenue-001",

    # 针对不同市场类型的多个数据客户端
    data_clients={
        "BINANCE_SPOT": BinanceDataClientConfig(
            account_type=BinanceAccountType.SPOT,
            environment=BinanceEnvironment.LIVE,
        ),
        "BINANCE_FUTURES": BinanceDataClientConfig(
            account_type=BinanceAccountType.USDT_FUTURES,
            environment=BinanceEnvironment.LIVE,
        ),
    },

    # 相应的执行客户端
    exec_clients={
        "BINANCE_SPOT": BinanceExecClientConfig(
            account_type=BinanceAccountType.SPOT,
            environment=BinanceEnvironment.LIVE,
        ),
        "BINANCE_FUTURES": BinanceExecClientConfig(
            account_type=BinanceAccountType.USDT_FUTURES,
            environment=BinanceEnvironment.LIVE,
        ),
    },
)
```

## 执行引擎配置 (ExecutionEngine configuration)

`LiveExecEngineConfig` 控制订单处理、执行事件和场所对账 (Venue reconciliation)。有关完整详细信息，请参阅 [API 参考](/docs/python-api-latest/config.html#nautilus_trader.live.config.LiveExecEngineConfig)。

### 对账 (Reconciliation)

恢复错过的订单和持仓 (Position) 事件，以保持系统状态与场所一致。

| 设置 | 默认值 | 描述 |
|---------------------------------|---------|---------------------------------------------------------------------------------|
| `reconciliation` | True | 启动时激活对账 (Reconciliation)，以使内部状态与场所保持一致。 |
| `reconciliation_lookback_mins` | None | 请求过去事件以进行未缓存状态对账的回溯时间（分钟）。 |
| `reconciliation_instrument_ids` | None | 包含要对账的合约 (Instrument) ID 列表。 |
| `filtered_client_order_ids` | None | 对账期间要跳过的客户端订单 ID（针对场所端的重复项）。 |

详情请参阅[执行对账 (Execution reconciliation)](../concepts/live.md#execution-reconciliation)。

### 订单过滤 (Order filtering)

控制系统处理哪些订单事件和报告，防止跨交易节点的冲突。

| 设置 | 默认值 | 描述 |
|------------------------------------|---------|-------------------------------------------------------------------------------|
| `filter_unclaimed_external_orders` | False | 丢弃未认领的外部订单，以免它们影响策略。 |
| `filter_position_reports` | False | 丢弃持仓状态报告。当多个节点交易一个账户时很有用。 |

:::note[订单标记行为]
对账 (Reconciliation) 按来源标记订单：

- **`VENUE` 标签**：在场所发现的外部订单（在此系统之外下达）。
- **`RECONCILIATION` 标签**：为调整持仓差异而生成的合成订单。

启用 `filter_unclaimed_external_orders` 时，仅过滤带有 `VENUE` 标签的订单。
带有 `RECONCILIATION` 标签的订单永远不会被过滤，因此持仓调整总是会成功。
:::

### 持续对账 (Continuous reconciliation)

后台循环在启动对账完成后开始。它：

- 监控在途订单 (In-flight order) 的延迟是否超过配置的阈值。
- 以可配置的间隔与场所对账未平仓订单。
- 针对场所的公共账本审计内部*自有*订单簿。

该循环等待启动对账完成后再开始定期检查。`reconciliation_startup_delay_secs` 参数在启动对账完成*后*增加进一步的延迟，以便给系统稳定的时间。

当重试耗尽时，引擎按如下方式解析订单：

**在途订单 (In-flight order) 超时解析**（在最大重试次数后场所未响应）：

| 当前状态 | 解析为 | 原理 |
|------------------|-------------|--------------------------------------------|
| `SUBMITTED` | `REJECTED` | 未收到场所的确认。 |
| `PENDING_UPDATE` | `CANCELED` | 修改仍未得到确认。 |
| `PENDING_CANCEL` | `CANCELED` | 场所从未确认撤单。 |

**订单一致性检查**（当缓存状态与场所状态不同时）：

| 缓存状态 | 场所状态 | 解析结果 | 原理 |
|--------------------|--------------|-------------|---------------------------------------------------------------------|
| `SUBMITTED` | 未找到 | `REJECTED` | 订单从未被场所确认（例如，在网络错误期间丢失）。 |
| `ACCEPTED` | 未找到 | `REJECTED` | 场所不存在该订单，可能从未成功下单。 |
| `ACCEPTED` | `CANCELED` | `CANCELED` | 场所撤销了订单（用户操作或场所发起）。 |
| `ACCEPTED` | `EXPIRED` | `EXPIRED` | 订单在场所达到 GTD 过期。 |
| `ACCEPTED` | `REJECTED` | `REJECTED` | 场所在初始接受后拒绝（罕见但可能发生）。 |
| `PARTIALLY_FILLED` | `CANCELED` | `CANCELED` | 场所在保留成交的情况下撤销了订单。 |
| `PARTIALLY_FILLED` | 未找到 | `CANCELED` | 订单不存在但有成交（对账成交历史）。 |

:::note
**对账注意事项：**

- **“未找到”解析**仅适用于全历史模式 (`open_check_open_only=False`)。
  仅限未平仓模式（默认）跳过这些检查，因为场地的“未平仓订单”端点根据设计排除已关闭的订单，从而无法区分丢失的订单和最近关闭的订单。
- **最近订单保护**：引擎跳过对最后一次事件落在 `open_check_threshold_ms` 窗口（默认 5s）内的订单的对账。这可以防止由于场所仍在处理而导致的竞争条件的误报。
- **针对性查询保障**：在将“未找到”的订单标记为 `REJECTED` 或 `CANCELED` 之前，引擎会向场所发出单笔订单查询。这可以捕获由于批量查询限制或时间延迟导致的漏报。
- **`FILLED` 订单**如果在场所“未找到”，将被默默忽略。场所通常会将已完成的订单从其查询结果中删除。

:::

### 重试协调和回溯行为 (Retry coordination and lookback behavior)

在途检查循环和未平仓订单循环共享一个重试计数器 (`_recon_check_retries`)，分别受 `inflight_check_retries` 和 `open_check_missing_retries` 的限制。以更严格的限制为准，并避免针对同一订单状态重复查询场所。

当未平仓订单循环耗尽重试时，引擎在应用终止状态之前发出一次针对性的 `GenerateOrderStatusReport` 探测。如果场所返回订单，对账继续进行，重试计数器重置。

**单笔订单查询保护**：引擎通过 `max_single_order_queries_per_cycle`（默认：10）限制每个周期的单笔订单查询。剩余订单推迟到下一个周期。可配置的延迟 (`single_order_query_delay_ms`，默认：100ms) 间隔连续查询以避免频率限制。这可以处理涉及数百个订单的批量查询失败，而不会压垮场所 API。

早于 `open_check_lookback_mins` 的订单依赖于此针对性探测。对于历史窗口较短的场所，请保持慷慨的回溯。如果场所时间戳滞后于本地时钟，请增加 `open_check_threshold_ms`，以免最近更新的订单被过早标记为丢失。

| 设置 | 默认值 | 描述 |
|--------------------------------------|----------------|--------------------------------------------------------------------------------------------------|
| `inflight_check_interval_ms` | 2,000&nbsp;ms | 检查在途订单状态的频率。设置为 0 禁用。 |
| `inflight_check_threshold_ms` | 5,000&nbsp;ms | 在途订单触发场所状态检查之前的时间。如果托管在同一数据中心，请调低此值。 |
| `inflight_check_retries` | 5&nbsp;次重试 | 向场所验证在途订单的重试次数。 |
| `open_check_interval_secs` | None | 在场所检查未平仓订单的频率（秒）。None 或 0.0 禁用。推荐：5-10s。 |
| `open_check_open_only` | True | 为 true 时，仅查询未平仓订单；为 false 时，获取完整历史记录（资源密集型）。 |
| `open_check_lookback_mins` | 60&nbsp;分钟 | 订单状态轮询的回溯窗口（分钟）。仅限在此窗口内修改的订单。 |
| `open_check_threshold_ms` | 5,000&nbsp;ms | 在根据场所差异采取行动之前，自上次缓存事件以来的最短时间。 |
| `open_check_missing_retries` | 5&nbsp;次重试 | 解析缓存中为未平仓但在场所未找到的订单之前的最大重试次数。 |
| `max_single_order_queries_per_cycle` | 10 | 每个周期的单笔订单查询上限。防止频率限制耗尽。 |
| `single_order_query_delay_ms` | 100&nbsp;ms | 单笔订单查询之间的延迟 (ms)，以避免频率限制。 |
| `reconciliation_startup_delay_secs` | 10.0&nbsp;s | 启动对账*后*、持续检查开始之前的延迟（秒）。 |
| `own_books_audit_interval_secs` | None | 针对公共账本审计自有订单簿的间隔（秒）。 |
| `position_check_interval_secs` | None | 持仓 (Position) 一致性检查之间的间隔（秒）。发现差异时，查询丢失的成交。None 禁用。推荐：30-60s。 |
| `position_check_lookback_mins` | 60&nbsp;分钟 | 在出现持仓差异时查询成交报告的回溯窗口（分钟）。 |
| `position_check_threshold_ms` | 5,000&nbsp;ms | 在根据持仓差异采取行动之前，自上次本地活动以来的最短时间。 |
| `position_check_retries` | 3&nbsp;次重试 | 每个合约 (Instrument) 的最大尝试次数，之后引擎停止重试该差异。一旦超过，将记录错误，并且在差异消除之前不再主动对账。 |

:::warning

- **`open_check_lookback_mins`**：请勿降低到 60 分钟以下。较短的窗口会触发错误的“丢失订单”解析，因为订单落在查询范围之外。
- **`reconciliation_startup_delay_secs`**：在生产环境中请勿降低到 10 秒以下。该延迟使系统在启动对账完成后、持续检查开始之前稳定下来。

:::

### 其他选项 (Additional options)

| 设置 | 默认值 | 描述 |
|------------------------------------|---------|-------------------------------------------------------------------------------------------------|
| `allow_overfills` | False | 允许超过订单数量的成交（记录警告）。在对账与成交竞争时很有用。 |
| `generate_missing_orders` | True | 在对账期间生成限价单 (LIMIT order) 以调整持仓差异（策略为 `EXTERNAL`，标签为 `RECONCILIATION`）。 |
| `snapshot_orders` | False | 在订单事件发生时获取订单快照。 |
| `snapshot_positions` | False | 在持仓事件发生时获取持仓快照。 |
| `snapshot_positions_interval_secs` | None | 持仓快照之间的间隔（秒）。 |
| `debug` | False | 启用执行的调试日志记录。 |

### 内存管理 (Memory management)

定期从内存缓存中清除已关闭的订单、已平仓的持仓和账户事件，在长时间运行或高频交易 (HFT) 会话期间保持内存有界。

| 设置 | 默认值 | 描述 |
|----------------------------------------|---------|------------------------------------------------------------------------------------|
| `purge_closed_orders_interval_mins` | None | 从内存中清除已关闭订单的频率（分钟）。推荐：10-15 分钟。 |
| `purge_closed_orders_buffer_mins` | None | 订单必须关闭多长时间（分钟）才能清除。推荐：60 分钟。 |
| `purge_closed_positions_interval_mins` | None | 从内存中清除已平仓持仓的频率（分钟）。推荐：10-15 分钟。 |
| `purge_closed_positions_buffer_mins` | None | 持仓必须关闭多长时间（分钟）才能清除。推荐：60 分钟。 |
| `purge_account_events_interval_mins` | None | 从内存中清除账户事件的频率（分钟）。推荐：10-15 分钟。 |
| `purge_account_events_lookback_mins` | None | 账户事件必须多旧（分钟）才能清除。推荐：60 分钟。 |
| `purge_from_database` | False | 同时也从后备数据库 (Redis/PostgreSQL) 中删除。**请谨慎使用**。 |

设置间隔可启用清除循环；不设置则禁用调度和删除。除非 `purge_from_database` 为 true，否则数据库记录不受影响。每个循环都委托给[缓存 (Cache)](../concepts/cache.md) 中描述的缓存 API。

### 队列管理 (Queue management)

| 设置 | 默认值 | 描述 |
|----------------------------------|---------|---------------------------------------------------------------------------------|
| `qsize` | 100,000 | 内部队列缓冲区的大小。 |
| `graceful_shutdown_on_exception` | False | 在发生意外的队列处理异常（非用户代码）时优雅停机。 |

## 策略配置 (Strategy configuration)

有关完整的参数列表，请参阅 `StrategyConfig` [API 参考](/docs/python-api-latest/config.html#nautilus_trader.trading.config.StrategyConfig)。

### 标识 (Identification)

| 设置 | 默认值 | 描述 |
|----------------|---------|---------------------------------------------------------------|
| `strategy_id` | None | 唯一的策略标识符。 |
| `order_id_tag` | None | 附加到此策略订单 ID 的唯一标签。 |

### 订单 management (Order management)

| 设置 | 默认值 | 描述 |
|-----------------------------|---------|--------------------------------------------------------------------------------------------|
| `oms_type` | None | 用于持仓 ID 和订单处理的 [OMS 类型](../concepts/execution#oms-configuration)。 |
| `use_uuid_client_order_ids` | False | 为客户端订单 ID 使用 UUID4 值。 |
| `external_order_claims` | None | 此策略认领其外部订单的合约 (Instrument) ID。 |
| `manage_contingent_orders` | False | 自动管理 OTO、OCO 和 OUO 条件订单。 |
| `manage_gtd_expiry` | False | 管理订单的 GTD 过期。 |

## Windows 信号处理 (Windows signal handling)

:::warning
Windows：asyncio 事件循环未实现 `loop.add_signal_handler`。因此，`TradingNode` 在 Windows 上无法通过 asyncio 接收操作系统信号。请使用 Ctrl+C (SIGINT) 处理或程序化关机；在 Windows 上不期望 SIGTERM 等效性。
:::

在 Windows 上，asyncio 事件循环未实现 `loop.add_signal_handler`，因此 Unix 风格的信号集成不可用。`TradingNode` 在 Windows 上无法通过 asyncio 接收操作系统信号，除非您进行干预，否则它不会优雅停止。

推荐方法：

- 使用 `try/except KeyboardInterrupt` 包裹 `run`，并调用 `node.stop()` 然后 `node.dispose()`。Ctrl+C 会在主线程中引发 `KeyboardInterrupt`，为您提供干净的拆除路径。
- 以程序化方式发布 `ShutdownSystem` 命令（或从执行器/组件调用 `shutdown_system(...)`）以触发相同的停机路径。

出现“在途检查循环任务仍挂起”消息是因为未触发正常的优雅停机路径。这被记录为 [#2785](https://github.com/nautechsystems/nautilus_trader/issues/2785)。

v2 `LiveNode` 已经通过 `tokio::signal::ctrl_c()` 和 Python SIGINT 桥接处理了 Ctrl+C，因此运行器和任务可以干净地关闭。

Windows 的示例模式：

```python
try:
    node.run()
except KeyboardInterrupt:
    pass
finally:
    try:
        node.stop()
    finally:
        node.dispose()
```
