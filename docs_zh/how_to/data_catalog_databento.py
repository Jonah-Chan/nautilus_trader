# %% [markdown]
# # 使用 Databento 的数据目录 (Data Catalog with Databento)
#
# 使用来自 Databento 的市场数据设置 Nautilus Parquet 数据目录 (Data Catalog)。该目录为回测 (Backtest) 和研究提供高效的存储和查询。
#
# [在 GitHub 上查看源码](https://github.com/nautechsystems/nautilus_trader/blob/develop/docs_zh/how_to/data_catalog_databento.py)。

# %% [markdown]
# ## 先决条件 (Prerequisites)
#
# - Python 3.12+
# - 安装了最新版本的 [NautilusTrader](https://pypi.org/project/nautilus_trader/) (`pip install nautilus_trader`)
# - [databento](https://pypi.org/project/databento/) Python 客户端库 (`pip install databento`)
# - 已设置 API 密钥为 `DATABENTO_API_KEY` 的 [Databento](https://databento.com) 账户

# %% [markdown]
# ## 请求数据 (Request data)
#
# 初始化 Databento 历史数据客户端。客户端默认从 `DATABENTO_API_KEY` 环境变量中读取您的 API 密钥。

# %%
import databento as db


client = db.Historical()  # 使用 DATABENTO_API_KEY 环境变量

# %% [markdown]
# **来自 `timeseries.get_range` 的每次历史数据流请求都会产生费用（即使是相同的数据），因此**：
# - 在发出请求前检查费用
# - 避免两次请求相同的数据
# - 将响应以 zstd 压缩的 DBN 文件形式写入磁盘

# %% [markdown]
# 在每次请求前，使用元数据 [get_cost 端点](https://databento.com/docs/api-reference-historical/metadata/metadata-get-cost?historical=python&live=python) 引用费用。仅请求磁盘上尚不存在的数据。
#
# 响应以美元 (USD) 为单位，显示为美分的几分之几。

# %% [markdown]
# 以下请求针对少量数据（如 Medium 文章 [Building high-frequency trading signals in Python with Databento and sklearn](https://databento.com/blog/hft-sklearn-python) 中所使用的），以演示工作流程。

# %%
from pathlib import Path

from databento import DBNStore

# %% [markdown]
# 我们将为原始 Databento DBN 格式数据准备一个目录，并在本教程的其余部分使用它。

# %%
DATABENTO_DATA_DIR = Path("databento")
DATABENTO_DATA_DIR.mkdir(exist_ok=True)

# %%
# 请求费用报价 (USD) - 此端点是“免费”的
client.metadata.get_cost(
    dataset="GLBX.MDP3",
    symbols=["ES.n.0"],
    stype_in="continuous",
    schema="mbp-10",
    start="2023-12-06T14:30:00",
    end="2023-12-06T20:30:00",
)

# %% [markdown]
# 使用历史数据 API 请求 Medium 文章中使用的数据。

# %%
path = DATABENTO_DATA_DIR / "es-front-glbx-mbp10.dbn.zst"

if not path.exists():
    # 请求数据
    client.timeseries.get_range(
        dataset="GLBX.MDP3",
        symbols=["ES.n.0"],
        stype_in="continuous",
        schema="mbp-10",
        start="2023-12-06T14:30:00",
        end="2023-12-06T20:30:00",
        path=path,  # <-- 传递 `path` 会将数据写入磁盘
    )

# %% [markdown]
# 从磁盘读取数据并转换为 pandas.DataFrame

# %%
data = DBNStore.from_file(path)

df = data.to_df()
df

# %% [markdown]
# ## 写入数据目录 (Write to data catalog)

# %%
import shutil
from pathlib import Path

from nautilus_trader.adapters.databento.loaders import DatabentoDataLoader
from nautilus_trader.model import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog

# %%
CATALOG_PATH = Path.cwd() / "catalog"

# 如果已存在则清除
if CATALOG_PATH.exists():
    shutil.rmtree(CATALOG_PATH)
CATALOG_PATH.mkdir()

# 创建目录实例
catalog = ParquetDataCatalog(CATALOG_PATH)

# %% [markdown]
# 使用 `DatabentoDataLoader` 解码数据并将其加载到 Nautilus 对象中。

# %%
loader = DatabentoDataLoader()

# %% [markdown]
# 通过设置 `as_legacy_cython=False` 加劳 Rust PyO3 对象。
#
# 传递 `instrument_id` 是可选的，但通过跳过符号映射可以加快加载速度。如果提供，请使用 Nautilus 的 `symbol.venue` 格式（例如 "ES.GLBX"）。

# %%
path = DATABENTO_DATA_DIR / "es-front-glbx-mbp10.dbn.zst"

# 选项 1（推荐）：让加载器从 DBN 元数据中推断合约 (Instrument) ID
depth10 = loader.from_dbn_file(
    path=path,
    as_legacy_cython=False,
)

# 选项 2：显式指定有效的 Nautilus 合约 (Instrument) ID (symbol.venue 格式)
# instrument_id = InstrumentId.from_str("ESZ3.GLBX")  # Globex 上的 2023 年 12 月 E-mini S&P 期货
# depth10 = loader.from_dbn_file(
#     path=path,
#     instrument_id=instrument_id,
#     as_legacy_cython=False,
# )

# %%
# 将数据写入目录（目前写入 MBP-10 大约需要 20 秒，即 ~250,000 条/秒）
catalog.write_data(depth10)

# %%
# 测试从目录读取
depths = catalog.order_book_depth10()
len(depths)

# %% [markdown]
# ## 准备一个月的 AAPL 交易数据 (Preparing a month of AAPL trades)

# %% [markdown]
# 现在我们将扩展此工作流程，使用 Databento `trade` 模式准备纳斯达克 (Nasdaq) 交易所一个月的 AAPL 交易数据，这将被转换为 Nautilus `TradeTick` 对象。

# %%
# 请求费用报价 (USD) - 此端点是“免费”的
client.metadata.get_cost(
    dataset="XNAS.ITCH",
    symbols=["AAPL"],
    schema="trades",
    start="2024-01",
)

# %% [markdown]
# 在请求历史数据时，传递 `path` 参数以将其写入磁盘。

# %%
path = DATABENTO_DATA_DIR / "aapl-xnas-202401.trades.dbn.zst"

if not path.exists():
    # 请求数据
    client.timeseries.get_range(
        dataset="XNAS.ITCH",
        symbols=["AAPL"],
        schema="trades",
        start="2024-01",
        path=path,  # <-- 传递 `path` 参数
    )

# %% [markdown]
# 从磁盘读取数据并转换为 pandas.DataFrame

# %%
data = DBNStore.from_file(path)

df = data.to_df()
df

# %% [markdown]
# 我们将使用 `"AAPL.XNAS"` 作为 `InstrumentId`，其中 XNAS 是纳斯达克 (Nasdaq) 场所的 ISO 10383 MIC (市场识别码)。
#
# 传递 `instrument_id` 可以跳过符号映射从而加快加载速度。在写入目录时，设置 `as_legacy_cython=False` 会更高效。

# %%
instrument_id = InstrumentId.from_str("AAPL.XNAS")

trades = loader.from_dbn_file(
    path=path,
    instrument_id=instrument_id,
    as_legacy_cython=False,
)

# %% [markdown]
# 在这里，我们将数据组织为每月一个文件。每天一个文件也同样有效。

# %%
# 将数据写入目录
catalog.write_data(trades)

# %%
trades = catalog.trade_ticks([instrument_id])

# %%
len(trades)
