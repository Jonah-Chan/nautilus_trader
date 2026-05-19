# 合成合约 (Synthetics)

合成合约 (Synthetic instruments) 是本地定义的合约，其价格派生自其他合约。它们可以组合来自一个或多个交易平台的组件，并将结果暴露为一个标准的 Nautilus 合约，使用合成交易所代码 `SYNTH`。

合成合约在以下场景非常有用：

- 允许 `Actor` 和 `Strategy` 组件订阅报价或成交馈送。
- 根据派生价格触发模拟订单 (Emulated orders)。
- 从合成报价或成交中构建柱状图 (Bars)。

合成合约不能直接交易。它们仅存在于平台本地，作为分析工具使用。未来，Nautilus 可能会支持基于合成合约行为来交易其组件合约。

## 公式语言 (Formula language)

每个合成合约都定义了一个派生公式。Nautilus 使用其内置的数值表达式引擎评估该公式，并将最终的数值结果转换为合成价格 (`Price`)。

### 支持的语法 (Supported syntax)

公式可以直接引用组件合约的 `InstrumentId` 值，包括包含 `/` 和 `-` 的 ID。

| 构造 | 示例 | 备注 |
|---------------------|------------------------------------------------|-----------------------------------------------------------------------|
| 组件引用 | `BTCUSDT.BINANCE` | 使用原始 `InstrumentId` 文本。 |
| 组件引用 | `AUD/USD.SIM` | 包含 `/` 的 ID 是有效的。 |
| 组件引用 | `ETH-USDT-SWAP.OKX` | 包含 `-` 的 ID 是有效的。 |
| 数值字面量 | `1`, `0.5`, `1.2e-3` | 使用 `f64` 语义进行评估。 |
| 布尔字面量 | `true`, `false` | 用于条件和逻辑表达式。 |
| 括号 | `(a + b) / 2` | 使用括号覆盖优先级。 |
| 一元运算符 | `-x`, `!flag` | 一元 `-` 用于数值取负。一元 `!` 用于布尔取反。 |
| 二元运算符 | `+ - * / % ^`, `== !=`, `< <= > >=`, `&& ||` | 算术运算是数值型的。逻辑运算符是布尔型的。 |
| 本地赋值 | `spread = a - b; spread / 2` | 语句从左到右运行。公式必须以一个值结尾。 |
| 注释 | `// line`, `/* block */` | 注释会被忽略。 |

:::note
新公式应使用原始 `InstrumentId` 值。为了向后兼容，在组件 ID 中将 `-` 替换为 `_` 的公式仍然被接受。
:::

### 运算符优先级 (Operator precedence)

表达式引擎按照以下顺序（从最高优先级到最低优先级）评估运算符：

| 级别 | 运算符 | 备注 |
|---------|----------------------|--------------------------------------------------------------|
| 最高 | `^` | 幂运算。右结合。 |
| | 一元 `-`, 一元 `!` | `-2 ^ 2` 的评估结果为 `-(2 ^ 2)`。 |
| | `*`, `/`, `%` | 乘法、除法和取模。 |
| | `+`, `-` | 加法和减法。 |
| | `<`, `<=`, `>`, `>=` | 数值比较。 |
| | `==`, `!=` | 相等和不等。两侧必须具有相同的类型。 |
| 最低 | `&&`, `||` | 布尔运算符。 |

赋值不是表达式运算符。使用 `;` 分隔语句，并确保最后一条语句是你希望合成合约生成的值。

### 内置函数 (Built-in functions)

| 函数 | 签名 | 备注 |
|----------|----------------------------------------|------------------------------------------------------|
| `abs` | `abs(x)` | 绝对值。 |
| `ceil` | `ceil(x)` | 向上取整。 |
| `floor` | `floor(x)` | 向下取整。 |
| `round` | `round(x)` | 根据 Rust `f64` 规则四舍五入到最接近的整数。 |
| `min` | `min(x1, x2, ...)` | 接受一个或多个数值参数。 |
| `max` | `max(x1, x2, ...)` | 接受一个或多个数值参数。 |
| `if` | `if(condition, when_true, when_false)` | 条件必须是布尔值。两个分支必须匹配。仅评估选定的分支。 |

### 类型规则 (Type rules)

- 组件输入是数值型的。
- 算术运算符要求操作数为数值型，并返回数值结果。
- `<`, `<=`, `>`, `>=` 要求操作数为数值型，并返回布尔结果。
- `==` 和 `!=` 接受任何匹配的类型（同为数值或同为布尔），并返回布尔结果。
- `&&`, `||` 以及一元 `!` 要求操作数为布尔型。
- `&&` 和 `||` 具有短路特性。仅在需要时评估右侧表达式。
- 本地变量必须在订阅前赋值。
- 本地变量名称必须以字母或 `_` 开头，后续可以是字母、数字或 `_`。
- 公式最终结果必须是数值。以赋值结尾或产生布尔结果的公式对合成合约是无效的。

### 限制 (Limits)

表达式引擎强制执行以下编译时限制。超过这些限制的公式在构造时会产生明确的错误。

| 限制 | 值 | 描述 |
|------------------|-------|----------------------------------------------------------------|
| 栈深度 | 32 | 评估栈上中间值的最大数量。 |
| 本地变量 | 16 | 不同本地变量名称的最大数量。 |

这些限制对于任何实际的定价公式来说都是非常宽松的。一个包含 8 个组件的加权和公式，其峰值栈深度仅为 3，且不使用本地变量。

### 示例 (Examples)

```python
# 简单价差
formula = "BTCUSDT.BINANCE - ETHUSDT.BINANCE"

# 两个外汇货币对的平均值
formula = "(AUD/USD.SIM + NZD/USD.SIM) / 2"

# 复用中间值
formula = "spread = BTCUSDT.BINANCE - ETHUSDT.BINANCE; spread / 2"

# 条件输出
formula = "if(BTCUSDT.BINANCE > ETHUSDT.BINANCE, BTCUSDT.BINANCE, ETHUSDT.BINANCE)"
```

## 创建合成合约 (Creating a synthetic instrument)

在定义新的合成合约之前，请确保所有组件合约已存在于缓存中。

以下示例通过 Actor 或策略创建一个合成合约。该合成合约代表币安 (Binance) 上比特币和以太坊现货价格之间的简单价差。它假设 `BTCUSDT.BINANCE` 和 `ETHUSDT.BINANCE` 已经存在于缓存中。

```python
from nautilus_trader.model.instruments import SyntheticInstrument

btcusdt_binance_id = InstrumentId.from_str("BTCUSDT.BINANCE")
ethusdt_binance_id = InstrumentId.from_str("ETHUSDT.BINANCE")

synthetic = SyntheticInstrument(
    symbol=Symbol("BTC-ETH:BINANCE"),
    price_precision=8,
    components=[
        btcusdt_binance_id,
        ethusdt_binance_id,
    ],
    formula=f"{btcusdt_binance_id} - {ethusdt_binance_id}",
    ts_event=self.clock.timestamp_ns(),
    ts_init=self.clock.timestamp_ns(),
)

self._synthetic_id = synthetic.id
self.add_synthetic(synthetic)
self.subscribe_quote_ticks(self._synthetic_id)
```

:::note
上述示例中的合成合约 `instrument_id` 是 `{symbol}.SYNTH`，生成的 ID 为 `BTC-ETH:BINANCE.SYNTH`。
:::

## 更新公式 (Updating formulas)

你可以随时更新合成合约的公式。

```python
synthetic = self.cache.synthetic(self._synthetic_id)

new_formula = "(BTCUSDT.BINANCE + ETHUSDT.BINANCE) / 2"
synthetic.change_formula(new_formula)

self.update_synthetic(synthetic)
```

## 触发合约 ID (Trigger instrument IDs)

你可以根据合成价格触发模拟订单 (Emulated orders)。在以下示例中，当合成价格达到触发条件时，合成合约会释放一个模拟订单。

```python
order = self.strategy.order_factory.limit(
    instrument_id=ETHUSDT_BINANCE.id,
    order_side=OrderSide.BUY,
    quantity=Quantity.from_str("1.5"),
    price=Price.from_str("30000.00000000"),
    emulation_trigger=TriggerType.DEFAULT,
    trigger_instrument_id=self._synthetic_id,
)

self.strategy.submit_order(order)
```

## 性能 (Performance)

公式在构造时编译一次，并在每次传入组件价格跳动 (Tick) 时进行评估。表达式引擎采用“一次编译/多次评估”的架构，使用零分配的 f64 栈，因此评估对 Tick 处理路径增加的开销微乎其微。

在 Apple M4 Pro，rustc 1.94.1，release 配置 (opt-level 3) 下测得：

### 评估（热路径）(Evaluation - hot path)

| 公式模式 | 时间 |
|-----------------------------------------|-------|
| `(A + B) / 2.0` | 12 ns |
| `A * 0.4 + B * 0.3 + C * 0.2 + D * 0.1` | 18 ns |
| `if(A > B, A - B, B - A)` | 12 ns |
| `spread = A - B; mid = ...; mid + ...` | 19 ns |
| `max(min(A, B * 20), abs(A - B))` | 15 ns |

### 评估扩展性（加权和）(Evaluation scaling - weighted sum)

| 组件数量 | 时间 |
|------------|-------|
| 2 | 14 ns |
| 4 | 18 ns |
| 8 | 28 ns |

### 编译（冷路径）(Compilation - cold path)

| 公式模式 | 时间 |
|--------------------|--------|
| 简单平均值 | 675 ns |
| 4 输入加权和 | 1.4 us |
| 条件判断 | 1.0 us |
| 带有本地变量 | 1.3 us |
| 带连字符的 ID | 755 ns |

## 错误处理 (Error handling)

Nautilus 在每个边界处都会验证合成合约。公式编译会拒绝未知的符号、类型错误和容量溢出。评估过程在进入公式之前会拒绝错误的输入数量和非有限价格（NaN, Infinity）。

有关输入要求和异常，请参阅 [`SyntheticInstrument` API 参考](https://nautilus-trader.com/docs/python-api-latest/model/instruments.html#nautilus_trader.model.instruments.synthetic.SyntheticInstrument)。

## 相关指南 (Related guides)

- [合约 (Instruments)](instruments.md) - 合约定义和特定交易平台的合约类型。
- [数据 (Data)](data.md) - 引用合约的市场数据类型。
- [订单 (Orders)](orders.md) - 订单可以使用合成合约 ID 作为模拟触发器。
