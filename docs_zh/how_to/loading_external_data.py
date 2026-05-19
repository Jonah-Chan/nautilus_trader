# %% [markdown]
# # 加载外部数据 (Loading External Data)
#
# 将 CSV 市场数据加载到 Parquet 数据目录 (Data Catalog) 中，然后使用 `BacktestNode` 运行回测 (Backtest)。当您拥有来自外部供应商的历史数据，且该供应商不被 NautilusTrader 适配器直接支持时，这是一个常见的工作流程。
#
# [在 GitHub 上查看源码](https://github.com/nautechsystems/nautilus_trader/blob/develop/docs_zh/how_to/loading_external_data.py)。

# %%
import os
import shutil
from decimal import Decimal
from pathlib import Path

import pandas as pd

from nautilus_trader.backtest.node import BacktestDataConfig
from nautilus_trader.backtest.node import BacktestEngineConfig
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.backtest.node import BacktestRunConfig
from nautilus_trader.backtest.node import BacktestVenueConfig
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model import BarType
from nautilus_trader.model import QuoteTick
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.persistence.wranglers import QuoteTickDataWrangler
from nautilus_trader.test_kit.providers import CSVTickDataLoader
from nautilus_trader.test_kit.providers import TestInstrumentProvider

# %% [markdown]
# ## 加载和整理数据 (Load and wrangle the data)
#
# 将 CSV Tick 文件（例如来自 [histdata.com](https://www.histdata.com/)）放置在 `~/Downloads/Data/HISTDATA/` 目录中。如果您的数据存放在其他地方，请将 `NAUTILUS_DATA_DIR` 环境变量设置为父目录。
# `CSVTickDataLoader` 将原始 CSV 读取到 DataFrame 中，而 `QuoteTickDataWrangler` 将其转换为 Nautilus 的 `QuoteTick` 对象。

# %%
DATA_DIR = Path(os.environ.get("NAUTILUS_DATA_DIR", "~/Downloads/Data")).expanduser() / "HISTDATA"

# %%
path = DATA_DIR
raw_files = [
    f for f in path.iterdir() if f.is_file() and (f.suffix == ".csv" or f.name.endswith(".csv.gz"))
]
assert raw_files, f"Unable to find any data files in directory {path}"
raw_files

# %%
# 将第一个数据文件加载到 pandas DataFrame 中
df = CSVTickDataLoader.load(raw_files[0], index_col=0, datetime_format="%Y%m%d %H%M%S%f")
df = df.iloc[:, :2]
df.columns = ["bid_price", "ask_price"]

# 使用 wrangler 处理报价 (Quote)
EURUSD = TestInstrumentProvider.default_fx_ccy("EUR/USD")
wrangler = QuoteTickDataWrangler(EURUSD)

ticks = wrangler.process(df)

# %% [markdown]
# ## 写入数据目录 (Write to the data catalog)
#
# 创建一个 `ParquetDataCatalog` 并写入合约 (Instrument) 定义和 Tick 数据。该目录以 Parquet 格式存储数据，以便在回测 (Backtest) 运行中进行高效查询。

# %%
CATALOG_PATH = Path.cwd() / "catalog"

# 如果已存在则清除，然后重新创建
if CATALOG_PATH.exists():
    shutil.rmtree(CATALOG_PATH)
CATALOG_PATH.mkdir()

catalog = ParquetDataCatalog(CATALOG_PATH)

# %%
catalog.write_data([EURUSD])
catalog.write_data(ticks)

# %%
# 验证写入目录的合约 (Instrument)
catalog.instruments()

# %%
start = dt_to_unix_nanos(pd.Timestamp("2020-01-03", tz="UTC"))
end = dt_to_unix_nanos(pd.Timestamp("2020-01-04", tz="UTC"))

ticks = catalog.quote_ticks(instrument_ids=[EURUSD.id.value], start=start, end=end)
ticks[:10]

# %% [markdown]
# ## 配置和运行回测 (Configure and run the backtest)
#
# 设置场所 (Venue)、数据和策略 (Strategy) 配置，然后通过 `BacktestNode` 运行。
# 您在这里构建的策略 (Strategy) 和执行器 (Actor) 可以直接延用到 `TradingNode` 的实盘交易 (Live Trading) 中。

# %%
instrument = catalog.instruments()[0]

venue_configs = [
    BacktestVenueConfig(
        name="SIM",
        oms_type="HEDGING",
        account_type="MARGIN",
        base_currency="USD",
        starting_balances=["1000000 USD"],
    ),
]

data_configs = [
    BacktestDataConfig(
        catalog_path=str(catalog.path),
        data_cls=QuoteTick,
        instrument_id=instrument.id,
        start_time=start,
        end_time=end,
    ),
]

strategies = [
    ImportableStrategyConfig(
        strategy_path="nautilus_trader.examples.strategies.ema_cross:EMACross",
        config_path="nautilus_trader.examples.strategies.ema_cross:EMACrossConfig",
        config={
            "instrument_id": instrument.id,
            "bar_type": BarType.from_str(f"{instrument.id.value}-15-MINUTE-BID-INTERNAL"),
            "fast_ema_period": 10,
            "slow_ema_period": 20,
            "trade_size": Decimal(1_000_000),
        },
    ),
]

config = BacktestRunConfig(
    engine=BacktestEngineConfig(strategies=strategies),
    data=data_configs,
    venues=venue_configs,
)

# %%
node = BacktestNode(configs=[config])

[result] = node.run()

# %%
result
