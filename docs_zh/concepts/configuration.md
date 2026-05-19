# 配置 (Configuration)

NautilusTrader 在整个平台中使用类型化配置结构体 (Typed configuration structs)。每个组件（数据客户端、执行客户端、引擎、策略）都有一个专门的配置结构体来控制其行为。

## 设计原则 (Design principles)

### 默认值在配置边界处解析 (Defaults resolve at the config boundary)

对于始终具有合理默认值的字段，配置结构体携带具体的值。超时时间 (Timeouts)、重试次数 (Retry counts)、退避延迟 (Backoff delays) 和心跳间隔 (Heartbeat intervals) 都是普通的类型（如 `u64` 或 `u32`），且内置了默认值。下游代码接收的是已解析的值，不需要重复默认逻辑。

### Option 表示语义缺失，而非“使用默认值” (Option means semantic absence, not "use default")

`Option<T>` 字段仅在 `None` 具有实际含义时出现：功能关闭、回溯窗口 (Lookback window) 无限制或值在运行时从环境继承。如果一个字段始终解析为具体值，则不会用 `Option` 包装。

这种区别使得配置语义在类型中可见。普通的 `u64` 字段始终有值。`Option<u64>` 字段可能缺失，消费它的代码将据此进行分支处理。

### 默认值的单一事实来源 (Single source of truth for defaults)

每个配置结构体都使用 `bon::Builder` 通过 `#[builder(default = value)]` 注解在同一处定义默认值。`Default` 实现委托给生成器 (`Self::builder().build()`)，因此不存在可能导致不同步的第二份默认值副本。

### 配置解码在遇到未知字段时失败 (Config decoding fails on unknown fields)

配置解码在遇到未知字段时会快速失败。Nautilus 将额外的键视为 Bug，而非无害的输入。这可以在节点或客户端以错误设置启动之前，捕获拼写错误、重命名后的旧名称以及复制粘贴错误。

## Python 配置 (Python configs)

Python 配置类 (msgspec structs) 接受 `None` 作为可选参数。对于普通的 `T` 字段，`None` 表示“使用默认值”。对于 `Option<T>` 字段，`None` 保留了该字段的可选含义（禁用、无限制等）。

所有的 Python 配置类都继承自 `NautilusConfig`，它在底层的 `msgspec.Struct` 上设置了 `forbid_unknown_fields=True`。现在，未知的键在解码过程中会引发 `msgspec.ValidationError`。

```python
from nautilus_trader.adapters.bybit.config import BybitDataClientConfig

# 全部使用默认值：60s 超时，3 次重试等。
config = BybitDataClientConfig()

# 仅覆盖超时时间
config = BybitDataClientConfig(http_timeout_secs=30)

# 禁用合约状态轮询
config = BybitDataClientConfig(instrument_status_poll_secs=None)
```

## Rust 配置 (Rust configs)

所有的配置结构体都派生自 [`bon::Builder`](https://bon-rs.com)，它生成一个带有必填字段编译时检查的类型安全生成器 (Builder)。带有 `#[builder(default = value)]` 的字段可以在生成器调用中省略，并将使用其声明的默认值。以下是构造配置的三种等效方法：

使用 Serde 进行反序列化的 Rust 配置结构体还设置了 `#[serde(deny_unknown_fields)]`。现在，未知的键会导致反序列化失败，而不是被忽略。

```rust
// 生成器 (Builder)：仅设置与默认值不同的项
let config = BybitDataClientConfig::builder()
    .http_timeout_secs(30)
    .build();

// 带有默认展开 (Default spread) 的结构体字面量
let config = BybitDataClientConfig {
    http_timeout_secs: 30,
    ..Default::default()
};

// 全默认值
let config = BybitDataClientConfig::default();
```

对于未指定的字段，这三种方法产生的结果完全相同。

## 通用配置字段 (Common config fields)

大多数适配器配置共享一组通用字段：

| 字段 | 类型 | 默认值 | 用途 |
|------------------------------------|--------|---------|-------------------------------|
| `http_timeout_secs` | `u64` | 60 | REST 请求超时。 |
| `max_retries` | `u32` | 3 | 最大重试次数。 |
| `retry_delay_initial_ms` | `u64` | 1,000 | 初始退避延迟。 |
| `retry_delay_max_ms` | `u64` | 10,000 | 最大退避延迟。 |
| `heartbeat_interval_secs` | `u64` | 不定 | WebSocket 保活间隔。 |
| `recv_window_ms` | `u64` | 不定 | 签名请求过期窗口。 |
| `update_instruments_interval_mins` | 不定 | 不定 | 定期的合约刷新。 |

适配器特定的字段（速率限制、轮询间隔、保证金模式）记录在每个适配器的集成指南中。

## 引擎配置 (Engine configs)

引擎配置（`LiveExecEngineConfig`, `DataEngineConfig` 等）遵循相同的模式。`reconciliation` (对账)、`inflight_check_interval_ms` (在途检查间隔) 和 `open_check_threshold_ms` (开仓检查阈值) 等字段是带有生成器默认值的普通类型。真正可选的功能使用 `Option<T>`：

```python
from nautilus_trader.config import LiveExecEngineConfig

config = LiveExecEngineConfig(
    reconciliation=True,
    open_check_interval_secs=30.0,       # 启用未成交订单轮询
    open_check_lookback_mins=60,         # 回溯 60 分钟
    # position_check_interval_secs=None  # 默认禁用
)
```
