# 可视化 (Visualization)

NautilusTrader 通过建立在 Plotly 之上的可扩展可视化系统，为分析回测 (Backtesting) 结果提供交互式 HTML 绩效图表 (Tear sheets)。您可以使用极少的代码生成报告，并添加自定义图表和主题。

## 概览 (Overview)

可视化系统由三个部分组成：

1. **图表注册表 (Chart Registry)** - 解耦的图表定义，可以通过自定义可视化进行扩展。
2. **主题系统 (Theme System)** - 使用内置和自定义主题实现一致的样式。
3. **配置 (Configuration)** - 对要渲染的内容以及显示方式的声明式规范。

所有可视化输出都是自包含的 HTML 文件，可以在任何现代浏览器中查看、与利益相关者共享或存档以备将来参考。

:::note
可视化系统需要 `plotly>=6.3.1`。使用以下命令安装：

```bash
uv pip install "nautilus_trader[visualization]"
```

或

```bash
uv pip install "plotly>=6.3.1"
```

:::

## 绩效图表 (Tearsheets)

绩效图表 (Tear sheet) 是一份性能报告，它将多个图表和统计数据组合成单个交互式可视化界面。绩效图表在完成回测运行后生成，并提供对策略绩效的即时视觉反馈。

### 快速开始 (Quick start)

使用默认设置生成绩效图表：

```python
from nautilus_trader.analysis import create_tearsheet
from nautilus_trader.backtest.engine import BacktestEngine

# 运行回测后
engine.run()

# 生成绩效图表
create_tearsheet(
    engine=engine,
    output_path="backtest_results.html",
)
```

这将生成一个包含所有默认图表的 HTML 文件，使用浅色主题和自动布局。在浏览器中打开 `backtest_results.html` 即可查看交互式绩效图表。

### 自定义 (Customization)

控制显示哪些图表以及它们的样式：

```python
from nautilus_trader.analysis import TearsheetConfig
from nautilus_trader.analysis import TearsheetDrawdownChart
from nautilus_trader.analysis import TearsheetEquityChart
from nautilus_trader.analysis import TearsheetRunInfoChart
from nautilus_trader.analysis import TearsheetStatsTableChart

config = TearsheetConfig(
    charts=[
        TearsheetRunInfoChart(),
        TearsheetStatsTableChart(),
        TearsheetEquityChart(),
        TearsheetDrawdownChart(),
    ],
    theme="nautilus_dark",
    height=2000,
)

create_tearsheet(
    engine=engine,
    output_path="custom_tearsheet.html",
    config=config,
)
```

### 货币过滤 (Currency filtering)

对于多货币回测，可以将统计数据过滤到特定货币：

```python
from nautilus_trader.model.currencies import USD

create_tearsheet(
    engine=engine,
    output_path="usd_only.html",
    currency=USD,  # 货币对象，仅显示 USD 统计数据
)
```

当 `currency` 为 `None`（默认值）时，所有货币的统计数据都会分别显示在绩效图表中。

## 可用图表 (Available charts)

绩效图表可以包含以下内置图表的任意组合：

| 图表名称           | 类型         | 描述                                                     |
|--------------------|--------------|----------------------------------------------------------|
| `run_info`         | 表格         | 运行元数据和账户余额。                                   |
| `stats_table`      | 表格         | 绩效统计数据（损益 PnL、收益、通用指标）。              |
| `equity`           | 折线图       | 随时间变化的累计收益，带有可选的基准 (Benchmark)。       |
| `drawdown`         | 面积图       | 权益峰值之后的回撤 (Drawdown) 百分比。                  |
| `monthly_returns`  | 热力图       | 按年份组织的月度投资组合收益百分比。                     |
| `distribution`     | 直方图       | 单个收益值的分布。                                       |
| `rolling_sharpe`   | 折线图       | 60 天滚动夏普比率 (Sharpe ratio)。                       |
| `yearly_returns`   | 柱状图       | 年度收益百分比。                                         |
| `bars_with_fills`  | 蜡烛图       | 叠加了订单成交标记的价格 K 线 (OHLC)。                  |

所有图表都在图表注册表中注册，并通过 `TearsheetConfig.charts` 中的图表对象进行配置（每个图表对象映射到一个内置图表名称）。

### 运行信息表 (Run information table)

`run_info` 图表显示关于回测运行的关键元数据：

- 运行 ID、开始时间、完成时间
- 回测周期（开始/结束日期）
- 处理的总迭代次数
- 事件、订单和持仓计数
- 账户初始和结束余额（按货币）

默认情况下，此表格出现在左上角位置。

### 绩效统计表 (Performance statistics table)

`stats_table` 图表显示按章节组织的绩效指标 (Metrics)：

- **损益统计 (PnL Statistics)**（按货币）：总损益、胜率、获利因子等。
- **收益统计 (Returns Statistics)**：夏普比率 (Sharpe ratio)、索提诺比率 (Sortino ratio)、最大回撤等。
- **通用统计 (General Statistics)**：总交易次数、平均持仓时长等。

默认情况下，此表格出现在右上角位置。

### 权益曲线 (Equity curve)

`equity` 图表绘制了回测期间的累计收益。当向 `create_tearsheet()` 提供 `benchmark_returns` 时，会叠加基准以进行比较。

```python
import pandas as pd

# 加载基准收益 (例如来自市场指数)
# 索引应为 datetime 类型，并与策略收益的时间范围对齐
benchmark_returns = pd.read_csv("sp500_returns.csv", index_col=0, parse_dates=True)["return"]

create_tearsheet(
    engine=engine,
    output_path="with_benchmark.html",
    benchmark_returns=benchmark_returns,
    benchmark_name="S&P 500",
)
```

基准序列将按原样绘制；请确保索引与您的策略收益日期对齐，以进行准确比较。

## 主题 (Themes)

主题控制图表的视觉样式，包括颜色、字体和背景。NautilusTrader 提供了四个内置主题：

| 主题名称        | 描述                                           | 使用场景                      |
|-----------------|------------------------------------------------|-------------------------------|
| `plotly_white`  | 带有深灰色标题的洁净浅色主题。                 | 默认，专业报告。               |
| `plotly_dark`   | 带有标准 Plotly 颜色的深色背景。               | 低光照环境。                   |
| `nautilus`      | 带有 NautilusTrader 品牌颜色的浅色主题。       | 官方浅色模式。                 |
| `nautilus_dark` | 带有青色/蓝绿色特征颜色的深色主题。             | 官方深色模式。                 |

### 选择主题 (Selecting a theme)

在 `TearsheetConfig` 中指定主题：

```python
config = TearsheetConfig(theme="nautilus_dark")
create_tearsheet(engine=engine, config=config)
```

### 自定义主题 (Custom themes)

注册自定义主题，以便在所有可视化中实现一致的品牌形象：

```python
from nautilus_trader.analysis import register_theme

register_theme(
    name="corporate",
    template="plotly_white",  # 基础 Plotly 模板
    colors={
        "primary": "#003366",      # 海军蓝
        "positive": "#2e8b57",     # 海蓝色
        "negative": "#c41e3a",     # 枢机红
        "neutral": "#808080",      # 灰色
        "background": "#ffffff",   # 白色
        "grid": "#e5e5e5",         # 浅灰色
        # 可选的表格颜色 (如果省略将提供默认值)
        "table_section": "#e5e5e5",
        "table_row_odd": "#f8f8f8",
        "table_row_even": "#ffffff",
        "table_text": "#000000",
    }
)

# 使用自定义主题
config = TearsheetConfig(theme="corporate")
```

主题系统会自动根据 `background` 和 `grid` 颜色为 `table_*` 颜色提供合理的默认值，确保与引入表格特定颜色之前注册的主题向后兼容。

## 配置 (Configuration)

`TearsheetConfig` 类提供了对绩效图表生成的声明式控制：

```python
from nautilus_trader.analysis import GridLayout
from nautilus_trader.analysis import TearsheetConfig
from nautilus_trader.analysis import TearsheetDrawdownChart
from nautilus_trader.analysis import TearsheetEquityChart
from nautilus_trader.analysis import TearsheetStatsTableChart

config = TearsheetConfig(
    charts=[
        TearsheetEquityChart(),
        TearsheetDrawdownChart(),
        TearsheetStatsTableChart(),
    ],
    theme="nautilus_dark",
    title="2024 年第四季度策略绩效",
    height=1800,
    include_benchmark=True,
    benchmark_name="SPY",
    layout=GridLayout(
        rows=2,
        cols=2,
        heights=[0.60, 0.40],
        vertical_spacing=0.08,
        horizontal_spacing=0.12,
    ),
)
```

### 配置参数 (Configuration parameters)

| 参数                | 类型                          | 默认值                            | 描述                                          |
|---------------------|-------------------------------|-----------------------------------|-----------------------------------------------|
| `charts`            | `list[TearsheetChart]`        | 所有内置图表                      | 要包含的图表对象列表（按顺序）。               |
| `theme`             | `str`                         | `"plotly_white"`                  | 用于样式的名称。                               |
| `layout`            | `GridLayout`                  | `None` (自动计算)                 | 自定义子图网格布局。                           |
| `title`             | `str`                         | 根据策略/时间自动生成             | 绩效图表标题。                                 |
| `include_benchmark` | `bool`                        | `True`                            | 提供时显示基准。                               |
| `benchmark_name`    | `str`                         | `"Benchmark"`                     | 基准的显示名称。                               |
| `height`            | `int`                         | `1500`                            | 总高度（像素）。                               |
| `show_logo`         | `bool`                        | `True`                            | 显示 NautilusTrader 标志（预留供将来使用）。   |

当 `layout` 为 `None` 时，网格尺寸和行高将根据图表数量自动计算。对于 8 个图表（默认），使用 4×2 网格，高度为 `[0.50, 0.22, 0.16, 0.12]`，以便为顶行表格提供更多空间。

## 自定义图表 (Custom charts)

注册表模式允许您添加自定义图表。图表是将轨迹 (Traces) 渲染到 Plotly 图表对象上的函数。

### 注册自定义图表 (Registering a custom chart)

```python
from nautilus_trader.analysis.tearsheet import register_chart
import plotly.graph_objects as go

def my_custom_chart(returns, output_path=None, title="自定义图表", theme="plotly_white"):
    """
    创建一个自定义可视化。

    为了保持一致性，该函数签名与内置图表函数匹配。
    """
    from nautilus_trader.analysis.themes import get_theme

    theme_config = get_theme(theme)

    # 创建您的可视化
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=returns.index,
        y=returns.cumsum(),
        mode="lines",
        name="自定义指标",
        line={"color": theme_config["colors"]["primary"]},
    ))

    fig.update_layout(
        title=title,
        template=theme_config["template"],
        xaxis_title="日期",
        yaxis_title="值",
    )

    if output_path:
        fig.write_html(output_path)

    return fig

# 注册图表以便独立使用 (通过 `get_chart()` / `list_charts()`)
register_chart("my_custom", my_custom_chart)
```

### 绩效图表集成 (Tearsheet integration)

为了实现与正确网格位置的完整绩效图表集成，请使用较低级别的注册。

:::warning
`_register_tearsheet_chart` 函数是内部 API，可能会在不同版本之间发生变化。对于大多数用例，建议使用 `register_chart` 处理独立图表，或向下游贡献新的内置图表。
:::

```python
from nautilus_trader.analysis import TearsheetConfig
from nautilus_trader.analysis import TearsheetCustomChart
from nautilus_trader.analysis import TearsheetEquityChart
from nautilus_trader.analysis import TearsheetStatsTableChart
from nautilus_trader.analysis.tearsheet import _register_tearsheet_chart

def _render_my_metric(fig, row, col, returns, theme_config, **kwargs):
    """
    将自定义指标直接渲染到子图上。

    参数
    ----------
    fig : go.Figure
        要添加轨迹的图表。
    row : int
        子图行位置。
    col : int
        子图列位置。
    returns : pd.Series
        来自分析器的策略收益。
    theme_config : dict
        主题配置字典。
    **kwargs : dict
        其他参数 (stats_pnls, stats_returns, benchmark_returns 等)。
    """
    metric_values = returns.rolling(30).std() * 100  # 指标示例

    fig.add_trace(
        go.Scatter(
            x=returns.index,
            y=metric_values,
            mode="lines",
            name="30 天滚动波动率",
            line={"color": theme_config["colors"]["neutral"]},
        ),
        row=row,
        col=col,
    )

    fig.update_xaxes(title_text="日期", row=row, col=col)
    fig.update_yaxes(title_text="波动率 (%)", row=row, col=col)

# 注册以便在绩效图表中使用
_register_tearsheet_chart(
    name="volatility",
    subplot_type="scatter",
    title="滚动波动率 (30 天)",
    renderer=_render_my_metric,
)

# 现在 "volatility" 可以用于 TearsheetConfig.charts 中：
config = TearsheetConfig(
    charts=[
        TearsheetStatsTableChart(),
        TearsheetEquityChart(),
        TearsheetCustomChart(chart="volatility"),
    ],
)
```

渲染器函数接收所有必要的数据（收益、统计数据、主题配置），并直接渲染到指定的子图位置。

## 离线分析 (Offline analysis)

如果您拥有预计算的统计数据但没有 `BacktestEngine` 实例，请使用较低级别的 API：

```python
from nautilus_trader.analysis.tearsheet import create_tearsheet_from_stats

# 加载预计算的数据 (结构与 PortfolioAnalyzer 输出匹配)
stats_pnls = {"USD": {"PnL (total)": 1500.0, "Win Rate": 0.55, ...}}  # 按货币
stats_returns = {"Sharpe Ratio (252 days)": 1.2, "Max Drawdown": -0.15, ...}
stats_general = {"Avg Winner": 100.0, "Avg Loser": -50.0, ...}
returns = pd.Series(...)  # 带有 datetime 索引的每日收益

create_tearsheet_from_stats(
    stats_pnls=stats_pnls,
    stats_returns=stats_returns,
    stats_general=stats_general,
    returns=returns,
    output_path="offline_analysis.html",
)
```

字典键应与 `PortfolioAnalyzer.get_performance_stats_*()` 返回的键匹配。

这种方法适用于：

- 分析存储在别处的多次回测运行结果。
- 使用预计算的指标比较策略。
- 集成到外部分析流水线中。

## 最佳实践 (Best practices)

### 图表选择

- 使用默认图表进行探索性分析，以查看所有可用指标。
- 当您知道哪些指标对您的策略至关重要时，自定义图表。
- 移除无关图表以减少视觉干扰并缩小文件大小。

### 主题使用

- 使用 `plotly_white` 进行专业报告和演示。
- 使用 `nautilus_dark` 进行官方材料制作或在光线较弱的情况下查看。
- 创建自定义主题以符合内部准则或个人偏好。

### 性能考量

- 绩效图表 HTML 文件包含所有内联数据，对于长期回测，其大小可能达到几兆字节。
- 考虑为不同的分析时间段生成单独的绩效图表。
- 对于超大型数据集，请使用单个图表函数而不是完整的绩效图表。

### 自定义统计集成

自定义图表与 `PortfolioAnalyzer` 中注册的[自定义统计](reports.md)配合使用效果最佳。这可确保您的可视化显示的指标与系统其余部分的计算一致：

```python
from nautilus_trader.analysis.statistic import PortfolioStatistic

class MyCustomStatistic(PortfolioStatistic):
    """用于专门策略分析的自定义指标。"""

    def calculate_from_returns(self, returns):
        # 您的计算逻辑
        return custom_metric_value

# 向分析器注册
analyzer.register_statistic(MyCustomStatistic())

# 现在可以在 stats_returns 中用于自定义图表
```

## API 层级 (API levels)

可视化系统提供两个 API 层级：

### 高级 API (High-level API)

推荐用于大多数用例：

```python
create_tearsheet(engine=engine, config=config)
```

自动从 `BacktestEngine` 提取数据，生成所有配置好的图表，并产出完整的 HTML 绩效图表。

### 低级 API (Low-level API)

用于高级自定义或离线分析：

```python
create_tearsheet_from_stats(
    stats_pnls=stats_pnls,
    stats_returns=stats_returns,
    stats_general=stats_general,
    returns=returns,
    run_info=run_info,
    account_info=account_info,
    config=config,
)
```

提供对数据输入的精细控制，并允许分析预计算的统计数据。

### 独立图表函数 (Standalone chart functions)

单个图表函数可以独立使用，为自定义分析工作流生成单一用途的 HTML 可视化或 Plotly 图表对象。

#### 带成交标记的价格 K 线 (Price bars with fills)

`create_bars_with_fills` 函数生成一个带有订单成交标记的价格蜡烛图，有助于直观分析价格走势中的策略执行情况。它可以独立使用或包含在绩效图表中：

```python
from nautilus_trader.analysis import create_bars_with_fills
from nautilus_trader.analysis import create_tearsheet
from nautilus_trader.analysis import TearsheetBarsWithFillsChart
from nautilus_trader.analysis import TearsheetConfig
from nautilus_trader.analysis import TearsheetEquityChart
from nautilus_trader.analysis import TearsheetStatsTableChart
from nautilus_trader.model.data import BarType

# 独立使用
bar_type = BarType.from_str("ESM4.XCME-1-MINUTE-LAST-EXTERNAL")
fig = create_bars_with_fills(
    engine=engine,
    bar_type=bar_type,
    title="ES 期货 - 入场/出场分析",
)
fig.show()  # 在 Jupyter 中显示
fig.write_html("bars_with_fills.html")  # 或者保存到文件

# 包含在绩效图表中
config = TearsheetConfig(
    charts=[
        TearsheetStatsTableChart(),
        TearsheetEquityChart(),
        TearsheetBarsWithFillsChart(
            bar_type="ESM4.XCME-1-MINUTE-LAST-EXTERNAL",
            title="带成交标记的 K 线图",
        ),
    ],
)
create_tearsheet(engine=engine, config=config)

# 在一个绩效图表中包含多个带成交标记的 K 线图
config = TearsheetConfig(
    charts=[
        TearsheetStatsTableChart(),
        TearsheetEquityChart(),
        TearsheetBarsWithFillsChart(
            bar_type=f"{instrument.id}-5-MINUTE-MID-INTERNAL",
            title=f"带成交标记的 K 线图 - {instrument.id}",
        ),
        TearsheetBarsWithFillsChart(
            bar_type=f"{other_instrument.id}-5-MINUTE-MID-INTERNAL",
            title=f"带成交标记的 K 线图 - {other_instrument.id}",
        ),
    ],
)
create_tearsheet(engine=engine, config=config)
```

该可视化界面显示 OHLC 价格蜡烛图，并用三角形标记表示订单成交（绿色上三角形表示买入，红色下三角形表示卖出）。需要额外配置（如 `bar_type`）的图表直接在图表对象上获取这些参数（例如 `TearsheetBarsWithFillsChart(bar_type=...)`）。

其他单个图表函数包括 `create_equity_curve`、`create_drawdown_chart`、`create_monthly_returns_heatmap` 等。请参阅 API 参考手册以获取完整列表。

## 相关指南

- [回测 (Backtesting)](backtesting.md) - 学习如何运行生成绩效图表的回测。
- [报告 (Reports)](reports.md) - 了解绩效图表中显示的底层统计数据。
- [投资组合 (Portfolio)](portfolio.md) - 探索投资组合跟踪和绩效指标。
