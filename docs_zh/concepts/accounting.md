# 账户核算 (Accounting)

账户核算 (Accounting) 子系统负责跟踪平台交互的每个账户的余额、保证金 (Margin) 和盈亏 (PnL)。本指南涵盖了数据模型、策略使用的查询 API，以及适配器 (Adapter) 作者为保持各交易平台 (Venue) 一致性必须遵循的惯例。

它同样适用于回测 (Backtesting) 和实盘交易。有关回测特定的配置（初始余额、每个交易平台的保证金模型选择），请参阅[回测 (Backtesting)](backtesting.md)。

## 账户类型 (Account types)

当您为实盘交易或回测 (Backtesting) 将交易平台 (Venue) 连接到引擎时，可以通过 `account_type` 选择三种核算模式之一：

| 账户类型 (Account type) | 典型用例                                 | 引擎锁定的内容                                                     |
| ------------ | ------------------------------------------------ | ------------------------------------------------------------------------- |
| 现货 (Cash)         | 现货交易（例如 BTC/USDT、股票）            | 挂单将开启的每个持仓的名义价值。             |
| 保证金 (Margin)       | 衍生品或任何允许杠杆 (Leverage) 的产品  | 每个订单的初始保证金 (Initial margin) 加上未平仓仓位的维持保证金 (Maintenance margin)。 |
| 博彩 (Betting)      | 体育博彩、做市                       | 交易平台 (Venue) 要求的本金 (Stake)；无杠杆 (Leverage)。                                 |

### 现货账户 (Cash accounts)

现货账户全额结算交易；没有杠杆 (Leverage)，因此没有保证金 (Margin) 的概念。锁定余额 (Locked balance) 反映了为挂单预留的名义价值。

### 保证金账户 (Margin accounts)

保证金账户支持需要抵押品的合约 (Instrument)，如期货或杠杆加密货币永续合约。它们跟踪账户余额 (Account balance)，为未平仓订单和仓位预留保证金 (Margin)，并对每个合约应用可配置的杠杆 (Leverage)。保证金 (Margin) 在两个范围内进行跟踪；请参阅下文的[保证金范围](#margin-scopes)。

**关键术语**：

- **杠杆 (Leverage)**：相对于账户权益放大风险敞口 (Exposure)。较高的杠杆会同时增加潜在回报和风险。
- **初始保证金 (Initial margin)**：提交订单时预留的抵押品。
- **维持保证金 (Maintenance margin)**：保持未平仓仓位所需的最低抵押品。
- **锁定余额 (Locked balance)**：预留作为抵押品的资金，不可用于新订单。

:::note 注意
只减仓 (Reduce-only) 订单不会增加现货账户的 `balance_locked`，也不会增加保证金账户的初始保证金 (Initial margin)，因为它们只能减少风险敞口 (Exposure)。
:::

### 博彩账户 (Betting accounts)

博彩账户专用于某些交易平台 (Venue)，在这些平台上，您投入一定金额的本金 (Stake) 以赢得或输掉固定的赔付（预测市场、体育博彩）。引擎仅锁定交易平台要求的本金；不适用杠杆 (Leverage) 和保证金 (Margin)。

## 余额模型 (Balance model)

`AccountBalance` 以同一货币持有三个值：

- `total`：交易平台 (Venue) 报告的总余额 (Total balance) 数值（钱包余额、净清算价值或保证金余额，取决于交易平台）。
- `locked`：为挂单和未平仓仓位预留的锁定余额 (Locked balance)。
- `free`：可用于新订单的可用余额 (Free balance) (`total - locked`)。

恒等式 `total == locked + free` 必须在货币精度范围内始终成立。

Python 中的 `AccountBalance(total, locked, free)` 构造函数要求预先提供所有三个字段。用 Rust 编写的适配器代码有两个额外的派生构造函数，用于集中强制执行该恒等式；每当交易平台仅报告三个值中的两个时，请优先使用它们而不是 `AccountBalance::new`：

| Rust 辅助函数                             | 何时使用                                                                    |
| --------------------------------------- | ------------------------------------------------------------------------------ |
| `AccountBalance::from_total_and_locked` | 交易平台报告总计和锁定金额；`free` 是派生的并被限制在 `[0, total]` 范围内。 |
| `AccountBalance::from_total_and_free`   | 交易平台报告总计和可用金额；`locked` 是派生的并被限制。                 |
| `AccountBalance::new`                   | 所有三个值均已知且一致（测试、透传）。      |

当 `total >= 0` 时，辅助函数会将派生字段限制在 `[0, total]` 范围内，因此交易平台进位取整导致的瞬时超额不会使账户处于损坏状态。

## 保证金范围 (Margin scopes)

`MarginBalance` 有四个字段：`initial`、`maintenance`、`currency` 以及一个选择两种范围之一的 `Optional[InstrumentId]`。

### 按合约范围 (Per-instrument scope)

`MarginBalance.instrument_id` 设置为具体的合约。这适用于：

- 逐仓保证金 (Isolated margin)（按仓位抵押），如某些 OKX 统一账户或 Bybit 逐仓模式。
- 回测 (Backtesting) 或计算出的保证金，其中 `AccountsManager` 根据每个合约的挂单和未平仓仓位在本地派生保证金 (Margin)。

### 账户范围 (Account-wide scope)

`MarginBalance.instrument_id` 为 `None`。该条目由其 `currency`（抵押货币）键入。这适用于：

- 报告每种抵押品单一汇总值的全仓保证金 (Cross-margin) 交易平台。例如：Binance USDT-M (USDT) 和 COIN-M（每种基本代币一个）、OKX、BitMEX、Hyperliquid (USDC)、Bybit UNIFIED（每种代币一个）、Deribit（每种货币一个）、Kraken Futures。

这两种范围在同一个 `MarginAccount` 的独立内部存储中并存。一个 `AccountState` 事件可能包含其中一个或两个范围的条目，而 `MarginAccount.apply()` 会根据是否设置了 `instrument_id` 将每个条目路由到正确的存储。

:::note 注意
`MarginAccount.apply()` 会**替换**来自传入事件的两个存储。它不会与之前的状态合并。发出部分快照的适配器必须在每次更新时包含每一个活跃的保证金条目，否则这些条目将被丢弃，直到下一次完整快照。余额列表同样也会被替换。
:::

## 策略查询 API (Strategy query API)

请使用与交易平台报告形式匹配的查询。如果交易平台报告按合约划分的保证金 (Margin)，请通过 `InstrumentId` 查询。如果它报告账户范围的保证金，请通过 `Currency` 查询。

| 您想要的值的范围              | 使用                                                      |
| ---------------------------------------- | -------------------------------------------------------- |
| 按合约保证金 (逐仓)         | `margin(id)` / `margin_init(id)` / `margin_maint(id)`    |
| 某种抵押品的账户范围保证金   | `margin_for_currency(ccy)` / `margin_init_for_currency(ccy)` / `margin_maint_for_currency(ccy)` |
| 跨两个范围的合并总计        | `total_margin_init(ccy)` / `total_margin_maint(ccy)`     |

点查询在条目不存在时返回 `None`；总计查询始终返回 `Money`（如果没有匹配项，则该货币为零）。

:::note 注意
下面的名称是 `MarginAccount` 上的 Python / Cython API。使用 `nautilus-model` crate 的 Rust 策略调用 `account_margin(&currency)`、`account_initial_margin(&currency)`、`account_maintenance_margin(&currency)`、`total_initial_margin(currency)` 和 `total_maintenance_margin(currency)`：同样按 `Option<InstrumentId>` 划分，但方法名称不同。
:::

### 按合约查询 (`MarginAccount`) (Per-instrument queries)

- `margin(instrument_id) -> MarginBalance | None`
- `margin_init(instrument_id) -> Money | None`
- `margin_maint(instrument_id) -> Money | None`
- `margins() -> dict[InstrumentId, MarginBalance]`（所有按合约条目）
- `margins_init() -> dict[InstrumentId, Money]`
- `margins_maint() -> dict[InstrumentId, Money]`

这些方法只能看到按合约存储。在全仓保证金 (Cross-margin) 交易平台上，它们返回空字典或 `None`。请使用下文的账户范围查询。

### 账户范围查询 (`MarginAccount`) (Account-wide queries)

- `margin_for_currency(currency) -> MarginBalance | None`
- `margin_init_for_currency(currency) -> Money | None`
- `margin_maint_for_currency(currency) -> Money | None`
- `account_margins() -> dict[Currency, MarginBalance]`（所有账户范围条目）
- `account_margins_init() -> dict[Currency, Money]`
- `account_margins_maint() -> dict[Currency, Money]`

### 总计 (`MarginAccount`) (Totals)

这些方法对给定货币的按合约条目和账户范围条目进行求和：

- `total_margin_init(currency) -> Money`
- `total_margin_maint(currency) -> Money`

当策略在可能出现两种范围的交易平台上交易时非常有用（例如，逐仓仓位与全仓保证金抵押品并存）。

### 清除账户范围条目 (Clearing account-wide entries)

- `clear_account_margin(currency)` 移除给定抵押货币的账户范围条目，并触发余额重新计算。按合约条目的对应方法是 `clear_margin(instrument_id)`。

这些是系统方法；适配器代码通过 `MarginAccount.apply()` 隐式调用它们。策略通常不需要直接调用它们。

### 投资组合级查询 (Portfolio-level queries)

保证金查询：

- `portfolio.margins_init(venue=..., account_id=...) -> dict[InstrumentId, Money]`
- `portfolio.margins_maint(venue=..., account_id=...) -> dict[InstrumentId, Money]`

这些方法镜像了 `MarginAccount.margins_init` / `margins_maint`，并且仅返回按合约条目。对于全仓保证金交易平台上的账户范围数据，请直接通过 `portfolio.account(venue).margin_init_for_currency(ccy)` 查询账户。

盈亏 (PnL)、风险敞口 (Exposure)、市值计价 (Mark-to-market) 和权益 (Equity) 查询均接受 `venue` 和可选的 `account_id` 来确定多账户交易平台的范围：

- `portfolio.unrealized_pnls(venue=..., account_id=...) -> dict[Currency, Money]`
- `portfolio.realized_pnls(venue=..., account_id=...) -> dict[Currency, Money]`
- `portfolio.total_pnls(venue=..., account_id=...) -> dict[Currency, Money]`
- `portfolio.net_exposures(venue=..., account_id=...) -> dict[Currency, Money]`
- `portfolio.mark_values(venue=..., account_id=...) -> dict[Currency, Money]`
- `portfolio.equity(venue=..., account_id=...) -> dict[Currency, Money]`
- `portfolio.missing_price_instruments(venue) -> list[InstrumentId]`

有关权益公式、价格回退链、基本货币转换行为以及仅警告一次的缺失价格跟踪器，请参阅[投资组合 (Portfolio) 指南](portfolio.md#equity-and-mark-to-market)。

### 实战示例 (Worked examples)

Hyperliquid（单抵押 USDC 全仓保证金）：

```python
usdc_margin = margin_account.margin_init_for_currency(USDC)
usdc_total  = margin_account.total_margin_init(USDC)
```

Bybit UNIFIED（每种代币独立的全仓保证金）：

```python
for ccy, margin_balance in margin_account.account_margins().items():
    print(ccy, margin_balance.initial, margin_balance.maintenance)
```

dYdX v4（USDC 全仓保证金，按报价货币聚合）：

```python
usdc_margin = margin_account.margin_init_for_currency(USDC)
```

## 保证金模型 (Margin models)

NautilusTrader 为计算路径提供灵活的保证金计算模型（用于回测，以及启用 `calculate_account_state=True` 以进行对账 (Reconciliation) 的实盘策略）。来自交易平台报告的保证金直接进入 `_account_margins` 或 `_margins`，而不经过模型。

### 概述 (Overview)

不同交易平台对待杠杆 (Leverage) 的方式不同：

- **传统经纪商** (Interactive Brokers, TD Ameritrade)：无论杠杆如何，保证金百分比固定。
- **加密货币交易所** (Binance 等)：杠杆可能会降低保证金要求。

两个内置模型都使用合约 (Instrument) 的 `margin_init` 和 `margin_maint` 字段，将保证金计算为名义价值的百分比。它们的区别仅在于杠杆是否减少预留金额。对于具有真正按合约固定保证金的交易平台 (CME / ICE)，请设置 `instrument.margin_init` 和 `margin_maint` 以使百分比还原为所需的金额，或实现[自定义模型](#custom-models)。

### 可用模型 (Available models)

#### `StandardMarginModel`

使用固定百分比，不进行杠杆除法，符合传统经纪商的行为。

```python
# 固定百分比 - 忽略杠杆
margin = notional * instrument.margin_init
```

- 初始保证金 (Initial margin)：`notional_value * instrument.margin_init`
- 维持保证金 (Maintenance margin)：`notional_value * instrument.margin_maint`

**用例**：传统经纪商 (Interactive Brokers)、具有固定保证金要求的外汇经纪商。

#### `LeveragedMarginModel`

将保证金要求除以杠杆 (Leverage)。

```python
# 杠杆会降低保证金要求
adjusted_notional = notional / leverage
margin = adjusted_notional * instrument.margin_init
```

- 初始保证金 (Initial margin)：`(notional_value / leverage) * instrument.margin_init`
- 维持保证金 (Maintenance margin)：`(notional_value / leverage) * instrument.margin_maint`

**用例**：通过杠杆降低保证金要求的加密货币交易所，杠杆影响保证金要求的交易平台。

### 默认行为 (Default behavior)

`MarginAccount` 默认使用 `LeveragedMarginModel`。可以通过编程方式覆盖：

```python
from nautilus_trader.backtest.models import LeveragedMarginModel
from nautilus_trader.backtest.models import StandardMarginModel
from nautilus_trader.test_kit.stubs.execution import TestExecStubs

account = TestExecStubs.margin_account()

# 传统经纪商行为
account.set_margin_model(StandardMarginModel())

# 或杠杆模型（默认）
account.set_margin_model(LeveragedMarginModel())
```

### 实战示例：EUR/USD

- **合约 (Instrument)**：EUR/USD
- **数量**：100,000 EUR
- **价格**：1.10000
- **名义价值**：$110,000
- **杠杆 (Leverage)**：50x
- **`instrument.margin_init`**：3%

| 模型     | 计算方式            | 结果 | 百分比 |
| --------- | ---------------------- | ------ | ---------- |
| 标准 (Standard)  | $110,000 × 0.03        | $3,300 | 3.00%      |
| 杠杆 (Leveraged) | ($110,000 ÷ 50) × 0.03 | $66    | 0.06%      |

在 10,000 美元的账户中：标准模型会阻止该交易；杠杆模型允许该交易。

### 自定义模型 (Custom models)

继承 `MarginModel` 并通过 `MarginModelConfig` 接收配置：

```python
from decimal import Decimal

from nautilus_trader.backtest.config import MarginModelConfig
from nautilus_trader.backtest.models import MarginModel
from nautilus_trader.model.objects import Money


class RiskAdjustedMarginModel(MarginModel):
    def __init__(self, config: MarginModelConfig) -> None:
        self.risk_multiplier = Decimal(str(config.config.get("risk_multiplier", 1.0)))
        self.use_leverage = config.config.get("use_leverage", False)

    def calculate_margin_init(self, instrument, quantity, price, leverage, use_quote_for_inverse=False):
        notional = instrument.notional_value(quantity, price, use_quote_for_inverse)

        if self.use_leverage:
            adjusted = notional.as_decimal() / leverage
        else:
            adjusted = notional.as_decimal()

        margin = adjusted * instrument.margin_init * self.risk_multiplier
        return Money(margin, instrument.quote_currency)

    def calculate_margin_maint(self, instrument, side, quantity, price, leverage, use_quote_for_inverse=False):
        return self.calculate_margin_init(instrument, quantity, price, leverage, use_quote_for_inverse)
```

有关通过 `BacktestVenueConfig` 和 `MarginModelConfig` 进行回测范围的保证金模型配置，请参阅[回测 (Backtesting)](backtesting.md#margin-models) 的保证金模型部分。

## 适配器惯例 (Adapter convention)

实盘适配器 (Live adapters) 将交易平台响应转换为 `AccountBalance` 和 `MarginBalance` 实例。适配器作者必须遵循的惯例：

### 构建 `AccountBalance`

优先使用派生辅助函数，以便集中强制执行限制和 `total == locked + free` 恒等式。手动计算三个字段并将其传递给 `AccountBalance::new`仅适用于所有三个值已具有权威性的透传路径（例如测试）。

### 构建 `MarginBalance`

选择与交易平台报告内容相匹配的范围：

| 交易平台报告内容                                   | 范围 (Scope)          | 发出方式                                              |
| ----------------------------------------------- | -------------- | ------------------------------------------------------ |
| 按合约 (逐仓仓位)             | 按合约 (Per-instrument) | `MarginBalance::new(initial, maint, Some(id))`         |
| 每种抵押品单一汇总值 (全仓保证金)  | 账户范围 (Account-wide)   | `MarginBalance::new(initial, maint, None)`             |
| 多个汇总值，每种抵押品一个         | 账户范围 (Account-wide)   | 每种货币一个 `MarginBalance`，`instrument_id=None` |

### 当前实盘适配器惯例 (Current live-adapter convention)

| 适配器 (Adapter)              | 范围 (Scope)                        | 抵押货币 (Collateral currencies)                                    |
| -------------------- | ---------------------------- | -------------------------------------------------------- |
| Binance Futures      | 账户范围                 | USDT‑M: USDT（或在多资产模式下的 BNB/等）；COIN‑M：每个基本币种一个（BTC、ETH 等） |
| Bybit                | 账户范围                 | 每个币种一个（USDT、BTC、USDC 等）；汇总仓位 IM + 订单 IM |
| Deribit              | 账户范围                 | 每种货币一个（BTC、ETH、USDC 等）                     |
| Hyperliquid          | 账户范围                 | USDC                                                     |
| OKX                  | 账户范围                 | USD（统一账户汇总）                          |
| BitMEX               | 账户范围                 | 每个抵押货币（XBT、USDT 等）                   |
| Kraken Futures       | 账户范围                 | USD                                                      |
| dYdX v4              | 账户范围                 | 按仓位计算，按报价货币 (USDC) 聚合 |
| Interactive Brokers  | 账户范围                 | 每个账户货币                                     |

:::note 注意
不使用合成的 `ACCOUNT.{VENUE}` 或 `ACCOUNT-{COIN}.{VENUE}` `InstrumentId` 占位符。账户范围条目的 `instrument_id=None` 并由 `currency` 键入。
:::

## 迁移说明 (Migration notes)

### 1.226.0

`MarginBalance.instrument_id` 变更为 `Optional[InstrumentId]`，并且 `MarginAccount` 将其内部存储拆分为按合约存储和账户范围存储。如果您之前的策略使用 `portfolio.margins_init(account_id=...)` 通过合成 ID 发现全仓保证金余额，请迁移至：

```python
account = portfolio.account(venue)

# 该账户的所有账户范围保证金
account_margins = account.account_margins_init()

# 特定抵押货币
usdc_margin = account.margin_init_for_currency(USDC)

# 某种货币的按合约 + 账户范围总和
total = account.total_margin_init(USDC)
```

按合约查询 API (`margin_init(instrument_id)`, `margins_init()`) 保持不变，现在具有严格的按合约语义。

## 相关指南 (Related guides)

- [回测 (Backtesting)](backtesting.md)：初始余额、`MarginModelConfig` 以及回测特定的账户设置。
- [投资组合 (Portfolio)](portfolio.md)：投资组合级盈亏 (PnL)、风险敞口 (Exposure) 和货币转换。
- [仓位 (Positions)](positions.md)：仓位生命周期、聚合和盈亏 (PnL)。
- [适配器 (Adapters)](adapters.md)：适配器作者的要求和最佳实践。
