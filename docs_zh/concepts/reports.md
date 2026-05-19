# 报告 (Reports)

本指南介绍了 `ReportProvider` 类提供的投资组合 (Portfolio) 分析和报告功能，以及这些报告如何用于损益 (PnL) 会计和回测 (Backtesting) 运行后的分析。

## 概览 (Overview)

NautilusTrader 中的 `ReportProvider` 类根据交易数据生成结构化的分析报告，将原始订单、成交、持仓和账户状态转换为 pandas DataFrame，以便进行分析和可视化 (Visualization)。这些报告可帮助您评估策略 (Strategy) 绩效、分析执行质量并验证损益 (PnL) 会计。

可以使用两种方法生成报告：

- **Trader 辅助方法**（推荐）：便捷的方法，如 `trader.generate_orders_report()`。
- **直接使用 ReportProvider**：为了对数据选择和过滤进行更多控制。

这些报告在回测 (Backtesting) 和实盘交易 (Live Trading) 环境中提供一致的分析，从而实现可靠的绩效评估和策略比较。

## 可用报告 (Available reports)

`ReportProvider` 类提供了多个静态方法，用于根据交易数据生成报告。每份报告都返回一个 pandas DataFrame，其中包含特定的列和索引，以便于分析。

### 订单报告 (Orders report)

生成所有订单的完整视图：

```python
# 使用 Trader 辅助方法 (推荐)
orders_report = trader.generate_orders_report()

# 或直接使用 ReportProvider
from nautilus_trader.analysis import ReportProvider

orders = cache.orders()
orders_report = ReportProvider.generate_orders_report(orders)
```

**返回 `pd.DataFrame`。关键列包括：**

| 列名               | 描述                                                    |
|--------------------|---------------------------------------------------------|
| `client_order_id`  | 索引 - 唯一的订单标识符。                               |
| `instrument_id`    | 交易合约 (Instrument)。                                 |
| `strategy_id`      | 创建该订单的策略 (Strategy)。                           |
| `trader_id`        | 交易者标识符。                                          |
| `account_id`       | 账户标识符（如果已分配）。                             |
| `venue_order_id`   | 场内分配的订单 ID（如果已接受）。                      |
| `side`             | 买入 (BUY) 或卖出 (SELL)。                              |
| `type`             | 市价 (MARKET)、限价 (LIMIT) 等。                        |
| `status`           | 当前订单状态。                                          |
| `quantity`         | 原始订单数量（字符串）。                                |
| `filled_qty`       | 已成交数量（字符串）。                                  |
| `price`            | 限价（取决于订单类型）。                                |
| `avg_px`           | 平均成交价格（如果已成交）。                            |
| `time_in_force`    | 订单有效期指令。                                        |
| `ts_init`          | 订单初始化时间戳（Unix 纳秒）。                        |
| `ts_last`          | 最后更新时间戳（Unix 纳秒）。                          |

其他列因订单类型而异（例如，止损单的 `trigger_price`，GTD 订单的 `expire_time`）。有关完整字段列表，请参阅 `Order.to_dict()`。

### 订单成交报告 (Order fills report)

提供已成交订单的摘要（每笔订单一行）：

```python
# 使用 Trader 辅助方法 (推荐)
fills_report = trader.generate_order_fills_report()

# 或直接使用 ReportProvider
orders = cache.orders()
fills_report = ReportProvider.generate_order_fills_report(orders)
```

该报告仅包含 `filled_qty > 0` 的订单，并包含与订单报告相同的列，但过滤为仅限已执行的订单。请注意，此报告中的 `ts_init` 和 `ts_last` 已转换为 datetime 对象，以便于分析。

### 成交细节报告 (Fills report)

详细记录单个成交事件（每次成交一行）：

```python
# 使用 Trader 辅助方法 (推荐)
fills_report = trader.generate_fills_report()

# 或直接使用 ReportProvider
orders = cache.orders()
fills_report = ReportProvider.generate_fills_report(orders)
```

**返回 `pd.DataFrame`。关键列包括：**

| 列名               | 描述                                     |
|--------------------|------------------------------------------|
| `client_order_id`  | 索引 - 订单标识符。                      |
| `trade_id`         | 唯一的交易/成交标识符。                  |
| `venue_order_id`   | 场内分配的订单 ID。                      |
| `instrument_id`    | 交易合约。                               |
| `strategy_id`      | 创建该订单的策略。                       |
| `account_id`       | 账户标识符。                             |
| `position_id`      | 关联的持仓 ID（如果适用）。              |
| `order_side`       | 买入 (BUY) 或卖出 (SELL)。               |
| `order_type`       | 订单类型（市价、限价等）。               |
| `last_px`          | 成交执行价格（字符串）。                 |
| `last_qty`         | 成交执行数量（字符串）。                 |
| `currency`         | 成交货币。                               |
| `liquidity_side`   | 挂单 (MAKER) 或吃单 (TAKER)。            |
| `commission`       | 佣金金额和货币。                         |
| `ts_event`         | 成交时间戳 (datetime)。                  |
| `ts_init`          | 初始化时间戳 (datetime)。                |

有关完整字段列表，请参阅 `OrderFilled.to_dict()`。

### 持仓报告 (Positions report)

包含快照的持仓分析：

```python
# 使用 Trader 辅助方法 (推荐)
# 对于 NETTING OMS 自动包含快照
positions_report = trader.generate_positions_report()

# 或直接使用 ReportProvider
positions = cache.positions()
snapshots = cache.position_snapshots()  # 对于 NETTING OMS
positions_report = ReportProvider.generate_positions_report(
    positions=positions,
    snapshots=snapshots
)
```

**返回 `pd.DataFrame`。关键列包括：**

| 列名               | 描述                                     |
|--------------------|------------------------------------------|
| `position_id`      | 索引 - 唯一的持仓标识符。                |
| `instrument_id`    | 交易合约。                               |
| `strategy_id`      | 管理该持仓的策略。                       |
| `trader_id`        | 交易者标识符。                           |
| `account_id`       | 账户标识符。                             |
| `opening_order_id` | 开启该持仓的订单 ID。                    |
| `closing_order_id` | 关闭该持仓的订单 ID。                    |
| `entry`            | 入场方向（买入或卖出）。                 |
| `side`             | 持仓方向（多头 LONG、空头 SHORT 或空仓 FLAT）。 |
| `quantity`         | 当前持仓规模。                           |
| `peak_qty`         | 达到的最大规模。                         |
| `avg_px_open`      | 平均开仓价格。                           |
| `avg_px_close`     | 平均平仓价格（如果已平仓）。             |
| `commissions`      | 已支付的佣金列表。                       |
| `realized_pnl`     | 已实现损益。                             |
| `realized_return`  | 收益百分比。                             |
| `ts_init`          | 持仓初始化时间戳。                       |
| `ts_opened`        | 开仓时间戳 (datetime)。                  |
| `ts_last`          | 最后更新时间戳。                         |
| `ts_closed`        | 平仓时间戳 (datetime 或 NA)。            |
| `duration_ns`      | 持仓持续时间（纳秒）。                   |
| `is_snapshot`      | 这是否为历史快照。                       |

### 账户报告 (Account report)

跟踪账户余额和保证金随时间的变化：

```python
# 使用 Trader 辅助方法 (推荐)
# 需要 venue 参数
from nautilus_trader.model.identifiers import Venue
venue = Venue("BINANCE")
account_report = trader.generate_account_report(venue)

# 或直接使用 ReportProvider
account = cache.account(account_id)
account_report = ReportProvider.generate_account_report(account)
```

**返回 `pd.DataFrame`。列名包括：**

| 列名            | 描述                                     |
|-----------------|------------------------------------------|
| `ts_event`      | 索引 - 账户状态变化的时间戳。            |
| `account_id`    | 账户标识符。                             |
| `account_type`  | 账户类型（例如 SPOT, MARGIN）。          |
| `base_currency` | 账户的基础货币。                         |
| `total`         | 总余额（字符串）。                       |
| `free`          | 可用余额（字符串）。                     |
| `locked`        | 订单锁定的余额（字符串）。               |
| `currency`      | 余额的货币。                             |
| `reported`      | 余额是否由场内报告。                     |
| `margins`       | 保证金信息（列表，如果适用）。           |
| `info`          | 其他场内特有信息。                       |

每一行代表一个余额条目；具有多种货币的账户在每个账户状态事件中会产生多行。

## 损益 (PnL) 会计考量

准确的损益 (PnL) 会计需要仔细考虑几个因素：

### 基于持仓的损益 (PnL)

- **已实现损益 (Realized PnL)**：在持仓部分或全部关闭时计算。
- **未实现损益 (Unrealized PnL)**：使用当前价格计算的账面价值 (Marked-to-market)。
- **佣金影响**：仅在结算货币中包含。

:::warning
损益 (PnL) 计算取决于订单管理系统 (OMS) 的类型。在 `NETTING`（净仓）OMS 中，持仓快照会在持仓重新开启时保留历史损益。为了准确计算总损益，请务必在报告中包含快照。在 `HEDGING`（对冲）OMS 中，不使用快照，因为每个持仓都有唯一的 ID 且永不重新开启。
:::

### 多货币会计 (Multi-currency accounting)

处理多种货币时：

- 每个持仓以其结算货币跟踪损益。
- 投资组合 (Portfolio) 汇总需要货币转换。
- 佣金货币可能与结算货币不同。

```python
# 访问各持仓的损益
for position in positions:
    realized = position.realized_pnl  # 以结算货币表示
    unrealized = position.unrealized_pnl(last_price)

    # 处理多货币汇总 (示例)
    # 注意: 货币转换需要用户提供的汇率
    if position.settlement_currency != base_currency:
        # 从您的数据源应用转换汇率
        # rate = get_exchange_rate(position.settlement_currency, base_currency)
        # realized_converted = realized.as_double() * rate
        pass
```

### 快照考量 (Snapshot considerations)

对于 `NETTING`（净仓）OMS：

```python
from nautilus_trader.model.objects import Money

# 包含快照以获得完整的损益 (每种货币)
pnl_by_currency = {}

# 添加来自当前持仓的损益
for position in cache.positions(instrument_id=instrument_id):
    if position.realized_pnl:
        currency = position.realized_pnl.currency
        if currency not in pnl_by_currency:
            pnl_by_currency[currency] = 0.0
        pnl_by_currency[currency] += position.realized_pnl.as_double()

# 添加来自历史快照的损益
for snapshot in cache.position_snapshots(instrument_id=instrument_id):
    if snapshot.realized_pnl:
        currency = snapshot.realized_pnl.currency
        if currency not in pnl_by_currency:
            pnl_by_currency[currency] = 0.0
        pnl_by_currency[currency] += snapshot.realized_pnl.as_double()

# 为每种货币创建 Money 对象
total_pnls = [Money(amount, currency) for currency, amount in pnl_by_currency.items()]
```

## 回测 (Backtesting) 运行后分析

在回测完成后，可以通过各种报告和投资组合分析器进行分析。

### 访问回测结果

```python
# 回测运行后
engine.run(start=start_time, end=end_time)

# 使用 Trader 辅助方法生成报告
orders_report = engine.trader.generate_orders_report()
positions_report = engine.trader.generate_positions_report()
fills_report = engine.trader.generate_fills_report()

# 或直接访问数据进行自定义分析
orders = engine.cache.orders()
positions = engine.cache.positions()
snapshots = engine.cache.position_snapshots()
```

### 投资组合统计 (Portfolio statistics)

投资组合分析器提供绩效指标 (Metrics)：

```python
# 访问投资组合分析器
portfolio = engine.portfolio

# 获取不同类别的统计数据
stats_pnls = portfolio.analyzer.get_performance_stats_pnls()
stats_returns = portfolio.analyzer.get_performance_stats_returns()
stats_general = portfolio.analyzer.get_performance_stats_general()
```

:::info
有关可用统计数据和创建自定义指标的详细信息，请参阅[投资组合指南](portfolio.md#portfolio-statistics)。该指南涵盖了：

- 内置统计类别（基于损益 PnL、收益、持仓、订单）。
- 使用 `PortfolioStatistic` 创建自定义统计。
- 注册和使用自定义指标。

:::

### 可视化 (Visualization)

NautilusTrader 通过 Plotly 提供交互式绩效图表 (Tear sheets) 和绘图：

```python
from nautilus_trader.analysis import create_tearsheet

# 回测运行后
engine.run()

# 生成交互式 HTML 绩效图表
create_tearsheet(engine, output_path="tearsheet.html")
```

这将生成一份包含以下内容的交互式 HTML 报告：

- 权益曲线 (Equity curve)
- 回撤 (Drawdown) 分析
- 月度收益热力图
- 绩效统计表
- 收益分布

如需更多控制，可生成单个图表：

```python
from nautilus_trader.analysis import create_equity_curve

returns = engine.portfolio.analyzer.returns()
fig = create_equity_curve(returns, title="我的策略权益")
fig.show()  # 在浏览器中显示
fig.write_image("equity.png")  # 导出为 PNG (需要 kaleido)
```

安装可视化依赖：

```bash
uv pip install "nautilus_trader[visualization]"
```

## 报告生成模式

### 实盘交易 (Live trading)

在实盘交易期间，定期生成报告：

```python
import pandas as pd

class ReportingActor(Actor):
    def on_start(self):
        # 安排定期报告
        self.clock.set_timer(
            name="generate_reports",
            interval=pd.Timedelta(minutes=30),
            callback=self.generate_reports
        )

    def generate_reports(self, event):
        # 生成并记录报告
        positions_report = self.trader.generate_positions_report()

        # 保存或传输报告
        positions_report.to_csv(f"positions_{event.ts_event}.csv")
```

### 绩效分析 (Performance analysis)

用于回测分析：

```python
import pandas as pd

# 运行回测
engine.run(start=start_time, end=end_time)

# 收集结果
positions_closed = engine.cache.positions_closed()
stats_pnls = engine.portfolio.analyzer.get_performance_stats_pnls()
stats_returns = engine.portfolio.analyzer.get_performance_stats_returns()
stats_general = engine.portfolio.analyzer.get_performance_stats_general()

# 创建摘要字典
results = {
    "total_positions": len(positions_closed),
    "pnl_total": stats_pnls.get("PnL (total)"),
    "sharpe_ratio": stats_returns.get("Sharpe Ratio (252 days)"),
    "profit_factor": stats_general.get("Profit Factor"),
    "win_rate": stats_general.get("Win Rate"),
}

# 显示结果
results_df = pd.DataFrame([results])
print(results_df.T)  # 转置以垂直显示
```

:::info
报告是根据内存中的数据结构生成的。对于大规模分析或长期运行的系统，请考虑将报告持久化到数据库中以便高效查询。有关持久化选项，请参阅[缓存 (Cache) 指南](cache.md)。
:::

## 与其他组件的集成

`ReportProvider` 与多个系统组件协作：

- **缓存 (Cache)**：报告的所有交易数据（订单、持仓、账户）的来源。
- **投资组合 (Portfolio)**：使用报告进行绩效分析和指标计算。
- **回测引擎 (BacktestEngine)**：使用报告进行运行后分析和可视化 (Visualization)。
- **持仓快照 (Position snapshots)**：在 `NETTING` OMS 中准确报告损益 (PnL) 所必需。

## 总结 (Summary)

`ReportProvider` 将订单、成交、持仓和账户状态生成为结构化的 DataFrame，用于分析和可视化 (Visualization)。为了在 `NETTING` OMS 中获得准确的总损益，在生成报告时应包含持仓快照。

## 相关指南

- [可视化 (Visualization)](visualization.md) - 根据回测结果生成交互式绩效图表和图表。
- [投资组合 (Portfolio)](portfolio.md) - 投资组合统计和绩效指标。
- [回测 (Backtesting)](backtesting.md) - 运行生成报告的回测。
- [缓存 (Cache)](cache.md) - 存储报告所需数据的缓存系统。
