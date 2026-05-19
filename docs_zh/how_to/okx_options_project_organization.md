# OKX 期权项目组织建议

> 适用背景：基于 NautilusTrader `1.227.0` 源码，构建一个面向 OKX 期权策略的独立项目。第一阶段只实现 Nautilus 内部 `OptionChainSlice` 因子链路；DolphinDB 外部因子接入放到后续阶段。

本文档用于指导 agent 落地项目结构。它不是 NautilusTrader 上游源码改造方案，也不是完整生产交易系统设计。当前第一阶段目标是：安装并运行 NautilusTrader 后，先验证 OKX 期权 instrument、quote / greeks、`OptionChainSlice`、以及基于链快照的期权截面因子计算。

## 1. 背景与边界

当前项目讨论已经明确了几条事实：

1. NautilusTrader 的常规策略开发入口是 Python `Strategy` / `Actor`。
2. OKX 期权链不是 OKX 原生直接推送 Nautilus 格式的整条链快照。
3. `subscribe_option_chain` 是 Nautilus 用户层的整链订阅 API；底层由 DataEngine / OptionChainManager 基于 active option instruments 的 quote / greeks / status 本地聚合出 `OptionChainSlice`。
4. 内置 `Indicator` 适合单 instrument 时序指标，例如 EMA、RSI、ATR；不适合扫描多个 strike / expiry 的期权截面因子。
5. 对于 IV skew、term structure、call-put skew、wing skew、跨 expiry 比较等期权截面因子，更自然的输入是一个或多个 `OptionChainSlice`。
6. DolphinDB 适合后续承载重型外部因子，例如长窗口、跨源 join、复杂流计算引擎、多个策略共享的统一实时因子。

因此第一阶段只做这一条数据流：

```text
OKX option instruments in cache
        |
        v
Strategy.subscribe_option_chain(...)
        |
        v
DataEngine / OptionChainManager
        |
        v
Strategy.on_option_chain(chain)
        |
        v
factors.option_surface / factors.term_structure
        |
        v
Strategy signal state
```

DolphinDB 外部因子链路先不实现。文档后面只保留后续扩展建议，避免第一阶段 agent 被过早引到 bridge、stream routing、`DataClient` 等复杂度上。

## 2. 第一阶段目标

第一阶段只验证 Nautilus 内部 option chain 因子链路。

完成标准：

- 可以启动独立项目。
- 可以配置 OKX OPTION 相关 live data。
- 可以加载目标 option instruments。
- 可以订阅并收到 `OptionChainSlice`。
- 可以在 `on_option_chain` 中维护最新链快照。
- 可以调用纯函数计算链内或跨链期权因子。
- 因子输入不完整时默认 fail-closed，不产生可交易信号。

不属于第一阶段：

- DolphinDB bridge。
- `DolphinFactor` custom data。
- `external_clients`。
- 多 DolphinDB stream routing。
- 完整 `DolphinDBDataClient`。
- catalog persistence 或长期回测一致性。

## 3. 第一阶段推荐目录结构

```text
my_okx_project/
├── .env.example
├── pyproject.toml
├── README.md
├── main.py
├── config/
│   ├── __init__.py
│   ├── settings.py
│   └── okx.py
├── strategies/
│   ├── __init__.py
│   └── calendar_spread.py
├── data_types/
│   ├── __init__.py
│   └── factor_result.py
├── factors/
│   ├── __init__.py
│   ├── option_surface.py
│   ├── term_structure.py
│   └── filters.py
├── research/
│   └── README.md
└── tests/
    ├── test_option_surface_factors.py
    ├── test_term_structure_factors.py
    └── test_strategy_gates.py
```

这个结构刻意不包含 `actors/dolphindb_bridge.py`、`data_types/dolphin_factor.py`、`streams/dolphin_streams.py`。这些属于后续 DolphinDB 外部因子阶段。

如果只是做最小实操验证，可以先创建：

```text
main.py
config/
strategies/
factors/
tests/
```

不要为了目录完整而写空抽象。

## 4. 模块职责

### 4.1 `main.py`

`main.py` 是 composition root，只负责组装运行时。

职责：

- 读取 `.env` 或环境变量。
- 创建 `TradingNodeConfig`。
- 配置 OKX OPTION instrument loading 所需参数，例如目标 `instrument_families`。
- 注册 live data / execution factories。
- 创建并添加策略，例如 `CalendarSpreadStrategy`。
- 调用 `node.build()` / `node.run()` / `node.dispose()`。

不应该放在这里：

- IV skew 公式。
- term structure 公式。
- 因子 gate 细节。
- 下单条件细节。
- 策略状态机细节。

### 4.2 `config/`

`config/` 放运行配置，不放业务计算。

建议分工：

- `settings.py`：环境变量读取、demo/live 开关、日志级别、通用超时。
- `okx.py`：目标 underlying、option family、expiry / strike 选择规则、instrument loading 配置。

第一阶段不需要 `config/streams.py`。等 DolphinDB 外部因子阶段再增加 stream 配置。

### 4.3 `strategies/`

`strategies/` 放 Nautilus `Strategy` 子类。

策略职责：

- 在 `on_start()` 里订阅一个或多个 option chain。
- 在 `on_option_chain()` 中接收 `OptionChainSlice`。
- 维护最新链快照，例如按 `str(chain.series_id)` 缓存。
- 调用 `factors/` 中的纯函数计算期权截面因子。
- 做输入完整性检查、freshness gate、position gate、risk gate。
- 在所有 gate 通过后，才产生交易意图或下单。

策略不应该：

- 把复杂 IV 曲线公式直接写在 `on_option_chain` 的长回调里。
- 直接连接 DolphinDB。
- 直接处理外部数据 callback。
- 在链快照缺失、quote 缺失、greeks 缺失时允许开仓。

建议策略内部维护的状态：

```python
latest_chains: dict[str, object]  # key = str(chain.series_id)
latest_factor_results: dict[str, FactorResult]
```

如果策略需要比较近月和远月：

```python
near_chain = latest_chains.get(near_series_key)
far_chain = latest_chains.get(far_series_key)
result = compute_atm_term_structure(near_chain, far_chain)
```

### 4.4 `data_types/`

`data_types/` 放项目内部数据契约。

第一阶段只需要：

```text
data_types/factor_result.py
```

`FactorResult` 推荐字段：

- `name`: 因子名。
- `value`: 因子值，可为 `None`。
- `ts_event`: 因子对应事件时间，可取输入 chain 的最新时间。
- `inputs_ready`: 输入是否完整。
- `reason`: 输入不完整或过滤失败的原因。
- `confidence`: 可选质量分。

这层的价值是让策略不用依赖裸 `float`，也让测试可以明确验证“缺数据时应该返回不可用结果”。

### 4.5 `factors/`

`factors/` 放纯计算函数。

原则：

- 输入是普通参数，例如一个或多个 `OptionChainSlice`、配置阈值、当前时间。
- 输出是 `FactorResult` 或明确数据结构。
- 不访问 `self.cache`。
- 不访问 `self.clock`，除非调用方显式传入当前时间。
- 不调用 Nautilus 订阅 API。
- 不下单。
- 不连接外部服务。

#### `factors/option_surface.py`

适合单 expiry option chain 因子：

- ATM IV。
- call-put IV skew。
- OTM put skew。
- wing skew。
- smile slope。
- smile curvature。
- quote / greeks completeness check。

函数形态示例：

```python
def compute_call_put_skew(chain) -> FactorResult:
    ...
```

#### `factors/term_structure.py`

适合跨 expiry 因子：

- near IV - far IV。
- calendar IV slope。
- same strike 不同 expiry 的 IV spread。
- ATM term structure。

函数形态示例：

```python
def compute_atm_term_structure(near_chain, far_chain) -> FactorResult:
    ...
```

#### `factors/filters.py`

适合通用 gate：

- chain 是否为空。
- call / put 是否齐全。
- quote 是否齐全。
- greeks 是否齐全。
- bid / ask spread 是否过宽。
- 因子结果是否可用。
- fail-closed helper。

第一阶段最重要的是：任何关键输入缺失，都返回 `inputs_ready=False`，策略不得开仓。

### 4.6 `research/`

`research/` 放研究和临时验证材料。

可以放：

- notebook。
- 离线期权链检查。
- IV 曲线探索。
- 临时图表。

不要让 live 策略直接依赖 `research/` 中的代码。

### 4.7 `tests/`

第一阶段测试重点是锁住因子公式和 gate 行为。

建议测试：

- `test_option_surface_factors.py`：测试 IV skew、wing skew、缺 greeks、缺 quote。
- `test_term_structure_factors.py`：测试近远月链输入完整、缺失、expiry 错配。
- `test_strategy_gates.py`：测试链快照缺失、因子不可用、quote 不完整时策略 fail-closed。

## 5. 第一阶段推荐数据流

### 5.1 Nautilus 内部 option chain 因子

```text
OKX option instruments in cache
    |
    v
Strategy.subscribe_option_chain(series_id, strike_range, ...)
    |
    v
DataEngine / OptionChainManager
    |
    v
Strategy.on_option_chain(chain)
    |
    v
factors.option_surface / factors.term_structure
    |
    v
Strategy signal state
```

适合：

- 当前链快照。
- 单 expiry skew。
- 多 expiry term structure。
- strike / expiry 截面扫描。
- calendar spread 的近远月 IV 对比。

第一阶段 agent 应围绕这条链路实现和验证，不要引入 DolphinDB bridge。

## 6. 后续阶段：DolphinDB 外部因子

当第一阶段确认 OKX option chain 链路可用、策略能稳定收到 `OptionChainSlice` 并计算内部因子后，再进入 DolphinDB 外部因子阶段。

后续推荐数据流：

```text
DolphinDB stream table
    |
    v
DolphinDBBridgeActor callback
    |
    v
queue.Queue
    |
    v
Actor timer drain
    |
    v
publish_data(DolphinFactor)
    |
    v
Strategy.on_data(factor)
    |
    v
Strategy signal state
```

适合：

- 长窗口历史因子。
- 多源 join。
- DolphinDB reactive state engine / time series engine 产出的因子。
- 需要在 DolphinDB 内统一生产的跨策略因子。

后续新增目录：

```text
actors/
└── dolphindb_bridge.py
data_types/
└── dolphin_factor.py
streams/
└── dolphin_streams.py
tests/
└── test_dolphin_stream_contracts.py
```

如果只是固定或半固定的多个 DolphinDB stream，优先使用 `external_clients + bridge actor + DataType metadata`。只有当策略运行时动态创建和销毁 DolphinDB stream 订阅，并要求统一 lifecycle，才升级完整 `DolphinDBDataClient`。

## 7. 阶段化落地建议

### Phase 1: OptionChainSlice 内部因子可运行

目标：

- 建出最小项目目录。
- 配好 OKX demo / live 切换。
- 能加载目标 option instruments。
- 能订阅一个或多个 option chain。
- 能在 `on_option_chain` 收到 `OptionChainSlice`。
- 能计算至少一个单链因子，例如 call-put IV skew。
- 能计算至少一个跨链因子，例如近远月 ATM IV term structure。
- 所有缺数据场景 fail-closed。

交付：

- `main.py`
- `config/settings.py`
- `config/okx.py`
- `strategies/calendar_spread.py`
- `data_types/factor_result.py`
- `factors/option_surface.py`
- `factors/term_structure.py`
- `factors/filters.py`
- `tests/test_option_surface_factors.py`
- `tests/test_term_structure_factors.py`
- `tests/test_strategy_gates.py`

### Phase 2: 策略决策收敛

目标：

- 把策略回调保持为状态更新和调度。
- 把所有公式放入 `factors/`。
- 明确 entry / exit gate。
- 明确 dry-run 行为。
- 保留足够日志用于 live smoke 验证。

交付：

- 更完整的 `CalendarSpreadStrategy`。
- 因子结果日志。
- fail-closed 单元测试。
- 一次短时间 OKX demo/live smoke run 记录。

### Phase 3: DolphinDB 外部因子接入

目标：

- 增加 `DolphinDBBridgeActor`。
- 增加 `DolphinFactor` 数据契约。
- 增加 stream metadata routing。
- 策略可同时组合内部 option chain 因子和 DolphinDB 外部因子。
- DolphinDB 断流、因子过期、schema 错误时 fail-closed。

交付：

- `actors/dolphindb_bridge.py`
- `data_types/dolphin_factor.py`
- `streams/dolphin_streams.py`
- `tests/test_dolphin_stream_contracts.py`

### Phase 4: 决定是否升级完整 DataClient

只有满足以下条件时，才考虑完整 `DolphinDBDataClient`：

- 策略运行中 `subscribe_data(...)` 必须自动触发 DolphinDB subscribe。
- 策略运行中 unsubscribe 必须自动触发 DolphinDB unsubscribe。
- 多策略共享 stream 需要 reference count。
- stream lifecycle 必须被 DataEngine 命令统一驱动。
- 健康状态、重连、backpressure、metrics 已经复杂到 bridge actor 难以维护。

## 8. Agent 落地指令

第一阶段给 agent 执行时，使用以下边界：

1. 不修改 NautilusTrader 上游核心源码，除非发现明确 adapter bug 且有证据。
2. 先创建独立项目骨架，不把业务代码直接塞进 `examples/live/okx/`。
3. 第一阶段不创建 DolphinDB bridge。
4. 第一阶段不创建 `DolphinFactor`。
5. 第一阶段不配置 `external_clients`。
6. `Strategy.on_option_chain` 只负责接收链快照、更新状态、调用因子函数。
7. `factors/` 中函数必须是纯函数。
8. 所有链快照缺失、quote 缺失、greeks 缺失、因子不可用场景默认 fail-closed。
9. 先验证一条单链因子，再验证一条跨链因子。

## 9. 常见反模式

避免以下做法：

- 第一阶段就引入 DolphinDB bridge。
- 第一阶段就实现完整 `DataClient`。
- 把 IV skew 公式直接写在 `on_option_chain` 的长回调里。
- 在因子函数里访问 Nautilus `self.cache` 或直接下单。
- 因子缺失时默认允许开仓。
- 把 research notebook 中的临时代码直接 import 到 live 策略。
- 为了目录完整创建大量空模块。

## 10. 推荐判断准则

当一个因子只依赖当前 option chain 快照，优先放进 `factors/`，由 `on_option_chain` 调用。

当一个因子依赖两个 expiry 的最新链快照，仍然优先放进 `factors/term_structure.py`，由策略缓存近远月 chain 后调用。

当一个因子依赖长窗口、历史聚合、跨源 join、DolphinDB 流计算引擎，再进入 DolphinDB 外部因子阶段。

当多个策略只是消费不同固定 DolphinDB stream，用 `external_clients + metadata routing`。

当多个策略需要运行时动态创建和销毁 DolphinDB stream 订阅，并要求统一 lifecycle，才升级完整 `DolphinDBDataClient`。

