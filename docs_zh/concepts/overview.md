# 概览 (Overview)

## 简介 (Introduction)

NautilusTrader 是一个开源、生产级 (Production-grade)、基于 Rust 的原生引擎，用于多资产 (Multi-asset)、多交易场所 (Multi-venue) 的交易系统。

该系统在单个事件驱动架构 (Event-driven architecture) 中涵盖了研究、确定性模拟 (Deterministic simulation) 和实盘执行，并以 Python 作为策略逻辑、配置和编排的控制平面 (Control plane)。

这种分离既提供了编译型交易引擎的性能和安全性，又保留了 Python 在系统组合和策略开发方面的灵活性。对于关键任务工作负载，交易系统也可以完全用 Rust 编写。

相同的执行语义 (Execution semantics) 和确定性时间模型 (Deterministic time model) 贯穿于研究和实盘系统。策略从研究环境部署到生产环境无需更改代码，从而实现了研究与实盘的一致性 (Research-to-live parity)，并减少了通常会导致部署风险的发散。

NautilusTrader 与资产类别无关 (Asset-class-agnostic)。任何具有 REST API 或 WebSocket 订阅源的交易场所都可以通过模块化适配器 (Modular adapters) 进行集成。目前的集成涵盖了加密货币交易所（CEX 和 DEX）、传统市场（外汇、股票、期货、期权）以及博彩交易所。

## 功能特性 (Features)

- **快速 (Fast)**：基于 Rust 核心，使用 [tokio](https://crates.io/crates/tokio) 实现异步网络。
- **可靠 (Reliable)**：由 Rust 提供类型和线程安全保障，支持可选的基于 Redis 的状态持久化。
- **可移植 (Portable)**：可在 Linux、macOS 和 Windows 上运行。支持使用 Docker 部署。
- **灵活 (Flexible)**：模块化适配器可集成任何 REST API 或 WebSocket 订阅源。
- **高级 (Advanced)**：生效时间 (Time in force) 支持 `IOC`, `FOK`, `GTC`, `GTD`, `DAY`, `AT_THE_OPEN`, `AT_THE_CLOSE`；支持高级订单类型和条件触发器。执行指令支持被动委托 (`post-only`)、只减仓 (`reduce-only`) 和冰山委托 (`icebergs`)。关联订单 (Contingency orders) 包括 `OCO`, `OUO`, `OTO`。
- **可自定义 (Customizable)**：用户可自定义组件，或使用 [缓存 (Cache)](cache.md) 和 [消息总线 (Message Bus)](message_bus.md) 从头开始组装整个系统。
- **回测 (Backtesting)**：同时对多个交易场所、合约 (Instruments) 和策略进行回测，支持历史报价 Tick、成交 Tick、K 线 (Bar)、订单簿以及纳秒分辨率的自定义数据。
- **实盘 (Live)**：研究与实盘部署之间的策略实现完全一致。
- **多场所 (Multi-venue)**：同时在多个交易场所运行做市和跨场所策略。
- **AI 训练 (AI training)**：引擎速度足以训练 AI 交易代理（强化学习 RL / 进化策略 ES）。

## 为什么选择 NautilusTrader？

交易策略研究通常在 Python 中使用向量化方法 (Vectorized approaches) 进行，而生产交易系统通常是在编译语言中使用事件驱动架构单独构建的。

NautilusTrader 消除了这种分离。

基于 Rust 的原生核心为研究和实盘执行提供了确定性的事件驱动运行时，而 Python 则作为控制平面。相同的架构、执行语义和时间模型在两个环境中运行，允许策略在不重新实现的情况下从研究阶段转移到生产阶段。

Python 绑定通过 [PyO3](https://pyo3.rs) 提供（目前正从 Cython 迁移）。安装时不需要 Rust 工具链。

## 使用场景 (Use cases)

本软件包主要有三个使用场景：

- 在历史数据上回测交易系统 (`backtest`)。
- 使用实时数据和虚拟执行模拟交易系统 (`sandbox`)。
- 在真实或模拟账户上实盘部署交易系统 (`live`)。

代码库提供了一个构建实现上述目标的软件层框架。默认的 `backtest` 和 `live` 系统实现在各自命名的子包中。可以使用沙盒适配器构建 `sandbox` 环境。

:::note

- 所有示例都将使用这些默认系统实现。
- 我们认为交易策略是端到端交易系统的子组件，这些系统包括应用层和基础设施层。

:::

## 分布式 (Distributed)

该平台可集成到更大的分布式系统中。几乎所有的配置和领域对象都使用 JSON、MessagePack 或 Apache Arrow (Feather) 进行序列化，以便通过网络通信。

## 通用核心 (Common core)

所有节点[环境上下文 (Environment contexts)](architecture.md#environment-contexts)（`backtest`、`sandbox` 和 `live`）都使用通用的系统核心。用户定义的执行器 (`Actor`)、策略 (`Strategy`) 和执行算法 (`ExecAlgorithm`) 组件在这些环境上下文中被一致地管理。

## 回测 (Backtesting)

直接或通过更高级别的 `BacktestNode` 和 `ParquetDataCatalog` 将数据提供给 `BacktestEngine`，然后以纳秒级分辨率运行系统。

## 实盘交易 (Live trading)

`TradingNode` 接入 (Ingest) 来自多个数据和执行客户端的数据和事件，支持模拟/纸单交易账户和真实账户。在单个[事件循环 (Event loop)](https://docs.python.org/3/library/asyncio-eventloop.html) 上异步运行可提供高性能，并可选择使用 [uvloop](https://github.com/MagicStack/uvloop) 实现（适用于 Linux 和 macOS）以获得额外的吞吐量。

## 领域模型 (Domain model)

该平台具有一个交易领域模型，其中包括各种数值类型（如 `Price` 和 `Quantity`），以及更复杂的实体（如 `Order` 和 `Position` 对象），这些实体用于聚合多个事件以确定状态。

## 时间戳 (Timestamps)

所有时间戳在 UTC 中都使用纳秒精度。

时间戳字符串遵循 ISO 8601 (RFC 3339) 格式，具有 9 位（纳秒）或 3 位（毫秒）的小数精度（但大多为纳秒），始终保留所有数字，包括末尾的零。这些可以在日志消息以及对象的调试/显示输出中看到。

一个时间戳字符串包含：

- 始终存在的完整日期组件：`YYYY-MM-DD`。
- 日期和时间组件之间的 `T` 分隔符。
- 始终为纳秒精度（9 位小数），或在某些情况（如 GTD 到期时间）下为毫秒精度（3 位小数）。
- 始终由 `Z` 后缀指定的 UTC 时区。

示例：`2024-01-05T15:30:45.123456789Z`

完整规范请参考 [RFC 3339: Date and Time on the Internet](https://datatracker.ietf.org/doc/html/rfc3339)。

## UUID (UUIDs)

平台使用通用唯一识别码 (UUID) 第 4 版 (RFC 4122) 作为唯一标识符。我们的高性能实现使用 `uuid` crate 在从字符串解析时进行正确性验证，确保输入的 UUID 符合规范。

一个有效的 UUID v4 包含：

- 分成 5 组显示的 32 位十六进制数字。
- 用连字符分隔的组：`8-4-4-4-12` 格式。
- 第 4 版指定（由第三组以 "4" 开头表示）。
- RFC 4122 变体指定（由第四组以 "8", "9", "a" 或 "b" 开头表示）。

示例：`2d89666b-1a1e-4a75-b193-4eb3b454c757`

完整规范请参考 [RFC 4122: A Universally Unique Identifier (UUID) URN Namespace](https://datatracker.ietf.org/doc/html/rfc4122)。

## 数据类型 (Data types)

以下市场数据类型可以进行历史请求，也可以在交易场所 / 数据提供商提供且在集成适配器中实现时，作为实时流进行订阅。

- `OrderBookDelta` (L1/L2/L3)
- `OrderBookDeltas` (容器类型)
- `OrderBookDepth10` (每侧 10 档固定深度)
- `QuoteTick` (报价 Tick)
- `TradeTick` (成交 Tick)
- `Bar` (K 线)
- `Instrument` (合约)
- `InstrumentStatus` (合约状态)
- `InstrumentClose` (合约收盘)

以下 `PriceType` (价格类型) 选项可用于 K 线聚合：

- `BID` (买入价)
- `ASK` (卖出价)
- `MID` (中间价)
- `LAST` (最新价)

## K 线聚合 (Bar aggregations)

提供以下 `BarAggregation` (K 线聚合) 方法：

- `MILLISECOND` (毫秒)
- `SECOND` (秒)
- `MINUTE` (分钟)
- `HOUR` (小时)
- `DAY` (天)
- `WEEK` (周)
- `MONTH` (月)
- `YEAR` (年)
- `TICK` (跳动)
- `VOLUME` (成交量)
- `VALUE` (成交额，又称美元 Bar)
- `RENKO` (基于价格的砖块)
- `TICK_IMBALANCE` (Tick 失衡)
- `TICK_RUNS` (Tick 运行)
- `VOLUME_IMBALANCE` (成交量失衡)
- `VOLUME_RUNS` (成交量运行)
- `VALUE_IMBALANCE` (成交额失衡)
- `VALUE_RUNS` (成交额运行)

上述所有聚合均已在内部聚合中实现。信息驱动的聚合需要 `TradeTick` 数据。

价格类型和 K 线聚合可以通过 `BarSpecification` (K 线规范) 以步长 >= 1 的任何方式进行组合。这允许在实盘交易中聚合替代性 K 线 (Alternative bars)。

## 账户类型 (Account types)

以下账户类型在实盘和回测环境中均可用：

- `Cash` (现金) 单货币（基准货币）
- `Cash` (现金) 多货币
- `Margin` (保证金) 单货币（基准货币）
- `Margin` (保证金) 多货币
- `Betting` (博彩) 单货币

## 订单类型 (Order types)

提供以下订单类型（如果交易场所支持）：

- `MARKET` (市价单)
- `LIMIT` (限价单)
- `STOP_MARKET` (止损市价单)
- `STOP_LIMIT` (止损限价单)
- `MARKET_TO_LIMIT` (市价转限价单)
- `MARKET_IF_TOUCHED` (触达市价单)
- `LIMIT_IF_TOUCHED` (触达限价单)
- `TRAILING_STOP_MARKET` (追踪止损市价单)
- `TRAILING_STOP_LIMIT` (追踪止损限价单)

## 数值类型 (Value types)

以下数值类型由 128 位或 64 位原始整数值支持，具体取决于编译期间使用的[精度模式 (Precision mode)](../getting_started/installation.md#precision-mode)。

- `Price` (价格)
- `Quantity` (数量)
- `Money` (资金)

### 高精度模式 (High-precision mode, 128-bit)

当 `high-precision` 功能标志**启用**（默认）时，数值使用以下规范：

| 类型 | 原始支持 | 最大精度 | 最小值 | 最大值 |
|:-------------|:------------|:--------------|:--------------------|:-------------------|
| `Price` | `i128` | 16 | -17,014,118,346,046 | 17,014,118,346,046 |
| `Money` | `i128` | 16 | -17,014,118,346,046 | 17,014,118,346,046 |
| `Quantity` | `u128` | 16 | 0 | 34,028,236,692,093 |

### 标准精度模式 (Standard-precision mode, 64-bit)

当 `high-precision` 功能标志**禁用**时，数值使用以下规范：

| 类型 | 原始支持 | 最大精度 | 最小值 | 最大值 |
|:-------------|:------------|:--------------|:--------------------|:-------------------|
| `Price` | `i64` | 9 | -9,223,372,036 | 9,223,372,036 |
| `Money` | `i64` | 9 | -9,223,372,036 | 9,223,372,036 |
| `Quantity` | `u64` | 9 | 0 | 18,446,744,073 |
