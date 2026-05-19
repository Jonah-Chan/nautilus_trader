# 合约 (Instruments)

合约 (Instrument) 代表任何可交易资产或契约的规范。所有合约类型都实现为实现 `Instrument` 特征 (Trait) 的 Rust 结构体。在 Python 中，它们作为 Cython 扩展类型（通过 `nautilus_trader.model.instruments`）暴露，并具有并行的 PyO3 表示，这些表示在边界处转换为 Cython 类型。纯 Rust 系统直接使用 Rust 类型。该平台支持一系列资产类别和合约类别：

- `Equity`：在现金市场交易的上市股票或 ETF。
- `CurrencyPair`：以 BASE/QUOTE 格式在现金市场交易的即时外汇或加密货币对。
- `Commodity`：在现金市场交易的即时大宗商品合约（例如黄金或原油）。
- `IndexInstrument`：根据成分股计算的即时指数；用作参考价格，不可直接交易。
- `FuturesContract`：具有定义好的标的、到期日和乘数的可交割期货合约。
- `FuturesSpread`：交易所定义的、作为单个合约报价的多腿期货策略（例如日历价差或跨商品价差）。
- `CryptoFuture`：具有固定到期日、标的加密货币和结算货币的定期、可交割加密货币期货合约。
- `CryptoPerpetual`：无到期日的加密货币永续期货合约（永续掉期）；可以是反向或混合结算 (quanto-settled)。
- `PerpetualContract`：资产类别无关的永续掉期，适用于任何标的（外汇、股票、大宗商品、指数、加密货币）。
- `OptionContract`：具有行权价和到期日的标的资产交易所交易期权（看跌或看涨）。
- `OptionSpread`：交易所定义的、作为单个合约报价的多腿期权策略（例如垂直价差、日历价差、跨式价差）。
- `CryptoOption`：以加密货币为标的，并使用加密货币报价/结算的期权；支持反向或混合结算风格。
- `BinaryOption`：根据二元结果结算为 0 或 1 的固定赔付期权。
- `Cfd`：跟踪标的资产并以现金结算的场外交易差价合约 (Contract for Difference)。
- `BettingInstrument`：可在博彩场所交易的体育/博彩市场选择（例如队伍或参赛者）。
- `SyntheticInstrument`：其价格通过公式从组件合约派生而来的合成合约。

## 符号体系 (Symbology)

所有合约都应具有唯一的 `InstrumentId`，它由原生代码 (Symbol) 和交易场所 ID (Venue ID) 组成，中间用点号分隔。
例如，在币安合约 (Binance Futures) 交易所，以太坊永续期货合约的合约 ID 为 `ETHUSDT-PERP.BINANCE`。

所有原生代码在交易场所内 *应该* 是唯一的（并非总是如此，例如币安在现货和合约市场之间共享原生代码），并且 `{symbol.venue}` 组合在 Nautilus 系统中 *必须* 是唯一的。

:::warning
必须将正确的合约与市场数据集（如 Tick 或订单簿数据）匹配，以保证逻辑运行的合理性。错误指定的合约可能会截断数据或产生令人惊讶的结果。
:::

## 回测 (Backtesting)

通用的测试合约可以通过 `TestInstrumentProvider` 实例化：

```python
from nautilus_trader.test_kit.providers import TestInstrumentProvider

audusd = TestInstrumentProvider.default_fx_ccy("AUD/USD")
```

```python
from nautilus_trader.adapters.binance.spot.providers import BinanceSpotInstrumentProvider
from nautilus_trader.model import InstrumentId

provider = BinanceSpotInstrumentProvider(client=binance_http_client)
await provider.load_all_async()

btcusdt = InstrumentId.from_str("BTCUSDT.BINANCE")
instrument = provider.find(btcusdt)
```

或者通过直接构造特定的合约类型来定义：

```python
from nautilus_trader.model.instruments import OptionContract

instrument = OptionContract(...)  # 提供所有必要的参数
```

```rust
use nautilus_model::instruments::CurrencyPair;
use nautilus_model::identifiers::{InstrumentId, Symbol};
use nautilus_model::types::{Currency, Price, Quantity};

let instrument = CurrencyPair::new(
    InstrumentId::from("EUR/USD.SIM"),
    Symbol::from("EUR/USD"),
    Currency::from("EUR"),
    Currency::from("USD"),
    5,                          // price_precision
    0,                          // size_precision
    Price::from("0.00001"),     // price_increment
    Quantity::from("1"),        // size_increment
    // ... 剩余参数
);
```

请参阅完整的合约 [API 参考](/docs/python-api-latest/model/instruments.html)。

## 实盘交易 (Live trading)

实盘集成适配器具有 `InstrumentProvider` 实现，可自动缓存交易场所的最新合约定义。通过将匹配的 `InstrumentId` 传递给需要合约的数据和执行方法来引用特定合约。

## 查找合约 (Finding instruments)

由于相同的 Actor/策略类可同时用于回测和实盘交易，你可以通过中央缓存以完全相同的方式获取合约：

```python
from nautilus_trader.model import InstrumentId

instrument_id = InstrumentId.from_str("ETHUSDT-PERP.BINANCE")
instrument = self.cache.instrument(instrument_id)
```

```rust
use nautilus_model::identifiers::InstrumentId;

let instrument_id = InstrumentId::from("ETHUSDT-PERP.BINANCE");
let instrument = cache.instrument(&instrument_id);
```

也可以订阅特定合约的任何变更：

```python
self.subscribe_instrument(instrument_id)
```

或者订阅整个交易场所的所有合约变更：

```python
from nautilus_trader.model import Venue

binance = Venue("BINANCE")
self.subscribe_instruments(binance)
```

当 `DataEngine` 收到合约更新时，对象将传递给 `on_instrument()` 处理器。重写此方法以在收到合约更新时采取行动：

```python
from nautilus_trader.model.instruments import Instrument

def on_instrument(self, instrument: Instrument) -> None:
    # 在合约更新时采取某些行动
    pass
```

## 精度 (Precision)

精度定义了给定合约上价格和数量允许的小数位数。每个合约都指定了 `price_precision` (价格精度) 和 `size_precision` (规模精度)，它们决定了该市场的有效分数分辨率。

NautilusTrader 在设计上严格执行精度。本节解释了这种方法的原理和机制。

### 为什么执行精度

**真实的行情模拟**。真实的交易所仅接受特定精度的价格和规模。加密货币现货市场可能支持到 2 位小数的价格（例如 `50000.01`），而另一个市场可能支持 8 位（例如 `0.00012345`）。在回测中允许任意精度会产生在生产环境中永远不可能存在的成交价位，从而导致误导性的性能指标。

**交易场所兼容性**。大多数交易所都会验证进场订单的价格和规模精度，并拒绝超过合约规范的订单。在平台层面执行精度可以尽早发现这类常见问题。请注意，交易场所还可能执行超出 `RiskEngine` 当前验证范围的 Tick 倍数或步长 (Step-size) 限制，因此仅符合精度要求并不能保证被交易场所接受。

**确定性计算**。具有显式精度的定点算术消除了浮点漂移，并确保计算在不同平台和环境下是可重现的。两个处理相同数据的系统将始终产生完全相同的结果。

**数据完整性**。回测撮合引擎会验证所有进场市场数据（报价、成交、K 线）是否符合合约声明的精度。这可以尽早发现合约定义与数据源之间的不匹配，防止静默破坏成交价格和数量。

### 精度如何工作

每个合约定义了两个精度值：

| 字段 | 约束内容 | 示例 |
|-------------------|--------------------------------------|------------------|
| `price_precision` | 订单价格、触发价格、成交价。 | `2` -> `50000.01` |
| `size_precision`  | 订单数量、成交数量。   | `5` -> `1.00001`  |

这些精度与最小增量成对出现：

| 字段 | 用途 |
|-------------------|------------------------------------------|
| `price_increment` | 最小有效价格变动（Tick 大小）。 |
| `size_increment`  | 最小有效数量变动。          |

增量自身的精度必须与合约声明的精度完全匹配。例如，一个具有 `price_precision=2` 和 `price_increment=Price(0.01, 2)` 的合约是有效的，但这些值之间的不匹配将在合约创建时引发错误。

### 精度在何处执行

精度在平台的多个层面上进行验证：

1. **合约创建**：`price_increment` 和 `size_increment` 的精度必须分别匹配 `price_precision` 和 `size_precision`。
2. **风险引擎 (Risk engine)**：在订单到达交易场所之前，`RiskEngine` 会检查订单的价格和数量精度是否超过了合约的限制。未通过此检查的订单将被拒绝。
3. **撮合引擎 (Matching engine)**：在回测期间，撮合引擎会验证所有进场市场数据是否匹配合约的精度。不匹配会立即引发 `RuntimeError`。

:::warning
`RiskEngine` 不会自动对数值进行舍入。如果你在一个支持 2 位小数的合约上创建了一个具有 5 位小数的 `Price`，订单将被拒绝。请使用 `instrument.make_price()` 和 `instrument.make_qty()` 来显式舍入。
:::

### 使用合约精度

使用合约的工厂方法来创建具有正确精度的值：

```python
instrument = self.cache.instrument(instrument_id)

price = instrument.make_price(0.90500)
quantity = instrument.make_qty(150)
```

这些方法会将输入舍入到合约声明的精度，确保结果通过精度检查。其他验证规则仍然适用（例如最大/最小数量限制），如果舍入后的值为零，`make_qty()` 将引发异常。

:::tip
在创建订单参数时，始终使用 `instrument.make_price()` 和 `instrument.make_qty()`。这可以避免精度不匹配错误，并确保你的数值具有合约所需的正确小数位数。
:::

如果在回测期间遇到精度不匹配错误，请验证：

1. 合约定义是否匹配数据源的精度。
2. 数据在加载过程中是否被无意中舍入或截断。
3. 自定义数据加载器是否保留了原始精度的元数据。

## 限制 (Limits)

某些数值限制对于合约是可选的，可以为 `None`，这些限制取决于交易所，可能包括：

- `max_quantity`（单个订单的最大数量）。
- `min_quantity`（单个订单的最小数量）。
- `max_notional`（单个订单的最大名义价值）。
- `min_notional`（单个订单的最小名义价值）。
- `max_price`（最大有效报价或订单价格）。
- `min_price`（最小有效报价或订单价格）。

:::note
大多数限制由 Nautilus 的 `RiskEngine` 检查，否则超过公布的限制 *可能* 会导致交易所拒绝订单。
:::

## 保证金和费用 (Margins and fees)

保证金计算由 `MarginAccount` 类处理。本节解释保证金的工作原理并介绍你需要了解的关键概念。

### 保证金何时适用？

每个交易所（例如 CME 或币安）都使用特定的账户类型，该类型决定了保证金计算是否适用。在设置交易场所时，你将指定以下账户类型之一：

- `AccountType.MARGIN`：使用保证金计算的账户，下面将进行说明。
- `AccountType.CASH`：不适用保证金计算的简单账户。
- `AccountType.BETTING`：为博彩设计的账户，同样不涉及保证金计算。

### 词汇表

要理解保证金交易，让我们先了解一些关键术语：

**名义价值 (Notional Value)**：以计价货币 (Quote currency) 表示的合同总价值。它代表你头寸的完整市场价值。例如，CME 上的 EUR/USD 期货（代码 6E）：

- 每手合约代表 125,000 EUR（EUR 是基准货币，USD 是计价货币）。
- 如果当前市场价格为 1.1000，则名义价值等于 125,000 EUR × 1.1000（EUR/USD 价格）= 137,500 USD。

**杠杆 (Leverage)** (`leverage`)：决定你相对于账户存款可以控制多少市场风险敞口的比例。例如，使用 10 倍杠杆，你可以用账户中的 1,000 USD 控制价值 10,000 USD 的头寸。

**初始保证金 (Initial Margin)** (`margin_init`)：开仓所需的保证金率。它代表在账户中开设新头寸必须可用的最小资金额。这仅是一项预检；实际上并不会锁定资金。

**维持保证金 (Maintenance Margin)** (`margin_maint`)：保持头寸开放所需的保证金率。这部分金额会锁定在你的账户中以维持头寸。它始终低于初始保证金。你可以在策略中使用以下代码查看总锁定资金（未平仓头寸维持保证金之和）：

```python
self.portfolio.balances_locked(venue)
```

**挂单/吃单费用 (Maker/Taker Fees)**：交易所根据你的订单与市场的交互方式收取的费用：

- 挂单费用 (Maker Fee) (`maker_fee`)：当你通过下达留在订单簿上的订单来“制造”流动性时收取的费用（通常较低）。例如，低于当前价格的限价买单会增加流动性，成交时适用 *挂单* 费用。
- 吃单费用 (Taker Fee) (`taker_fee`)：当你通过下达立即执行的订单来“提取”流动性时收取的费用（通常较高）。例如，市价买单或高于当前价格的限价买单会移除流动性，成交时适用 *吃单* 费用。

**费率正负号约定**：Nautilus 在所有适配器和回测引擎中对费率使用一致的正负号约定：

- **正费率** = 佣金（收取费用，减少账户余额）。
- **负费率** = 返佣（赚取费用，增加账户余额）。

例如，`-0.00025` 的挂单费率意味着你因提供流动性而获得 0.025% 的返佣，而 `0.00075` 的吃单费率意味着你因提取流动性而支付 0.075% 的佣金。

:::note
不同的交易所在其 API 中使用不同的正负号约定。Nautilus 适配器会将这些标准化为上述约定。如果你在为回测手动指定费率，请确保遵循此约定。
:::

:::tip
并非所有的交易所或合约都实行挂单/吃单费用。如果没有，请将 `Instrument`（例如 `FuturesContract`、`Equity`、`CurrencyPair`、`Commodity`、`Cfd`、`BinaryOption`、`BettingInstrument`）的 `maker_fee` 和 `taker_fee` 都设为 0。
:::

### 保证金计算公式

`MarginAccount` 类使用以下公式计算保证金：

```python
# 初始保证金计算
margin_init = (notional_value / leverage * margin_init) + (notional_value / leverage * taker_fee)

# 维持保证金计算
margin_maint = (notional_value / leverage * margin_maint) + (notional_value / leverage * taker_fee)
```

**关键点**：

- 两个公式遵循相同的结构，但使用各自的保证金率（`margin_init` 和 `margin_maint`）。
- 每个公式由两部分组成：
  - **主要保证金计算**：基于名义价值、杠杆和保证金率。
  - **费用调整**：考虑吃单/挂单费用。

### 实现细节

对于有兴趣探索技术实现的用户：

- [nautilus_trader/accounting/accounts/margin.pyx](https://github.com/nautechsystems/nautilus_trader/blob/develop/nautilus_trader/accounting/accounts/margin.pyx)
- 关键方法：`calculate_margin_init(self, ...)` 和 `calculate_margin_maint(self, ...)`

## 佣金 (Commissions)

交易佣金代表交易所或经纪人为执行交易而收取的费用。虽然挂单/吃单费用在加密货币市场很常见，但像 CME 这样的传统交易所通常采用其他费用结构，例如按合约收取的佣金。NautilusTrader 支持多种佣金模型，以适应不同市场的多样化费用结构。

### 内置费率模型

框架提供了两个内置的费率模型实现：

1. `MakerTakerFeeModel`：实现了加密货币交易所常见的挂单/吃单费用结构，费用按成交价值的百分比计算。
2. `FixedFeeModel`：对每笔成交收取固定佣金，无论成交规模如何。

### 创建自定义费率模型

虽然内置费率模型涵盖了常见场景，但你可能会遇到需要特定佣金结构的情况。NautilusTrader 灵活的架构允许你通过继承 `FeeModel` 来实现自定义费率模型。

例如，如果你在按合约收取佣金的交易所（如 CME）交易期货，你可以实现自定义费率模型。在创建自定义费率模型时，我们继承自 `FeeModel` 基类，该基类出于性能原因在 Cython 中实现。这种 Cython 实现反映在参数命名约定中，其中类型信息使用下划线包含在参数名称中（如 `Order_order` 或 `Quantity_fill_qty`）。

虽然这些参数名称对于 Python 开发人员来说可能看起来很不寻常，但它们是 Cython 类型系统的结果，有助于保持与框架核心组件的一致性。以下是你可以如何创建按合约收费的佣金模型：

```python
class PerContractFeeModel(FeeModel):
    def __init__(self, commission: Money):
        super().__init__()
        self.commission = commission

    def get_commission(self, Order_order, Quantity_fill_qty, Price_fill_px, Instrument_instrument):
        total_commission = Money(self.commission * Quantity_fill_qty, self.commission.currency)
        return total_commission
```

这个自定义实现通过将 `固定的按合约费用` 乘以交易的 `合约数量` 来计算总佣金。`get_commission(...)` 方法接收有关订单、成交数量、成交价格和合约的信息，允许基于这些参数进行灵活的佣金计算。

我们的新类 `PerContractFeeModel` 继承了在 Cython 中实现的 `FeeModel` 类，因此请注意方法签名中 Cython 风格的参数名称：

- `Order_order`：订单对象，带有类型前缀 `Order_`。
- `Quantity_fill_qty`：成交数量，带有类型前缀 `Quantity_`。
- `Price_fill_px`：成交价格，带有类型前缀 `Price_`。
- `Instrument_instrument`：合约对象，带有类型前缀 `Instrument_`。

这些参数名称遵循 NautilusTrader 的 Cython 命名约定，其中前缀指示预期的类型。虽然与典型的 Python 命名约定相比，这可能显得冗长，但它确保了类型安全以及与框架 Cython 代码库的一致性。

### 在实践中使用费率模型

要在你的交易系统中使用任何费率模型（无论是内置的还是自定义的），你需要在设置交易场所时指定它。以下是使用自定义按合约费率模型的示例：

```python
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.objects import Money, Currency

engine.add_venue(
    venue=venue,
    oms_type=OmsType.NETTING,
    account_type=AccountType.MARGIN,
    base_currency=USD,
    fee_model=PerContractFeeModel(Money(2.50, USD)),  # 每手合约 2.50 USD
    starting_balances=[Money(1_000_000, USD)],  # 以 1,000,000 USD 余额开始
)
```

:::tip
在实现自定义费率模型时，请确保它们准确地反映了目标交易所的费用结构。即使在佣金计算中的微小差异，也会在回测期间显着影响策略性能指标。
:::

### 附加信息

由交易所提供的原始合约定义（通常来自 JSON 序列化数据）也包含在通用的 Python 字典中。这是为了保留不一定是统一 Nautilus API 一部分的所有信息，用户在运行时可以通过调用 `.info` 属性来获取。

## 合成合约 (Synthetic instruments)

该平台支持创建定制的合成合约，可以生成合成报价和成交。这些合约对于以下方面非常有用：

- 使 `Actor` 和 `Strategy` 组件能够订阅报价或成交提要。
- 触发模拟订单 (Emulated orders)。
- 从合成报价或成交构建 K 线。

合成合约不能直接交易，因为它们是仅在平台内部本地存在的构造。它们作为分析工具，根据其组件合约提供有用的指标。

在未来，我们计划支持合成合约的订单管理，从而能够根据合成合约的行为交易其组件合约。

:::info
合成合约的交易场所始终被指定为 `'SYNTH'`。
:::

合成合约的价格派生自两个或多个组件合约之上的公式。它在订阅和模拟触发方面的行为类似于普通合约，但它仅存在于 NautilusTrader 内部。

请参阅 [合成合约 (Synthetics)](synthetics.md) 指南以了解：

- 公式语言参考。
- 受支持的运算符和函数。
- 创建和更新示例。
- 从合成价格触发模拟订单。
- 验证规则和错误处理。

## 相关指南

- [数据 (Data)](data.md) - 合约的市场数据类型。
- [订单 (Orders)](orders.md) - 引用合约的订单。
