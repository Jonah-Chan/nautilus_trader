# 数值类型 (Value Types)

NautilusTrader 提供了专门用于表示核心交易概念的数值类型：`Price` (价格)、`Quantity` (数量) 和 `Money` (货币/资金)。这些类型在内部使用定点算术 (Fixed-point arithmetic)，以便在不同平台和环境中实现高效且确定性的计算。

## 概览 (Overview)

| 类型 | 用途 | 有符号 (Signed) | 包含货币 |
|------------|------------------------------------------|--------|----------|
| `Quantity` | 交易规模、订单金额、仓位。 | 否 (无符号) | - |
| `Price` | 市场价格、报价、价格档位。 | 是 | - |
| `Money` | 货币金额、盈亏 (PnL)、账户余额。 | 是 | 是 |

## 不变性 (Immutability)

所有数值类型都是**不可变 (Immutable)** 的。一旦数值被构造，它就不能被更改。任何运算都不会修改原始对象。

```python
from nautilus_trader.model.objects import Quantity

qty1 = Quantity(100, precision=0)
qty2 = Quantity(50, precision=0)

# 这将创建一个新的 Quantity；qty1 和 qty2 保持不变
result = qty1 + qty2

print(qty1)    # 100
print(qty2)    # 50
print(result)  # 150
```

这种设计提供了几个好处：

- **线程安全 (Thread safety)**：不可变数值可以安全地在多线程间共享而无需同步。
- **可预测性 (Predictability)**：数值永远不会意外改变，使调试更容易。
- **可哈希性 (Hashability)**：不可变类型可以作为字典的键或存储在集合 (Set) 中。

## 算术运算 (Arithmetic operations)

数值类型支持标准算术运算符（`+`, `-`, `*`, `/`, `%`, `//`）和一元运算符（`-`, `+`, `abs`）。返回类型取决于运算符和操作数的类型。

### 同类型二元运算 (Same-type binary operations)

相同数值类型的加法和减法会返回该类型，保留其领域含义（价格加价格仍然是价格）：

| 运算 | 结果 |
|-----------------------|------------|
| `Quantity + Quantity` | `Quantity` |
| `Quantity - Quantity` | `Quantity` |
| `Price + Price` | `Price` |
| `Price - Price` | `Price` |
| `Money + Money` | `Money` |
| `Money - Money` | `Money` |

```python
from nautilus_trader.model.objects import Price

price1 = Price(100.50, precision=2)
price2 = Price(0.25, precision=2)

result = price1 + price2  # 返回 Price(100.75, precision=2)
print(type(result))       # <class 'Price'>
```

两个相同类型的数值之间的乘法、除法、地板除 (Floor division) 和取模 (Modulo) 运算会返回 `Decimal`：

| 运算 | 结果 |
|-----------------------|-----------|
| `Price * Price` | `Decimal` |
| `Price / Price` | `Decimal` |
| `Price // Price` | `Decimal` |
| `Price % Price` | `Decimal` |

`Quantity` 和 `Money` 也遵循相同的模式。

这些运算之所以不返回原始类型，是因为结果具有不同的量纲含义。价格乘以价格产生的是“价格的平方”，而不是价格。数量除以数量产生的是一个无量纲比率 (Dimensionless ratio)，而不是数量。返回 `Decimal` 使得单位的变化变得显式，并防止将结果误解为原始单位的数值。

### 一元运算 (Unary operations)

如果结果对该类型有效，一元运算符会保留数值类型：

| 运算 | `Price` | `Quantity` | `Money` |
|--------------|-----------|------------|-----------|
| `-x` (负) | `Price` | `Decimal` | `Money` |
| `+x` (正) | `Price` | `Quantity` | `Money` |
| `abs(x)` (绝对值) | `Price` | `Quantity` | `Money` |
| `int(x)` | `int` | `int` | `int` |
| `float(x)` | `float` | `float` | `float` |
| `round(x)` | `Decimal` | `Decimal` | `Decimal` |

`Quantity.__neg__` 返回 `Decimal` 而非 `Quantity`，因为 `Quantity` 是无符号的，不能表示负值。

```python
from nautilus_trader.model.objects import Price, Quantity, Money
from nautilus_trader.model.currencies import USD

price = Price(100.50, precision=2)
print(-price)            # -100.50
print(type(-price))      # <class 'Price'>

money = Money(-50.00, USD)
print(abs(money))        # 50.00 USD
print(type(abs(money)))  # <class 'Money'>

qty = Quantity(10, precision=0)
print(+qty)              # 10
print(type(+qty))        # <class 'Quantity'>
```

### 混合类型运算 (Mixed-type operations)

当与其他数值类型进行运算时，结果类型遵循 Python 的[数字塔 (Numeric tower)](https://docs.python.org/3/library/numbers.html)惯例。一般原则是运算结果会向更通用的类型扩展：`float` 运算返回 `float`，而 `int` 和 `Decimal` 运算返回 `Decimal` 以保留精度。

这适用于所有六个二元运算符（`+`, `-`, `*`, `/`, `//`, `%`），且双向有效（`value op scalar` 和 `scalar op value`）：

| 左操作数 | 右操作数 | 结果类型 |
|--------------|---------------|-------------|
| 数值类型 | `int` | `Decimal` |
| 数值类型 | `float` | `float` |
| 数值类型 | `Decimal` | `Decimal` |
| `int` | 数值类型 | `Decimal` |
| `float` | 数值类型 | `float` |
| `Decimal` | 数值类型 | `Decimal` |

```python
from decimal import Decimal
from nautilus_trader.model.objects import Quantity

qty = Quantity(100, precision=0)

# Quantity + int -> Decimal
result1 = qty + 50
print(type(result1))  # <class 'decimal.Decimal'>

# Quantity + float -> float
result2 = qty + 50.5
print(type(result2))  # <class 'float'>

# Quantity + Decimal -> Decimal
result3 = qty + Decimal("50")
print(type(result3))  # <class 'decimal.Decimal'>
```

## 精度处理 (Precision handling)

每个数值类型都存储一个精度 (Precision) 字段，表示小数位数。精度在构造时设置且不可变。不存在“未指定”的精度。

### 定点表示 (Fixed-point representation)

数值类型在内部存储为缩放 (Scaled) 到全局固定精度（例如，高精度模式下为 10^16）的整数，而不是浮点数。`precision` 字段追踪构造时使用的小数位数，控制显示格式和序列化，但底层的原始值始终使用全局缩放。

```python
from nautilus_trader.model.objects import Price

p1 = Price(1.23, precision=2)   # 显示为 "1.23"
p2 = Price(1.230, precision=3)  # 显示为 "1.230"

p1 == p2  # True: 底层数值相同
str(p1)   # "1.23"
str(p2)   # "1.230"
```

**精度控制显示，而非身份**。具有相同十进制值但不同精度的两个价格是相等的。`precision` 字段决定了字符串格式以及显示多少位小数，但相等性基于底层的数值。

**市场数据序列化使用精度元数据**。当市场数据类型（报价、成交、订单簿增量）以 Parquet 或 Arrow 格式写入时，精度会存储在文件元数据中，以便正确解码。单个文件内的所有市场数据值必须共享相同的精度。

:::note
如果交易场所更改了合约的最小价格变动单位 (Tick size)（从而更改了其精度），那么在更改前后写入的数据文件将具有不同的精度元数据，不应合并到单个文件中。
:::

有关合约级别的精度如何约束有效价格和数量，请参阅“合约”指南中的[精度 (Precision)](instruments.md#precision)部分。

### 算术精度 (Arithmetic precision)

在对具有不同精度的数值进行算术运算时，结果将使用操作数中的最大精度。

```python
from nautilus_trader.model.objects import Price

price1 = Price(100.5, precision=1)    # 1 位小数
price2 = Price(0.125, precision=3)    # 3 位小数

result = price1 + price2
print(result)            # 100.625
print(result.precision)  # 3 (1 和 3 中的最大值)
```

## 类型特定约束 (Type-specific constraints)

### 数量 (Quantity)

`Quantity` 表示非负金额。尝试创建负数数量或从较小数量中减去较大数量会引发错误：

```python
from nautilus_trader.model.objects import Quantity

# 这将引发 ValueError: Quantity cannot be negative
qty = Quantity(-100, precision=0)

# 这也会引发 ValueError
qty1 = Quantity(50, precision=0)
qty2 = Quantity(100, precision=0)
result = qty1 - qty2  # 结果将是 -50，这是无效的
```

### 货币 (Money)

`Money` 值包含货币。`Money` 值之间的加法和减法要求货币匹配：

```python
from nautilus_trader.model.objects import Money
from nautilus_trader.model.currencies import USD, EUR

usd_amount = Money(100.00, USD)
eur_amount = Money(50.00, EUR)

# 这可行 - 货币相同
result = usd_amount + Money(25.00, USD)

# 这将引发 ValueError - 货币不匹配
result = usd_amount + eur_amount
```

## 常见模式 (Common patterns)

### 累加数值 (Accumulating values)

由于数值类型是不可变的，因此通过重新赋值进行累加：

```python
from nautilus_trader.model.objects import Money
from nautilus_trader.model.currencies import USD

total = Money(0.00, USD)
amounts = [Money(100.00, USD), Money(50.00, USD), Money(25.00, USD)]

for amount in amounts:
    total = total + amount  # 重新赋值给新的 Money 实例

print(total)  # 175.00 USD
```

### 转换为其他类型 (Converting to other types)

数值类型提供了转换方法：

```python
from nautilus_trader.model.objects import Price

price = Price(123.456, precision=3)

# 转换为 Decimal (保留精度)
decimal_value = price.as_decimal()

# 转换为 float
float_value = price.as_double()

# 转换为 string
string_value = str(price)  # "123.456"
```

### 从字符串创建 (Creating from strings)

从字符串表示形式解析数值类型：

```python
from nautilus_trader.model.objects import Quantity, Price, Money

qty = Quantity.from_str("100.5")
price = Price.from_str("99.95")
money = Money.from_str("1000.00 USD")
```
