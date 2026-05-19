# 核心概念 (Concepts)

这些指南解释了 NautilusTrader 的核心组件、架构和设计。

## 概览 (Overview)

平台的主要功能和预期使用场景。

## 架构 (Architecture)

支撑平台的原则、结构和设计。

## 执行器 (Actors)

`Actor` 是与交易系统交互的基础组件。涵盖了其功能和实现细节。

## 策略 (Strategies)

如何使用 `Strategy` 组件实现交易策略。

## 合约 (Instruments)

可交易资产和合约 (Contract) 的定义。

## 合成合约 (Synthetics)

用户定义的合约，其价格通过计算各组件合约价格的数值表达式得出。

## 连续期货 (Continuous Futures)

通过显式的展期表 (Roll Table) 将连续的期货合约拼接成一个调整后的 K 线序列，包括四种调整模式、请求和订阅流程，以及 K 线中途展期边界策略。

## 数值类型 (Value Types)

平台各处使用的不可变数值类型 (`Price`, `Quantity`, `Money`)，包括它们的算术行为、精度处理和类型特定约束。

## 数据 (Data)

交易领域的内置数据类型，以及如何处理自定义数据。

## 事件 (Events)

驱动系统的事件类型：订单事件、仓位事件、账户事件和时间事件。涵盖处理器分发、从订单成交到仓位事件的因果链，以及从订单到仓位的追踪。

## 期权 (Options)

期权合约类型、交易场所提供的希腊字母 (Greeks) 流、带行权价范围过滤的期权链订阅以及快照聚合。

## 希腊字母 (Greeks)

通过两条路径获取的期权希腊字母 (delta, gamma, vega, theta)：通过 Rust/PyO3 `OptionGreeks` 类型获取的交易场所提供的实时希腊字母，以及用于 Black-Scholes 计算（含压力测试场景、Beta 权重和投资组合聚合）的本地 `GreeksCalculator`。

## 自定义数据 (Custom Data)

自定义数据系统在 Python 和 Rust 中的工作方式：注册、持久化、Arrow 编码以及通过执行器 (Actor) 和策略 (Strategy) 进行的运行时路由。

## 订单簿 (Order Book)

高性能订单簿、自有订单追踪、净流动性过滤视图以及二元市场 (Binary Market) 支持。

## 执行 (Execution)

在多个策略和交易场所同时（每个实例）进行交易执行和订单管理，包括涉及的组件和执行消息（命令和事件）的流程。

## 订单 (Orders)

可用的订单类型、支持的执行指令、高级订单类型和模拟订单。

## 仓位 (Positions)

仓位生命周期、订单成交后的聚合、盈亏 (PnL) 计算以及用于净额结算 OMS 配置的仓位快照。

## 缓存 (Cache)

`Cache` 是所有交易相关数据的中央内存存储。涵盖了其功能和最佳实践。

## 消息总线 (Message Bus)

`MessageBus` 实现了组件之间的解耦消息传递，支持点对点 (Point-to-Point)、发布/订阅 (Publish/Subscribe) 和请求/响应 (Request/Response) 模式。

## 账户核算 (Accounting)

账户类型（现金、保证金、博彩）、`AccountBalance` 和 `MarginBalance` 数据模型、按合约与按账户的保证金范围、策略查询 API、内置保证金模型以及跨实盘交易场所的适配器惯例。

## 投资组合 (Portfolio)

`Portfolio` 追踪跨策略和合约的所有仓位，提供持仓、风险敞口和绩效的统一视图。

## 报告 (Reports)

执行报告、投资组合分析、盈亏核算以及回测运行后的分析。

## 日志 (Logging)

在 Rust 中实现的高性能日志系统，适用于回测和实盘交易。

## 回测 (Backtesting)

使用特定的系统实现在历史数据上运行模拟交易。

## 可视化 (Visualization)

用于分析回测结果的交互式绩效图表 (Tearsheet)，包括图表、主题、自定义选项以及通过可扩展图表注册表实现的自定义可视化。

## 配置 (Configuration)

配置结构体在 Python 和 Rust 中的工作方式：默认解析、`T` 与 `Option<T>` 惯例、建造者模式 (Builder patterns) 以及跨适配器和引擎共享的通用字段。

## 实盘交易 (Live Trading)

在不更改代码的情况下实时部署经过回测的策略，以及回测与实盘交易之间的关键区别。

## 适配器 (Adapters)

开发数据提供商和交易场所集成适配器的要求和最佳实践。

## Rust

直接使用 `crates/` 实现，在纯 Rust 中编写执行器 (Actor)、策略 (Strategy) 并运行回测和实盘交易。

## 确定性模拟测试 (DST)

用于种子可重现执行的确定性合约、实现该合约的源码级接缝、强制执行该合约的 pre-commit 钩子，以及已知的范围边界。

:::note
如果这些指南与 API 参考 (API Reference) 之间存在差异，请以 API 参考为准。
:::
