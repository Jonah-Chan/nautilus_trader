# 希腊字母 (Greeks)

Nautilus 为处理期权希腊字母（期权价格对市场变量变化的敏感度）提供了两条路径：

1. **交易所提供的希腊字母 (Venue-provided Greeks) (Rust/PyO3)**：通过 `OptionGreeks` 数据类型和期权链聚合系统，从 Deribit、Bybit 和 OKX 等交易所实时流式传输。
2. **本地希腊字母计算器 (Local Greeks calculator) (Cython/Python)**：通过 `GreeksCalculator` 类根据缓存的市场数据计算 Black-Scholes 希腊字母，支持投资组合聚合、冲击场景 (Shock scenarios) 和 Beta 加权。

这两条路径既可以独立工作，也可以协同工作。交易所提供的希腊字母通过数据订阅系统到达，不需要本地计算。本地计算器则涵盖了不流式传输希腊字母的交易所、回测以及自定义调整（如冲击、Beta 加权、百分比希腊字母）。

## 交易所提供的希腊字母 (Venue-provided Greeks) (Rust/PyO3)

### OptionGreeks

`OptionGreeks` 类型表示交易所提供的单个期权合约的敏感度。它是一个 Rust 原生类型，通过 PyO3 暴露给 Python。

| 字段 | 类型 | 描述 |
|--------------------|------------------|-----------------------------------------------------|
| `instrument_id` | `InstrumentId` | 这些希腊字母所属的期权合约。 |
| `delta` | `float` | 标的资产每变动一个单位，期权价格的变化率。 |
| `gamma` | `float` | 标的资产每变动一个单位，Delta 的变化率。 |
| `vega` | `float` | 隐含波动率变动 1% 的敏感度。 |
| `theta` | `float` | 每日时间衰减 (dV/dt / 365.25)。 |
| `rho` | `float` | 利率变动的敏感度。 |
| `mark_iv` | `float` 或 None | 标记隐含波动率 (Mark IV)。 |
| `bid_iv` | `float` or None | 买价隐含波动率 (Bid IV)。 |
| `ask_iv` | `float` or None | 卖价隐含波动率 (Ask IV)。 |
| `underlying_price` | `float` or None | 计算时的标的资产价格。 |
| `open_interest` | `float` or None | 合约的持仓量 (Open Interest)。 |
| `ts_event` | `int` | 事件的 UNIX 时间戳（纳秒）。 |
| `ts_init` | `int` | 初始化时的 UNIX 时间戳（纳秒）。 |

从 Actor 或策略中订阅：

```python
self.subscribe_option_greeks(instrument_id, client_id=ClientId("DERIBIT"))
```

处理更新：

```python
def on_option_greeks(self, greeks: OptionGreeks) -> None:
    self.log.info(f"delta={greeks.delta:.4f} gamma={greeks.gamma:.6f}")
```

关于期权链聚合、行权价范围过滤和快照模式等完整的订阅 API，请参阅 [期权 (Options)](options.md) 指南。

### 底层 Rust 类型 (Underlying Rust types)

核心 Rust 实现在 `crates/model/src/data/greeks.rs` 中：

- `OptionGreekValues`：一个普通的结构体，包含 `delta`、`gamma`、`vega`、`theta`、`rho` 字段。实现了 `Add` 和 `Mul<f64>` 以支持聚合。
- `OptionGreeks`（在 `crates/model/src/data/option_chain.rs` 中）：封装了 `OptionGreekValues`，并增加了 `instrument_id`、隐含波动率字段和时间戳。实现了 `Deref<Target = OptionGreekValues>`，因此你可以直接访问希腊字母字段。
- `HasGreeks` trait：提供一个返回 `OptionGreekValues` 的 `greeks()` 方法。`OptionGreekValues` 和 `OptionGreeks` 都实现了该 trait。

### Black-Scholes 函数 (Rust/PyO3)

从 `crates/model/src/data/greeks.rs` 暴露给 Python 的底层定价函数：

```python
from nautilus_trader.core.nautilus_pyo3 import (
    black_scholes_greeks,
    imply_vol,
    imply_vol_and_greeks,
    refine_vol_and_greeks,
)

# 在已知波动率的情况下计算希腊字母
result = black_scholes_greeks(s=100.0, r=0.05, b=0.0, vol=0.20, is_call=True, k=100.0, t=0.25)
# result.delta, result.gamma, result.vega, result.theta, result.price, result.vol

# 从市场价格推导隐含波动率，然后计算希腊字母
result = imply_vol_and_greeks(s=100.0, r=0.05, b=0.0, is_call=True, k=100.0, t=0.25, price=5.0)

# 从一个初始波动率估计值开始精炼波动率（收敛更快）
result = refine_vol_and_greeks(s=100.0, r=0.05, b=0.0, is_call=True, k=100.0, t=0.25,
                                target_price=5.0, initial_vol=0.18)
```

这些函数返回的 `BlackScholesGreeksResult` 包含：`price`、`vol`、`delta`、`gamma`、`vega`、`theta` 和 `itm_prob`（实值概率）。

**约定：**

- Vega 已按 0.01 缩放（即波动率变动 1 个百分点的敏感度）。
- Theta 已按 1/365.25 缩放（即每日衰减）。
- 美式期权在计算希腊字母时被视为欧式期权。

## 本地希腊字母计算器 (Local Greeks calculator) (Cython/Python)

### GreeksCalculator

`nautilus_trader/model/greeks.pyx` 中的 `GreeksCalculator` 类根据缓存的市场数据计算 Black-Scholes 希腊字母。它可以从任何 Actor 或策略中访问。

```python
from nautilus_trader.model.greeks import GreeksCalculator

# 通常在 on_start() 中创建
calculator = GreeksCalculator(cache=self.cache, clock=self.clock)
```

#### 合约希腊字母 (Instrument Greeks)

计算单个合约（期权或标的资产）数量为 1 时的希腊字母：

```python
greeks = calculator.instrument_greeks(
    instrument_id=option_id,
    flat_interest_rate=0.0425,  # 如果缓存中没有收益率曲线，则使用此利率
)
# 返回 GreeksData 或 None
```

该计算器：

1. 在缓存中查找合约及其标的资产。
2. 获取当前价格（优先使用中间价 MID，LAST 作为备选）。
3. 从缓存中查找收益率曲线（如果找不到则回退到 `flat_interest_rate`）。
4. 使用 `imply_vol_and_greeks` 从市场价格推导隐含波动率。
5. 返回包含所有计算值的 `GreeksData` 对象。

对于非期权合约（期货、股票），计算器返回的 `GreeksData` 中 `delta=1`（或 Beta 加权后的 Delta），且没有 Gamma/Vega/Theta。

**冲击场景 (Shock scenarios)**：对现货价格、波动率或时间应用假设的变化：

```python
greeks = calculator.instrument_greeks(
    instrument_id=option_id,
    spot_shock=10.0,            # 标的资产价格 +10 点
    vol_shock=0.02,             # 绝对波动率增加 +2%
    time_to_expiry_shock=1/365, # 时间向前推移一天
)
```

**波动率更新 (Volatility update)**：从缓存的起始点精炼隐含波动率，以加快收敛速度：

```python
greeks = calculator.instrument_greeks(
    instrument_id=option_id,
    update_vol=True,        # 使用缓存的波动率作为起点
    cache_greeks=True,      # 存储结果供下次迭代使用
)
```

**Beta 加权希腊字母 (Beta-weighted Greeks)**：用指数的形式表示 Delta 和 Gamma：

```python
greeks = calculator.instrument_greeks(
    instrument_id=option_id,
    index_instrument_id=InstrumentId.from_str("SPX.CBOE"),
    beta_weights={underlying_id: 1.15},
    percent_greeks=True,
)
```

**时间加权 Vega (Time-weighted vega)**：归一化不同到期日的 Vega：

```python
greeks = calculator.instrument_greeks(
    instrument_id=option_id,
    vega_time_weight_base=30,  # 归一化为 30 天 Vega
)
```

#### 投资组合希腊字母 (Portfolio Greeks)

聚合所有匹配过滤条件的未平仓头寸的希腊字母：

```python
portfolio = calculator.portfolio_greeks(
    underlyings=["AAPL", "MSFT"],
    venue=Venue("CBOE"),
    strategy_id=StrategyId("DELTA_HEDGE-001"),
    flat_interest_rate=0.0425,
    index_instrument_id=InstrumentId.from_str("SPX.CBOE"),
    beta_weights=beta_dict,
    percent_greeks=True,
)
# 返回 PortfolioGreeks：pnl, price, delta, gamma, vega, theta
```

过滤器：

- `underlyings`：代码前缀列表（例如，`["AAPL"]` 匹配 AAPL 股票和所有 AAPL 期权）。
- `venue`：限制在单个交易所。
- `instrument_id`：限制在单个合约。
- `strategy_id`：限制在单个策略。
- `side`：按头寸方向过滤（LONG, SHORT）。
- `greeks_filter`：接受每个头寸的 `PortfolioGreeks` 的可调用对象；返回 `True` 表示包含。

### GreeksData

`GreeksData` 是一个 Python 自定义数据类 (`@customdataclass`)，它承载了单个合约希腊字母计算的完整上下文。它扩展了 `Data`，并支持 Arrow 序列化、缓存存储和目录持久化。

| 字段 | 类型 | 描述 |
|---------------------|-----------------|--------------------------------------------------------|
| `instrument_id` | `InstrumentId` | 合约 ID。 |
| `is_call` | `bool` | 看涨为 True，看跌为 False。 |
| `strike` | `float` | 行权价。 |
| `expiry` | `int` | 到期日期，表示为 YYYYMMDD 整数。 |
| `expiry_in_days` | `int` | 距离到期的天数。 |
| `expiry_in_years` | `float` | 距离到期的年数 (days / 365.25)。 |
| `multiplier` | `float` | 合约乘数。 |
| `quantity` | `float` | 头寸数量（在 `instrument_greeks` 中始终为 1）。 |
| `underlying_price` | `float` | 计算中使用的标的资产价格。 |
| `interest_rate` | `float` | 使用的利率。 |
| `cost_of_carry` | `float` | 持有成本 (r - 股息收益率；期货为 0)。 |
| `vol` | `float` | 隐含波动率。 |
| `pnl` | `float` | 相对于开仓时的盈亏（如果提供了头寸信息）。 |
| `price` | `float` | 模型价格。 |
| `delta` | `float` | Delta。 |
| `gamma` | `float` | Gamma。 |
| `vega` | `float` | Vega (dV / 1% 波动率变化)。 |
| `theta` | `float` | Theta (每日衰减)。 |
| `itm_prob` | `float` | 实值概率 (In-the-money probability)。 |

`GreeksData` 可以通过其 `to_portfolio_greeks()` 方法缩放到投资组合级别，该方法将所有值乘以合约乘数 `multiplier`。`*` 运算符则应用头寸数量：

```python
position_greeks = signed_qty * instrument_greeks  # 返回 PortfolioGreeks
```

### PortfolioGreeks

`PortfolioGreeks` 是 `portfolio_greeks()` 的聚合结果。它支持加法 (`+`) 以合并头寸，以及标量乘法 (`*`) 以进行缩放：

| 字段 | 类型 | 描述 |
|---------|---------|------------------------|
| `pnl` | `float` | 累积盈亏。 |
| `price` | `float` | 累积模型价值。 |
| `delta` | `float` | 投资组合 Delta。 |
| `gamma` | `float` | 投资组合 Gamma。 |
| `vega` | `float` | 投资组合 Vega。 |
| `theta` | `float` | 投资组合 Theta。 |

### 收益率曲线数据 (YieldCurveData)

`YieldCurveData` 存储利率或股息收益率曲线。`GreeksCalculator` 通过货币代码（用于利率）或标的合约 ID（用于股息收益率）从缓存中查找曲线。

```python
from nautilus_trader.model.greeks_data import YieldCurveData
import numpy as np

curve = YieldCurveData(
    ts_event=0,
    ts_init=0,
    curve_name="USD",
    tenors=np.array([0.25, 0.5, 1.0, 2.0]),
    interest_rates=np.array([0.04, 0.042, 0.045, 0.048]),
)

# 可调用对象：为给定的期限 (Tenor) 插值计算利率
rate = curve(0.75)  # 二次插值
```

## 两条路径的选择 (Choosing between the two paths)

| 准则 | 交易所提供 (`OptionGreeks`) | 本地计算器 (`GreeksCalculator`) |
|------------------------------|----------------------------------------|------------------------------------------|
| 计算 | 由交易所完成 | 本地 Black‑Scholes 计算 |
| 延迟 | 随市场数据到达 | 按需计算 |
| 交易场所支持 | Deribit, Bybit, OKX | 任何具有期权合约的交易所 |
| 冲击场景 | 不支持 | 支持现货、波动率和时间冲击 |
| 投资组合聚合 | 手动（遍历 `OptionChainSlice`） | 通过 `portfolio_greeks()` 内置支持 |
| Beta 加权 | 不支持 | 内置支持 |
| 回测支持 | 通过记录的 `OptionGreeks` 数据 | 在任何时间点根据缓存的价格计算 |
| 可用的希腊字母 | delta, gamma, vega, theta, rho, IV, OI | delta, gamma, vega, theta, itm_prob, vol |
| 数据类型 | `OptionGreeks` (Rust/PyO3) | `GreeksData` (Python `@customdataclass`) |

## 希腊字母定义 (Greek definitions)

作为参考，Nautilus 计算的希腊字母定义如下：

| 希腊字母 | 符号 | 定义 |
|------------|--------|-------------------------------------------------------------------------------|
| Delta | `d` | 期权价格相对于标的价格的一阶导数 (dV/dS)。 |
| Gamma | `g` | 期权价格相对于标的价格的二阶导数 (d2V/dS2)。 |
| Vega | `v` | 对隐含波动率变动 1 个百分点的敏感度 (dV/dVol)。 |
| Theta | `t` | 每日时间衰减：每个日历日期的期权价格变化 (dV/dt / 365.25)。 |
| Rho | `r` | 对无风险利率变动的敏感度 (dV/dr)。 |
| 实值概率 | - | 期权到期时处于实值状态的概率：P(ϕS_T > ϕK)，看涨期权 ϕ = 1，看跌期权 ϕ = -1。 |

## 示例 (Examples)

代码库中提供了完整的示例：

- `examples/live/bybit/bybit_option_greeks.py`：订阅 Bybit 交易所提供的希腊字母。
- `examples/live/deribit/deribit_option_greeks.py`：订阅 Deribit 交易所提供的希腊字母。
- `examples/live/okx/okx_option_greeks.py`：订阅 OKX 交易所提供的希腊字母。

## 相关指南 (Related guides)

- [期权 (Options)](options.md) - 期权合约、期权链订阅和行权价过滤。
- [数据 (Data)](data.md) - 内置数据类型、自定义数据和订阅模型。
- [Actor (Actors)](actors.md) - 订阅和处理器参考。
- [策略 (Strategies)](strategies.md) - 策略实现和处理器方法。
