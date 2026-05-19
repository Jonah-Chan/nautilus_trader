# NautilusTrader OKX 期权与因子系统事实指南

> 版本：v1.227.0 | 重整日期：2026-05-18
>
> 本文基于当前仓库源码、类型 stub 和示例文件整理。目标是给 OKX 期权策略开发一个可落地的入口，同时明确哪些能力是 NautilusTrader 本地聚合出来的，哪些来自 OKX 原生行情。

---

## 0. 先明确当前事实

1. NautilusTrader 的常规策略和因子开发入口仍然是 Python `Strategy` / `Actor`。
2. Rust/Cython 负责高性能核心、数据模型、执行和部分指标实现；只有在延迟、吞吐或 CPU 密集计算确实成为瓶颈时，才需要写 Rust Actor/Strategy。
3. OKX 期权支持已经覆盖 instrument loading、quote ticks、option greeks 和 Nautilus 通用 `OptionChainSlice`。
4. `subscribe_option_chain` 是用户层的整链订阅 API；但底层不是 OKX 直接推送 Nautilus 格式的整条链快照，而是 DataEngine/OptionChainManager 基于 active option instruments 的 quote/greeks/status 本地聚合出链快照。
5. OKX Greeks 的物理来源是 OKX option summary family 订阅；quote ticks 仍按具体 option instrument 管理。
6. OKX `OPTION` 必须配置 `instrument_families`，例如 `"BTC-USD"`。没有配置时，当前 adapter 会跳过 OPTION instrument loading。

源码锚点：

- `nautilus_trader/common/actor.pyx`：`subscribe_option_chain`、`on_option_chain`、indicator 注册 API。
- `nautilus_trader/data/engine.pyx`：option chain manager 创建、instrument 匹配、quote/greeks/status 订阅。
- `nautilus_trader/adapters/okx/data.py`：OKX option greeks 订阅到 `option_summary` family。
- `nautilus_trader/adapters/okx/config.py`：OKX `instrument_families` 对 OPTIONS 的要求。
- `nautilus_trader/core/nautilus_pyo3.pyi`：`OptionSeriesId`、`StrikeRange`、`OptionChainSlice` 当前 Python API。

---

## 1. 代码应该写在哪里

| 任务 | 推荐入口 | 语言 | 说明 |
| --- | --- | --- | --- |
| 交易策略、订单管理、仓位风控 | `Strategy` 子类 | Python | 默认选择。事件回调、订阅、下单和风控都在这里组织。 |
| 外部数据或因子接入 | `Actor` 子类 | Python | 适合把 DolphinDB/Kafka/自研服务的数据转成 Nautilus `Data` 后发布到 MessageBus。 |
| 单 instrument 时序指标 | `Indicator` | Python/Cython | 适合 EMA、RSI、ATR 这类依附 tick/bar 的流式计算。 |
| 期权截面因子 | `on_option_chain` | Python | 适合 IV skew、term structure、put/call ratio 等链内扫描。 |
| 极低延迟或 CPU 密集路径 | Rust Actor/Strategy 或 Rust/Cython 扩展 | Rust/Cython | 先用 Python 做正确性和回测，再用测量结果决定是否下沉。 |

---

## 2. 内置 Indicator 适合单品种时序因子

`Indicator` 系统适合挂在单个 instrument 的 quote/trade/bar 上自动更新。当前 Python/Cython API 里 quote tick 注册方法是 `register_indicator_for_quote_ticks`，不是 `register_indicator_for_quotes`。

```python
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.trading.strategy import Strategy


class MyStrategy(Strategy):
    def on_start(self) -> None:
        self.ema = ExponentialMovingAverage(20)
        self.register_indicator_for_quote_ticks(self.instrument_id, self.ema)
        self.subscribe_quote_ticks(self.instrument_id)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        if not self.ema.initialized:
            return

        if tick.bid_price.as_double() > self.ema.value:
            ...
```

对于“扫描一组期权合约并比较不同 strike/expiry”的问题，不要把它硬塞进单个 `Indicator`。更自然的入口是 `OptionChainSlice`。

---

## 3. OKX OPTIONS 必须先正确加载 instrument

OKX 期权不是默认加载的 SPOT/SWAP 路径。最小 live data config 要明确：

- `instrument_types=(OKXInstrumentType.OPTION,)`
- `instrument_families=("BTC-USD",)` 或其他 OKX option family
- `InstrumentProviderConfig(load_all=True)` 用于把对应 family 的 option instruments 放进 cache

示例：

```python
from nautilus_trader.adapters.okx import OKX
from nautilus_trader.adapters.okx import OKXDataClientConfig
from nautilus_trader.adapters.okx import OKXLiveDataClientFactory
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.core.nautilus_pyo3 import OKXEnvironment
from nautilus_trader.core.nautilus_pyo3 import OKXInstrumentType
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import TraderId


config_node = TradingNodeConfig(
    trader_id=TraderId("OKX-OPTIONS-001"),
    data_clients={
        OKX: OKXDataClientConfig(
            environment=OKXEnvironment.DEMO,
            instrument_provider=InstrumentProviderConfig(load_all=True),
            instrument_types=(OKXInstrumentType.OPTION,),
            instrument_families=("BTC-USD",),
        ),
    },
)

node = TradingNode(config=config_node)
node.add_data_client_factory(OKX, OKXLiveDataClientFactory)
```

验证 Greeks 时，优先看 `examples/live/okx/okx_option_greeks.py`。当前 `examples/live/okx/okx_data_tester.py` 默认是 SPOT/SWAP 数据测试，不是 option greeks 验证脚本。

---

## 4. OKX 期权链：用户 API 是整链，底层是本地聚合

### 4.1 数据路径

当前链路可以理解为：

```text
OKX option instruments in cache
        |
        v
Strategy/Actor.subscribe_option_chain(series_id, strike_range, ...)
        |
        v
DataEngine resolves active instruments by venue + underlying + settlement + expiry
        |
        v
DataEngine subscribes quote ticks + option greeks + instrument status for active instruments
        |
        v
OKX adapter maps greeks subscriptions to option_summary(inst_family)
        |
        v
OptionChainManager combines quotes and greeks
        |
        v
Strategy/Actor.on_option_chain(chain_slice)
```

这意味着：

- 你可以在策略层只调用 `subscribe_option_chain`，不需要手写数百个合约订阅。
- 但实现上仍需要 active option instruments，并且 quote/greeks/status 是按合约生命周期被 DataEngine 管理的。
- OKX 不是直接给 Nautilus 推送 `OptionChainSlice`；`OptionChainSlice` 是 Nautilus 本地构建的通用数据对象。

### 4.2 不要手写错误的 `OptionSeriesId`

`OptionSeriesId` 匹配 instrument 时会检查：

- venue
- underlying
- settlement currency
- expiration timestamp

因此 OKX BTC-USD option family 不能随手写成 settlement `"USD"`。仓库里的 OKX option 示例配置使用 `"BTC-USD"` family，实际 settlement 应从 instrument cache 读取，或按 instrument 定义精确填写。

如果使用 `OptionSeriesId.from_expiry(...)`，日期字符串要用源码支持的格式，例如 `"YYYY-MM-DD"`、RFC3339、整数纳秒或浮点秒。不要写 `"20241225"` 这种不在当前 parser 文档里的格式。

更稳妥的方式是从 cache 里找到目标 expiry 和 settlement，然后直接构造 `OptionSeriesId`：

```python
from nautilus_trader.adapters.okx import OKX
from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.identifiers import ClientId


class OKXOptionChainConfig(ActorConfig, frozen=True):
    underlying: str = "BTC"
    strikes_above: int = 10
    strikes_below: int = 10
    snapshot_interval_ms: int = 100


class OKXOptionChainActor(Actor):
    def __init__(self, config: OKXOptionChainConfig) -> None:
        super().__init__(config)
        self._underlying = config.underlying
        self._strikes_above = config.strikes_above
        self._strikes_below = config.strikes_below
        self._snapshot_interval_ms = config.snapshot_interval_ms
        self._series_id: nautilus_pyo3.OptionSeriesId | None = None

    def on_start(self) -> None:
        now_ns = self.clock.timestamp_ns()
        options = []

        for inst in self.cache.instruments():
            if str(inst.id.venue) != OKX:
                continue
            if not hasattr(inst, "option_kind"):
                continue
            if str(inst.underlying) != self._underlying:
                continue

            expiry_ns = getattr(inst, "expiration_ns", None)
            if expiry_ns is None or expiry_ns <= now_ns:
                continue

            settlement = str(inst.get_settlement_currency())
            options.append((settlement, expiry_ns))

        if not options:
            self.log.warning(f"No active OKX {self._underlying} options found")
            return

        nearest_expiry = min(expiry_ns for _, expiry_ns in options)
        settlement = next(
            settlement
            for settlement, expiry_ns in options
            if expiry_ns == nearest_expiry
        )

        self._series_id = nautilus_pyo3.OptionSeriesId(
            OKX,
            self._underlying,
            settlement,
            nearest_expiry,
        )

        strike_range = nautilus_pyo3.StrikeRange.atm_relative(
            strikes_above=self._strikes_above,
            strikes_below=self._strikes_below,
        )

        self.subscribe_option_chain(
            series_id=self._series_id,
            strike_range=strike_range,
            snapshot_interval_ms=self._snapshot_interval_ms,
            client_id=ClientId(OKX),
        )

    def on_option_chain(self, chain_slice) -> None:
        atm = chain_slice.atm_strike
        self.log.info(
            f"OPTION_CHAIN {chain_slice.series_id} "
            f"atm={atm} calls={chain_slice.call_count()} "
            f"puts={chain_slice.put_count()} strikes={chain_slice.strike_count()}",
        )

        if atm is not None:
            atm_call = chain_slice.get_call(atm)
            atm_put = chain_slice.get_put(atm)
            if atm_call is not None and atm_call.greeks is not None:
                self.log.info(f"ATM call mark_iv={atm_call.greeks.mark_iv}")
            if atm_put is not None and atm_put.greeks is not None:
                self.log.info(f"ATM put mark_iv={atm_put.greeks.mark_iv}")

        for strike in chain_slice.strikes():
            call = chain_slice.get_call(strike)
            put = chain_slice.get_put(strike)
            if (
                call is None
                or put is None
                or call.greeks is None
                or put.greeks is None
            ):
                continue

            call_iv = call.greeks.mark_iv
            put_iv = put.greeks.mark_iv
            if call_iv is None or put_iv is None:
                continue

            skew = call_iv - put_iv
            self.log.info(f"K={strike} call-put IV skew={skew}")

    def on_stop(self) -> None:
        if self._series_id is not None:
            self.unsubscribe_option_chain(
                series_id=self._series_id,
                client_id=ClientId(OKX),
            )
```

### 4.3 `OptionChainSlice` 当前 API

当前 Python stub 暴露的主要接口是：

- `chain_slice.series_id`
- `chain_slice.atm_strike`
- `chain_slice.call_count()`
- `chain_slice.put_count()`
- `chain_slice.strike_count()`
- `chain_slice.strikes()`
- `chain_slice.get_call(strike)`
- `chain_slice.get_put(strike)`
- `chain_slice.get_call_quote(strike)`
- `chain_slice.get_put_quote(strike)`
- `chain_slice.get_call_greeks(strike)`
- `chain_slice.get_put_greeks(strike)`

不要使用 `chain.atm_strike_data`、`chain.call(strike)`、`chain.put(strike)` 这类当前 stub 中不存在的接口。

---

## 5. DolphinDB 外部因子接入

仓库里的 DolphinDB 示例展示的是 live streaming bridge 模式：

```text
DolphinDB subscribe callback
        |
        v
thread-safe queue.Queue
        |
        v
Actor clock timer drains queue
        |
        v
Actor.publish_data(DolphinFactor)
        |
        v
MessageBus -> Strategy.on_data
```

对应文件：

- `examples/live/okx/dolphin_bridge_actor.py`
- `examples/live/okx/dolphin_factor_types.py`

当前 `DolphinFactor` 示例是 ephemeral real-time custom data；文件注释明确说目前不需要 catalog persistence。如果未来要做到严格回测/实盘一致，需要把外部因子落地、回放和 `@customdataclass`/catalog 支持一并设计，而不能只依赖 live bridge。

---

## 6. 推荐开发顺序

1. 安装或进入源码环境，确认版本：

   ```bash
   uv run python -c "import nautilus_trader; print(nautilus_trader.__version__)"
   ```

2. 跑基础 backtest 示例，熟悉事件循环、cache、MessageBus 和 Strategy 生命周期：

   ```text
   examples/backtest/
   ```

3. 验证 OKX option instruments 和 Greeks：

   ```text
   examples/live/okx/okx_option_greeks.py
   ```

4. 基于 `OptionChainSlice` 写期权截面因子。当前仓库里 Bybit/Deribit 有完整 option chain 示例，可迁移其 `on_option_chain` 写法到 OKX，并使用 OKX 的 data client config：

   ```text
   examples/live/bybit/bybit_option_chain.py
   examples/live/deribit/deribit_option_chain.py
   ```

5. 对外部复杂因子，先用 Actor bridge 进入 MessageBus；只有需要可回测和可重放时，再补 catalog/persistence 设计。

---

## 7. 项目组织建议

```text
my_okx_project/
├── .env                  # OKX_API_KEY, OKX_API_SECRET, OKX_API_PASSPHRASE
├── pyproject.toml        # uv dependency management
├── main.py               # TradingNode config and factories
├── strategies/           # Strategy subclasses
├── actors/               # Data bridge actors, option chain actors
├── factors/              # Pure factor calculation helpers
├── research/             # notebooks or offline analysis
└── tests/                # backtest/smoke/regression tests
```

保持 strategy/actor 与 factor 计算解耦：`on_option_chain` 负责接收链数据和调度，纯计算函数负责给定 `OptionChainSlice` 后产出因子值。

---

## 8. Rust 扩展边界

Rust Actor/Strategy 是可用的，但不要把它当作默认起点。

适合保留在 Python：

- 策略原型
- 回测验证
- 常规日内/波段策略
- 低频或中频期权截面因子
- 外部因子 bridge

考虑 Rust/Cython 的场景：

- Python profiling 明确显示 CPU 计算成为瓶颈
- 需要处理大规模链内矩阵计算或实时定价
- 回调频率极高，Python 层分配和 GIL 竞争已经影响目标延迟
- 需要复用 Nautilus Rust core 的 trait/macro surface

源码和文档入口：

- `docs/how_to/write_rust_actor.md`
- `docs/how_to/write_rust_strategy.md`
- `crates/common/src/actor/`
- `crates/trading/src/strategy/`

---

## 9. 最后检查清单

开发 OKX 期权链策略前，至少确认：

- [ ] OKX data client 使用 `OKXInstrumentType.OPTION`。
- [ ] 已配置目标 `instrument_families`，例如 `"BTC-USD"`。
- [ ] cache 中能看到目标 option instruments。
- [ ] `okx_option_greeks.py` 或等价 actor 能收到 `OptionGreeks`。
- [ ] `OptionSeriesId` 的 underlying、settlement、expiry 与 cache 中 instruments 匹配。
- [ ] `on_option_chain` 使用 `atm_strike`、`strikes()`、`get_call()`、`get_put()` 等当前 API。
- [ ] 明确知道 `OptionChainSlice` 是 Nautilus 本地聚合结果，不是 OKX 原生整链快照。
