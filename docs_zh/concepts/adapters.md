# 适配器 (Adapters)

适配器 (Adapters) 将数据提供商和交易场所集成到 NautilusTrader 中。它们位于顶层的 `adapters` 子包中。

一个适配器通常由以下组件组成：

```mermaid
flowchart LR
    subgraph Venue ["交易场所"]
        API[REST API]
        WS[WebSocket]
    end

    subgraph Adapter ["适配器"]
        HTTP[HttpClient]
        WSC[WebSocketClient]
        IP[InstrumentProvider]
        DC[DataClient]
        EC[ExecutionClient]
    end

    subgraph Core ["Nautilus 核心"]
        DE[DataEngine]
        EE[ExecutionEngine]
    end

    API <--> HTTP
    WS <--> WSC
    HTTP --> IP
    HTTP --> DC
    HTTP --> EC
    WSC --> DC
    WSC --> EC
    DC <--> DE
    EC <--> EE
```

| 组件 | 用途 |
|----------------------|------------------------------------------------------------|
| `HttpClient` | REST API 通信。 |
| `WebSocketClient` | 实时流连接。 |
| `InstrumentProvider` | 从交易场所加载并解析合约 (Instrument) 定义。 |
| `DataClient` | 处理市场数据订阅和请求。 |
| `ExecutionClient` | 处理订单提交、修改和取消。 |

## 合约提供者 (Instrument providers)

合约提供者 (Instrument providers) 将交易场所的 API 响应解析为 Nautilus 的 `Instrument` 对象。

`InstrumentProvider` 适用于两种场景：

- 为研究或回测独立发现可用合约。
- 在 `sandbox` (沙盒) 或 `live` (实盘) [环境上下文](architecture.md#environment-contexts)中为执行器 (Actor) 和策略 (Strategy) 进行运行时加载。

### 研究与回测 (Research and backtesting)

以下是发现 Binance Futures 测试网当前合约的示例：

```python
import asyncio
import os

from nautilus_trader.adapters.binance.common.enums import BinanceAccountType
from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
from nautilus_trader.adapters.binance import get_cached_binance_http_client
from nautilus_trader.adapters.binance.futures.providers import BinanceFuturesInstrumentProvider
from nautilus_trader.common.component import LiveClock


async def main():
    clock = LiveClock()

    client = get_cached_binance_http_client(
        clock=clock,
        account_type=BinanceAccountType.USDT_FUTURES,
        api_key=os.getenv("BINANCE_FUTURES_TESTNET_API_KEY"),
        api_secret=os.getenv("BINANCE_FUTURES_TESTNET_API_SECRET"),
        environment=BinanceEnvironment.TESTNET,
    )

    provider = BinanceFuturesInstrumentProvider(
        client=client,
        account_type=BinanceAccountType.USDT_FUTURES,
    )

    await provider.load_all_async()

    # 访问已加载的合约
    instruments = provider.list_all()
    print(f"Loaded {len(instruments)} instruments")


if __name__ == "__main__":
    asyncio.run(main())
```

### 实盘交易 (Live trading)

每个集成处理此问题的方式各不相同。`TradingNode` 内的 `InstrumentProvider` 通常提供两种加载行为：

- 启动时加载所有合约：

```python
from nautilus_trader.config import InstrumentProviderConfig

InstrumentProviderConfig(load_all=True)
```

- 仅加载配置中指定的合约：

```python
InstrumentProviderConfig(load_ids=["BTCUSDT-PERP.BINANCE", "ETHUSDT-PERP.BINANCE"])
```

## 数据客户端 (Data clients)

数据客户端 (Data clients) 处理交易场所的市场数据订阅和请求。它们连接到交易场所的 API，并将传入的数据归一化 (Normalized) 为 Nautilus 类型。

### 请求数据 (Requesting data)

执行器 (Actor) 和策略 (Strategy) 可以使用内置方法请求数据。数据通过回调返回：

```python
from nautilus_trader.model import Instrument, InstrumentId
from nautilus_trader.trading.strategy import Strategy


class MyStrategy(Strategy):
    def on_start(self) -> None:
        # 请求合约定义
        self.request_instrument(InstrumentId.from_str("BTCUSDT-PERP.BINANCE"))

        # 请求历史 K 线
        self.request_bars(BarType.from_str("BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL"))

    def on_instrument(self, instrument: Instrument) -> None:
        self.log.info(f"Received instrument: {instrument.id}")

    def on_historical_data(self, data) -> None:
        self.log.info(f"Received historical data: {data}")
```

### 订阅数据 (Subscribing to data)

对于实时数据，请使用订阅方法：

```python
def on_start(self) -> None:
    # 订阅实时成交 Tick 更新
    self.subscribe_trade_ticks(InstrumentId.from_str("BTCUSDT-PERP.BINANCE"))

    # 订阅实时 K 线
    self.subscribe_bars(BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"))

def on_trade_tick(self, tick: TradeTick) -> None:
    self.log.info(f"Trade: {tick}")

def on_bar(self, bar: Bar) -> None:
    self.log.info(f"Bar: {bar}")
```

:::tip
请参阅 [执行器 (Actors)](actors.md) 文档，了解可用请求和订阅方法及其对应回调的完整参考。
:::

## 执行客户端 (Execution clients)

执行客户端 (Execution clients) 处理交易场所的订单管理。它们将 Nautilus 订单命令转换为交易场所特定的 API 调用，并将执行报告处理回 Nautilus 事件。

主要职责：

- 提交、修改和取消订单。
- 处理成交 (Fills) 和执行报告。
- 与交易场所对账订单状态。
- 处理账户和仓位更新。

`ExecutionEngine` (执行引擎) 根据订单的交易场所将命令路由到相应的执行客户端。有关从策略角度进行订单管理的详细信息，请参阅 [执行 (Execution)](execution.md) 指南。

:::tip
有关构建自定义适配器的信息，请参阅 [适配器开发人员指南](../developer_guide/adapters.md)。
:::

## 相关指南 (Related guides)

- [实盘交易 (Live Trading)](live.md) - 配置并使用适配器运行实盘交易。
- [执行 (Execution)](execution.md) - 通过适配器进行订单执行。
- [数据 (Data)](data.md) - 由适配器提供的市场数据。
