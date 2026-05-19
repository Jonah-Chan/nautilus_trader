# 投资组合 (Portfolio)

投资组合 (Portfolio) 是管理和跟踪交易节点或回测 (Backtesting) 中所有活跃策略仓位的核心枢纽。
它整合了来自多个合约 (Instrument) 的仓位数据，为您提供持仓、风险敞口 (Exposure) 和整体业绩的统一视图。

## 货币转换 (Currency conversion)

投资组合 (Portfolio) 支持对盈亏 (PnL) 和风险敞口 (Exposure) 计算进行自动货币转换，
允许您以首选货币查看结果。这在交易结算货币不同的多个合约 (Instrument) 或管理基本货币不同的多个账户时特别有用。

### 支持的转换 (Supported conversions)

货币转换适用于以下投资组合 (Portfolio) 查询：

- `realized_pnl()` / `realized_pnls()` - 将已实现盈亏 (Realized PnL) 转换为目标货币。
- `unrealized_pnl()` / `unrealized_pnls()` - 将未实现盈亏 (Unrealized PnL) 转换为目标货币。
- `total_pnl()` / `total_pnls()` - 将总盈亏 (Total PnL) 转换为目标货币。
- `net_exposure()` / `net_exposures()` - 将净风险敞口 (Net exposure) 转换为目标货币。

所有方法都接受一个可选的 `target_currency` 参数来指定所需的输出货币。

### 单账户行为 (Single account behavior)

在查询单个账户且未指定 `target_currency` 时，投资组合 (Portfolio) 会自动将数值转换为该账户的基本货币：

```python
# 返回该账户基本货币（例如 USD）的风险敞口 (Exposure)
exposure = portfolio.net_exposures(venue=BINANCE, account_id=account_id)
```

### 多账户行为 (Multi-account behavior)

同时查询多个账户时，其行为取决于您是查询所有合约 (Instrument) (`net_exposures()`) 还是单个合约 (Instrument) (`net_exposure()`)：

**对于 `net_exposures()`（所有合约）：**

- **相同基本货币**：自动转换为通用的基本货币。
- **不同基本货币**：返回一个包含多种货币的字典，每种货币都转换为其账户的基本货币。若需单一货币结果，请提供 `target_currency`。

**对于 `net_exposure()`（跨账户的单个合约）：**

- **不同基本货币**：除非您提供 `target_currency`，否则返回 `None`。

```python
# 场景 1：多个账户，基本货币均为 USD
exposures = portfolio.net_exposures(venue=BINANCE)
# 返回 {USD: Money(...)}

# 场景 2：多个账户，基本货币不同（USD 和 EUR）
exposures = portfolio.net_exposures(venue=BINANCE)
# 返回 {USD: Money(...), EUR: Money(...)}

# 强制跨账户使用单一货币
exposures = portfolio.net_exposures(venue=BINANCE, target_currency=USD)
# 返回 {USD: Money(...)}
```

### 转换失败 (Conversion failures)

当提供了 `target_currency` 但货币转换失败时，行为取决于方法类型：

- **单值方法** (`realized_pnl`, `unrealized_pnl`, `total_pnl`, `net_exposure`):
  返回 `None` 并记录错误，以防止出现错误数值。
- **返回字典的方法** (`realized_pnls`, `unrealized_pnls`, `total_pnls`, `net_exposures`):
  忽略转换失败的合约 (Instrument)，但返回转换成功的合约结果。

:::warning 警告
在跨货币聚合使用 `target_currency` 时，汇率数据必须可用。
:::

### 转换价格类型 (Conversion price types)

在将风险敞口 (Exposure) 转换为目标货币时，投资组合 (Portfolio) 根据持仓组成使用不同的价格类型：

- **全是多头仓位**：使用 `BID` 价格（多头风险敞口的保守估算）。
- **全是空头仓位**：使用 `ASK` 价格（空头风险敞口的保守估算）。
- **混合仓位**：使用 `MID` 价格（多头和空头并存时的中性估算）。

这确保了转换能反映现实的市场条件，即您将以买入价 (Bid) 平仓多头，以卖出价 (Ask) 覆盖空头。对于混合持仓，中间价 (Mid-pricing) 提供了一个中性的估值。

如果在投资组合配置中启用了 `use_mark_xrates`，则对于混合持仓和通用转换，`MARK` 价格将取代 `MID` 价格。

## 权益和市值计价 (Equity and mark-to-market)

投资组合 (Portfolio) 提供了三种拉取式查询，用于持续的投资组合估值。每种查询都返回按相关账户基本货币或原生结算货币键入的每种货币结果。

| 方法 (Method)                             | 返回 (Returns)                                                   |
|------------------------------------------|------------------------------------------------------------------|
| `mark_values(venue, account_id)`         | 未平仓仓位的带正负号的市值计价 (MTM) 总计。                            |
| `equity(venue, account_id)`              | 结合账户余额 (Account balance) 和仓位估值的总权益。                  |
| `missing_price_instruments(venue)`       | 当前被标记为无法定价的合约 (Instrument)。                           |

多头贡献正名义价值，空头贡献负名义价值。空仓状态会被跳过。

### 权益公式 (Equity formula)

权益 (Equity) 将账户余额 (Account balance) 与未平仓仓位估值相结合，根据账户类型使用不同的第二项：

- **现货 (Cash) 和博彩 (Betting) 账户**：`balances_total + Σ mark_value(未平仓仓位)`。
- **保证金 (Margin) 账户**：`balances_total + Σ unrealized_pnl(未平仓仓位)`。

现货和博彩路径内部使用 `mark_values()`。保证金路径使用与 `unrealized_pnls()` 相同的缓存未实现盈亏管道。

### 价格回退机制 (Price fallback)

估值按以下顺序向 `Cache` 请求价格，并在第一个匹配项处停止：

1. 标记价格 (Mark price)，如果在 `PortfolioConfig` 中 `use_mark_prices=true` 且缓存了标记价格。
2. 方向适配的报价：多头使用 `BID`，空头使用 `ASK`。
3. 最新成交价 (Last trade price)。
4. 最近的缓存 K 线收盘价 (Cached bar close)（当 `bar_updates=true` 时填充）。

如果这四种方式都无法获取价格，该仓位将进入缺失价格跟踪器，并在求和中被跳过。

### 基本货币转换 (Base currency conversion)

当 `convert_to_account_base_currency=true`（默认值）且账户设置了 `base_currency` 时，结算货币数值将使用来自 `Cache.get_xrate()` 的 `MID` 汇率转换为基本货币。若 `use_mark_xrates=true`，则优先使用来自 `Cache.get_mark_xrate()` 的缓存标记汇率，若不可用则回退到 `MID`。输出字典将包含一个与基本货币匹配的唯一键。

当 `convert_to_account_base_currency=false` 或账户未设置 `base_currency` 时，结果将按每个持仓的原生结算货币键入，且不进行汇率转换。

如果所需的转换缺乏汇率数据，该仓位将被视为无法定价，并通过缺失价格跟踪器进行标记，而不是静默地按 1.0 的汇率计价。

### 缺失价格跟踪 (Missing-price tracking)

跟踪器是每个交易平台 (Venue) 的合约 ID 集合，这些合约在上次调用 `mark_values()` 或 `equity()` 时无法定价。它有两种可观察的行为：

- 在从“有定价”转换为“无定价”时，每个合约会触发一次警告日志，而不是在每次后续调用中都触发。下一次转换回“有定价”时会清除该条目，以便未来再次丢失定价时重新发出警告。
- 当一个交易平台 (Venue) 处于空仓状态（无未平仓仓位）时，其跟踪器条目将被清除，因此陈旧的合约不会保持被标记状态。

调用 `missing_price_instruments(venue)` 来检查当前集合。

:::tip 提示
如果 `equity()` 的结果低于您的预期，在调查数学计算之前，请先检查 `missing_price_instruments(venue)`。某个合约的报价、成交和 K 线推送为空是导致静默缺口的最常见原因。
:::

### 交易平台和账户范围 (Venue and account scope)

`mark_values` and `equity` 接受可选的 `account_id` 以将聚合范围限制在单个账户。如果 `account_id=None`，结果将聚合该交易平台 (Venue) 上的每个账户。

缺失价格跟踪器是交易平台范围的。`missing_price_instruments` 仅接收交易平台参数，按账户过滤的 `mark_values(venue, account_id)` 调用不会清除交易平台条目，因此由同一交易平台上其他账户引发的标记将继续存在。

## 投资组合统计 (Portfolio statistics)

有各种[内置的投资组合统计指标](https://github.com/nautechsystems/nautilus_trader/tree/develop/crates/analysis/src/statistics)，用于分析回测 (Backtesting) 和实盘交易的投资组合表现。

统计指标通常分为以下几类：

- 基于盈亏 (PnL) 的统计指标（按货币）
- 基于回报 (Returns) 的统计指标
- 基于仓位 (Positions) 的统计指标
- 基于订单 (Orders) 的统计指标

您也可以调用交易者的 `PortfolioAnalyzer` 在任何任意时间计算统计指标，包括在回测或实盘交易期间。

## 自定义统计指标 (Custom statistics)

可以通过继承 `PortfolioStatistic` 基类并实现任何 `calculate_` 方法来定义自定义投资组合统计指标。

例如，以下是内置 `WinRate`（胜率）统计指标的实现：

```python
import pandas as pd
from typing import Any
from nautilus_trader.analysis.statistic import PortfolioStatistic


class WinRate(PortfolioStatistic):
    """
    从已实现盈亏序列计算胜率。
    """

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        # 前提条件
        if realized_pnls is None or realized_pnls.empty:
            return 0.0

        # 计算统计指标
        winners = [x for x in realized_pnls if x > 0.0]
        losers = [x for x in realized_pnls if x <= 0.0]

        return len(winners) / float(max(1, (len(winners) + len(losers))))
```

然后可以将这些统计指标注册到交易者的 `PortfolioAnalyzer`。

```python
stat = WinRate()

# 注册到投资组合分析器
engine.portfolio.analyzer.register_statistic(stat)
```

有关所有可用方法，请参阅 [`PortfolioAnalyzer` API 参考文档](/docs/python-api-latest/analysis.html#nautilus_trader.analysis.analyzer.PortfolioAnalyzer)。

:::tip 提示
您的统计指标应处理异常输入，如 `None`、空序列或数据不足。对于未知/无法计算的数值返回 `None`，或在语义适当时返回合理的默认值（例如，无交易时的胜率为 `0.0`）。
:::

## 回报率：仓位 vs 投资组合 (Returns: position vs portfolio)

分析器跟踪两个不同的回报序列：

- **仓位回报 (Position returns)** (`analyzer.position_returns()`) 衡量每个仓位的已实现回报，作为相对于平均开仓价的考虑方向的价格回报。这反映了合约 (Instrument) 在入场和出场之间的价格变动，与账户大小或杠杆 (Leverage) 无关。
- **投资组合回报 (Portfolio returns)** (`analyzer.portfolio_returns()`) 衡量总账户余额 (Account balance) 的每日百分比变化。在 100,000 美元的账户中获得 900 美元的收益，当天报告的回报率约为 0.9%。

当分析器拥有跨越至少两个不同日历日的账户状态历史时，它会自动计算投资组合回报，并将其作为统计指标、业绩分析表 (Tearsheet) 和月度回报热力图的主要序列。同一天内的多个快照计为一天，因此仅日内交易不会产生投资组合回报。当投资组合回报不可用时，它会回退到仓位回报。

便捷访问器 `analyzer.returns()` 解决了这种偏好：存在时使用投资组合回报，否则使用仓位回报。

### 多货币账户 (Multi-currency accounts)

投资组合回报需要单一货币的余额历史。当账户持有多种货币的余额时，分析器无法生成单一的回报序列，会静默回退到仓位回报。统计指标和业绩分析表图表使用 `returns()` 解析出的任何序列。

如果您需要多货币账户的投资组合级别回报，请在计算百分比变化之前将余额转换为通用的基本货币，在外部进行计算。

### 按交易平台计算 (Per-venue calculation)

在回测引擎中，分析器按交易平台 (Venue) 运行 (`engine.pyx`)。每个交易平台的账户都会产生自己的投资组合回报序列。业绩分析表会汇总所有缓存账户，为多交易平台回测生成组合回报序列。

## 回测分析 (Backtest analysis)

回测运行后，引擎会将已实现盈亏 (Realized PnLs)、回报 (Returns)、仓位 (Positions) 和订单 (Orders) 数据传递给每个注册的统计指标。任何输出都将显示在业绩分析表 (Tearsheet) 的 `Portfolio Performance` 标题下，分组如下：

- 已实现盈亏统计指标（按货币）
- 回报统计指标（针对整个投资组合）
- 源自仓位和订单数据的通用统计指标（针对整个投资组合）

## 相关指南 (Related guides)

- [仓位 (Positions)](positions.md) - 投资组合中的仓位跟踪。
- [报告 (Reports)](reports.md) - 生成投资组合分析报告。
- [可视化 (Visualization)](visualization.md) - 可视化投资组合表现。
