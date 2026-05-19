# 操作指南 (How-to Guides)

面向目标的常见任务秘籍。每本指南都假设您熟悉 Nautilus 的概念，并专注于实现特定的目标。

刚接触 Nautilus？请先从[入门指南 (Getting Started)](../getting_started/)路径和[教程 (Tutorials)](../tutorials/)开始。

## 数据工作流 (Data workflows)

| 指南 | 描述 |
|:------------------------------------------------------|:-----------------------------------------------|
| [加载外部数据 (Loading external data)][loading_external_data] | 将 CSV 数据加载到 Parquet 数据目录 (Data Catalog) 中。 |
| [使用 Databento 的数据目录 (Data catalog with Databento)][data_catalog_databento] | 使用 Databento 市场数据设置目录。 |

## 实盘交易 (Live trading)

| 指南 | 描述 |
|:--------------------------------------------------------------|:--------------------------------------------------------|
| [配置实盘交易节点 (Configure a live trading node)](configure_live_trading) | 设置 TradingNodeConfig、执行引擎和场所。 |

## Rust

| 指南 | 描述 |
|:----------------------------------------------------------|:-------------------------------------------------------|
| [编写执行器 (Actor) (Rust)](write_rust_actor) | 构建具有订阅和处理程序的数据执行器 (Actor)。 |
| [编写策略 (Strategy) (Rust)](write_rust_strategy) | 构建具有订单管理的策略 (Strategy)。 |
| [运行回测 (Backtest) (Rust)](run_rust_backtest) | 使用 BacktestEngine 或 BacktestNode 配合目录运行。 |
| [运行实盘交易 (Live Trading) (Rust)](run_rust_live_trading) | 使用 LiveNode 连接到场所。 |

[loading_external_data]: loading_external_data.py
[data_catalog_databento]: data_catalog_databento.py
