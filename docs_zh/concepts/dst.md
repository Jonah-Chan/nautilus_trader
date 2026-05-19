# 确定性模拟测试 (DST)

**确定性模拟测试 (Deterministic simulation testing, DST)** 在受种子控制的运行时下运行 NautilusTrader，使得对时间敏感的执行行为可以从单个整数实现位级别的复现。本指南介绍了什么是 DST、NautilusTrader 如何支持它、该支持提供的保证以及这些保证在何处停止。

其目标是建立一个公开的契约，外部用户和审核员可以进行验证：NautilusTrader 所声称的确定性由代码层面的证据支撑，并在提交时通过在持续集成中运行的 pre-commit 钩子强制执行。

## 简介 (Introduction)

### 什么是 DST (What DST is)

DST 是一种针对并发系统的测试技术。单个种子即可完全决定一次执行过程，包括任务调度、定时器触发和随机值。使用相同的种子、二进制文件和配置进行两次运行，将产生完全一致的可观察行为。当某个属性测试失败时，种子就是复现手段：相同的种子每次都能重现该失败。

异步运行时中的调度决策通常来自环境进程状态：任务唤醒顺序、定时器分辨率、线程调度、哈希种子。这些都不受测试框架控制，这就是为什么在 CI 中出现一次的竞态条件通常很难按需复现。DST 将这些环境来源替换为受种子控制的伪随机序列，因此交织 (Interleaving) 成了种子的函数。

FoundationDB 从 2009 年左右开始在其生产级分布式数据库中应用这一模式；在 Rust 生态系统中，[madsim](https://github.com/madsim-rs/madsim) 通过拦截 `tokio` 原语来提供确定性调度器。

DST 针对的是那些逃过了单元测试、集成测试、属性测试和验收测试的漏洞：通道唤醒顺序、关机时的排空竞态、启动顺序、对账顺序、恢复路径的正确性。所有这些都涉及其他测试层无法穷举覆盖的交织，但确定性调度器可以系统地对其进行探索。

### 本指南涵盖的内容 (What this guide covers)

NautilusTrader 的 DST 支持由两部分组成：

- **契约 (The contract)**：运行时在受种子控制的执行下保证什么，以及在哪些条件下保证。
- **强制执行 (The enforcement)**：实现该契约的源代码层面接缝 (Seams)，以及维持这些接缝的 pre-commit 钩子。

## 目标 (Goals)

- 为 NautilusTrader 运行时范围内部分实现**种子可复现的执行**。
- **诚实的范围**。契约列出了涵盖的内容和不涵盖的内容。不会静默回退到真实的挂钟时间或不受种子控制的 RNG；枚举了削弱保证的条件。
- **源代码强制执行**。pre-commit 钩子会拒绝向 DST 路径添加禁用模式的提交，因此契约的真实性不依赖于评审人员的注意力。
- **最小限度的仪器化 (Instrumentation)**。仅在契约要求的对接缝处，将时间、任务调度和随机性路由到确定性源；其他所有内容保持不变。

## 方法 (Approach)

`madsim` 仅对通过其别名子模块（`time`、`task`、`runtime`、`signal`）路由的 `tokio` 原语进行确定性处理。挂钟时间读取、单调时间读取、RNG 抽取、哈希迭代和 `select!` 轮询完全绕过了 `tokio`，需要它们自己的接缝。第 1 层将别名子模块替换为 `madsim`；第 2 层提供接缝。

### 第 1 层：运行时交换 (Runtime swap)

在 `nautilus-common` 的 `simulation` Cargo 特性下，当设置了 `RUSTFLAGS="--cfg madsim"` 时，四个 `tokio` 子模块将被路由到 `madsim`：

- `time`（定时器、时间间隔、单调 `Instant`）。
- `task`（异步任务的派生和加入）。
- `runtime`（运行时构建器和句柄）。
- `signal`（进程信号，如 `ctrl_c`；支持重新导出，调用侧采用是部分采用，详见 [范围边界](#信号处理)）。

这些重新导出位于 `nautilus_common::live::dst`。DST 路径上对 `time`、`task` 和 `runtime` 的调用从该模块导入，而不是直接从 `tokio` 导入，因此切换特性可以在一个地方为完全路由的原语切换异步运行时。在正常构建下，重新导出解析为真实的 `tokio`。在 `simulation` + `cfg(madsim)` 下，它们解析为 `madsim` 对应的确定性组件。

`tokio` 提供的其他所有内容（`sync`、`io`、作为宏的 `select!`、`fs`、`net`）无条件使用真实的 `tokio`。传递依赖的 Crate (`tokio-tungstenite`、`tokio-rustls`、`reqwest`) 不受影响。

### 第 2 层：非确定性替换 (Nondeterminism substitution)

异步运行时之外的非确定性通过显式的接缝进行重定向：

- **挂钟时间读取**通过 `nautilus_core::time::duration_since_unix_epoch`。在模拟下，这路由到 `madsim::time::TimeHandle::try_current()`，保留了顺序和成交时间戳的 Unix 纪元语义。当在 madsim 运行时之外调用时（普通的 `#[rstest]` 测试体），它回退到 `SystemTime::now()`，在 `cfg(madsim)` 下，这被 libc 拦截到与正常构建相同的真实系统调用。模拟下的生产路径始终在运行时内运行，因此它们继续接收虚拟时间。
- **单调时间读取**通过 `nautilus_common::live::dst::time::Instant`。该类型在正常构建中解析为 `tokio::time::Instant`（为了与 `tokio::test(start_paused)` 测试助手兼容），在模拟下解析为 `madsim::time::Instant`。
- **网络本地单调时间读取**通过 `nautilus_network::dst::time`。该 Crate 在依赖图中位于 `nautilus-common` 之下，并暴露一个具有相同语义的本地重新导出模块。
- **哈希迭代顺序**在对账管理器和订单撮合引擎中使用 `IndexMap` 和 `IndexSet`，而不是 `AHashMap` 和 `AHashSet`。`AHash` 会为每个进程随机化其哈希器；在订单驱动下游事件发布或受种子控制的 `FillModel` RNG 消费序列的情况下，需要按插入顺序进行迭代。
- **`tokio::select!` 轮询顺序**在 DST 路径上的每个生产位置都使用 `biased;` 修饰符。无偏 (Unbiased) 的 `select!` 会按照不受拦截的 RNG 选择的分支顺序进行轮询。

## 确定性契约 (Determinism contract)

在以下条件下，同一平台上由 `(seed, binary hash, configuration hash)` 标识的运行将产生位级别完全一致的结果：

1. 异步任务的调度顺序。
2. 定时器触发（虚拟单调时间和虚拟挂钟时间）。
3. 来自 `madsim::rand` 的 RNG 输出。
4. `tokio` 原语上的通道交付顺序。

### 必要条件 (Required conditions)

契约仅在以下所有条件都满足时有效：

1. `simulation` Cargo 特性处于激活状态，且设置了 `RUSTFLAGS="--cfg madsim"`。两者缺一不可。该特性激活确定性运行时；cfg 标志激活 `madsim` 的 libc 级别 `clock_gettime` 和 `getrandom` 拦截。如果只有一个而没有另一个，则会静默回退到真实的 `tokio`，并破坏确定性而不会报错。
2. DST 路径上的每个 `tokio::select!` 调用位置都使用 `biased;` 修饰符。
3. 单调时间读取通过 DST 接缝路由（`nautilus_common::live::dst::time` 或 `nautilus_network::dst::time`），而不是直接使用 `std::time::Instant::now`。
4. 挂钟时间读取通过 `nautilus_core::time::duration_since_unix_epoch` 路由。
5. 随机性通过 `madsim::rand` 路由。`rand::thread_rng`、`rand::rng()`、`fastrand`、`getrandom` 和 `OsRng` 不会被拦截。
6. 对迭代顺序敏感的集合使用 `IndexMap` 或 `IndexSet`，而不是 `AHashMap` 或 `AHashSet`。
7. `tokio::task::LocalSet` 的构造在模拟下通过 cfg 门控被排除。`madsim` 不提供 `LocalSet`；`spawn_local` 无需它即可工作。
8. `tokio::task::spawn_blocking` 的调用位置被 cfg 门控或移除。阻塞调用会逃出确定性调度器。

## 静态强制执行 (Static enforcement)

一个名为 `check-dst-conventions` 的 pre-commit 钩子在源代码中强制执行结构化条件。该钩子位于 `.pre-commit-hooks/check_dst_conventions.sh`，作为标准 pre-commit 套件的一部分运行，并在持续集成中运行。它涵盖了 16 个范围内的工作区 Crate，并在检测到以下任何情况时使提交失败：

- 原始 `std::time::Instant::now()` 或 `SystemTime::now()` 读取，包括当包含文件从 `std::time` 导入该类型时的裸形式。
- 未经 cfg 门控的原始 RNG 使用（`rand::thread_rng`、`rand::rng()`、`fastrand::`、`getrandom::`、`OsRng`）。
- `tokio::select!` 块在前三行内缺失 `biased;`。
- 缺少前置 `#[cfg(test)]`、`#[cfg(not(madsim))]` 或 `#[cfg(not(all(feature = "simulation", madsim)))]` 属性的 `std::thread::spawn`、`std::thread::Builder::new` 或 `tokio::task::spawn_blocking` 调用。
- 在 DST 路径上对迭代顺序敏感的文件中使用 `AHashMap` 或 `AHashSet`。完整的文件集合正在审核中；强制执行目前涵盖 `crates/live/src/manager.rs` 和 `crates/execution/src/matching_engine/engine.rs`，并随着更多文件的评审而扩展。
- 直接调用 `tokio::net::TcpStream::connect` / `tokio::net::TcpListener::bind` 而绕过 `nautilus_network::net`。接缝在正常构建下重新导出 `tokio::net` 类型，并在 `turmoil` 特性下切换到 `turmoil::net`，因此所有 TCP 入口点共享一个单一的 cfg 门控交换点。

该钩子支持两种异常形式：

- 特定行上的内联 `// dst-ok` 标记，通常附带简短原因（例如，不影响状态的仅用于日志的挂钟计时）。
- 钩子脚本本身中有一个小的文件级允许列表，用于代码库审核中归类为“保持现状”的位置（缓存模块中的日志计时，DeFi 模块中的进度报告）。

测试文件、`tests/`、`python/` 和 `ffi/` 目录下的文件，以及内联 `#[cfg(test)]` 模块内的行都被排除在外，因为它们不是 DST 路径的一部分。

### 范围内的 Crate (In-scope crates)

该钩子适用于 `nautilus-live` 传递闭包中的 16 个工作区 Crate：

- `analysis`、`common`、`core`、`cryptography`、`data`、`execution`、`indicators`、`live`、`model`、`network`、`persistence`、`portfolio`、`risk`、`serialization`、`system`、`trading`。

适配器 (Adapter) Crate 和基础设施 Crate (Redis, Postgres) 不在范围内。它们在进入 DST 路径之前需要单独的审核。

## 实现说明 (Implementation notes)

此代码库中 DST 审核产生的具体变更。在调查代码路径是否在 DST 路径上以及它今天如何路由时，请以此为起点。

### 迭代顺序接缝 (Iteration-order seams)

由于迭代顺序在 DST 路径上是可观察的，生产位置中的 `AHashMap` / `AHashSet` 切换到了 `IndexMap` / `IndexSet`：

- **撮合引擎 (Matching engine)** (`crates/execution/src/matching_engine/engine.rs`)：9 个字段 (`execution_bar_types`、`execution_bar_deltas`、`account_ids`、`cached_filled_qty`、`bid_consumption`、`ask_consumption`、`queue_ahead`、`queue_excess`、`queue_pending`)。迭代删除使用 `.shift_remove()`。解决了 [#3914](https://github.com/nautechsystems/nautilus_trader/issues/3914)。
- **对账管理器 (Reconciliation manager)** (`crates/live/src/manager.rs`)：由钩子强制执行，外加 `ReconciliationResult.orders` 和 `ReconciliationResult.fills`。
- **账户 Trait (Account trait)** (`crates/model/src/accounts/`)：`balances`、`balances_total`、`balances_free`、`balances_locked`、`starting_balances` 返回值。`BaseAccount` 和 `MarginAccount` 上的存储字段为 `IndexMap`。
- **头寸事件 (Position events)** (`crates/model/src/position.rs`)：`Position::commissions` 切换到了 `IndexMap`（在 `events/position/snapshot.rs` 中通过 `.values()` 消费）。
- **投资组合聚合 (Portfolio aggregation)** (`crates/portfolio/src/portfolio.rs`)：`unrealized_pnls`、`realized_pnls`、`net_positions` 存储；`accumulate_mark_values` 构建 `IndexMap<Currency, f64>`。
- **数据引擎 (Data engine)** (`crates/data/src/engine/`)：`book_snapshot_counts`、`bar_aggregators`、`BookSnapshotInfos`。迭代删除使用 `.shift_remove()`。
- **执行引擎 (Execution engine)** (`crates/execution/src/engine/`)：`ExecutionEngine.clients`，以及 `get_clients_for_orders()` 中的 `client_ids` / `venues` 累加器。
- **交易算法 (Trading algorithm)** (`crates/trading/src/algorithm/core.rs`)：`strategy_event_handlers`（驱动有序的 `msgbus::unsubscribe_*` 扇出）。
- **分析器 (Analyzer)** (`crates/analysis/src/analyzer.rs`)：`account_balances`、`account_balances_starting`。
- **缓存 API (Cache API)** (`crates/common/src/cache/mod.rs`)：`get_orders_for_ids` 和 `get_positions_for_ids` 在返回前按 `client_order_id` / `position_id` 对其 `Vec` 返回值进行排序。存储保持在 `AHashSet`（集合语义）。

范围内 Crate 中剩余的 `AHashMap` / `AHashSet` 位置仅用于查找、位于并发共享所有权包装器 (`Arc<DashMap>`、`AtomicMap`) 之后，或者喂入可交换的聚合中。任何驱动可观察迭代顺序的新范围内位置都是一次退化，受各区域审核的保护。

### 时间接缝 (Time seams)

保留在 DST 路径上的 `Instant::now` / `SystemTime::now` 调用位置要么位于 `#[cfg(test)]` 内、在钩子的文件允许列表中，要么带有内联的 `// dst-ok` 标记并说明原因：

- `crates/common/src/testing.rs:81,108` `wait_until` / `wait_until_async`
- `crates/execution/src/engine/mod.rs:822,847` 初始化日志计时
- `crates/common/src/cache/mod.rs:569,904,3895` 日志和审核计时（文件允许列表）
- `crates/model/src/defi/reporting.rs:59,123` 进度日志（文件允许列表）
- `crates/core/src/time.rs` 接缝定义位置（文件允许列表）

`chrono::Utc::now` 在范围内 Crate 中被钩子禁用。剩余的调用位置是日志桥接器和写入器（在“日志在真实 OS 线程上运行”下被排除）。`crates/core/src/datetime.rs::is_within_last_24_hours` 助手以前曾从非日志路径调用 `chrono::Utc::now`；它现在通过 `nautilus_core::time::nanos_since_unix_epoch()` 路由并直接在 `u64` 纳秒中进行比较。

### 随机性接缝 (Randomness seams)

DST 路径上的生产 RNG 位置：

- `crates/core/src/uuid.rs::UUID4::new()` 在模拟下于 madsim 运行时内部调用时通过 `madsim::rand::thread_rng()` 路由，在运行时外部（以及正常构建下）回退到 `rand::rng()`。模拟下的生产路径始终在运行时内部运行，因此它们消费受种子控制的字节；`cfg(madsim)` 下的普通 `#[rstest]` 测试使用宿主 RNG。可从 `nautilus-common` 和 `nautilus-risk` 中的订单和事件工厂访问。
- `crates/execution/src/models/fill.rs::default_std_rng()` 以同样的方式路由。当未提供种子时从 `ProbabilisticFillState::new()` 调用。有了种子，`StdRng::seed_from_u64` 按构造就是确定性的。
- `crates/execution/src/matching_engine/ids_generator.rs:167,179` 在 `use_random_ids` 路径上使用 `nautilus_core::UUID4::new()`。默认的 ID 方案 (`{venue}-{raw_id}-{count}`) 在没有它的情况下也是确定性的。

带标记允许：`crates/network/src/backoff.rs:105` 用于重连抖动，`// dst-ok` (传输层)。

### Tokio 子模块拆分 (Tokio submodule split)

`madsim` 别名了 `time`、`task`、`runtime` 和 `signal`。其他 tokio 子模块（`sync`、`io`、`select!`、`fs`、`net`）在模拟下仍保持在真实 tokio 上。进一步扩展交换将需要针对垫片化的 `tokio::net::TcpStream` 重新构建 `tokio-tungstenite`、`tokio-rustls` 和 `reqwest`，审核认为这太具侵入性而被排除。

范围内直接接触真实 `tokio::net` / `tokio::io` 的位置：

- `crates/network/src/net.rs:37` 重新导出 `tokio::net::{TcpListener, TcpStream}`
- `crates/network/src/socket/client.rs:46,356` `tokio::io::{AsyncReadExt, AsyncWriteExt}`
- `crates/network/src/tls.rs:22` `tokio::io::{AsyncRead, AsyncWrite}`
- `crates/network/src/websocket/types.rs:26,29` 别名 `MaybeTlsStream<tokio::net::TcpStream>`

即使在模拟下，这些也运行在真实的套接字上。`tokio::sync` 上的通道交付顺序保持确定性，因为发送者和接收者任务是由 madsim 执行器调度的，即使通道实现是真实的。

### 原始线程逃逸规则 (Raw thread escape rules)

钩子的第 4 条规则禁止在三种逃逸情况之外派生原始线程：

- `#[cfg(test)]` 测试模块。
- `#[cfg(not(madsim))]` 或 `#[cfg(not(all(feature = "simulation", madsim)))]` 生产位置（例如日志写入器线程）。
- 内联 `// dst-ok` 标记。

`tokio::task::LocalSet` 和 `tokio::task::spawn_blocking` 在 `madsim` 下不支持。代码库审核未在范围内 Crate 中发现两者的任何生产位置；新位置必须带有 cfg 门控或 `// dst-ok` 标记。

### 模拟下的日志测试 (Logging tests under simulation)

日志写入器线程在模拟下通过 cfg 门控排除；在 `cfg(madsim)` 下，日志事件将被丢弃。初始化文件日志写入器的测试要么会挂起，要么会对空的日志文件进行断言，因此受影响的子模块在模块边界处被门控排除：

- `crates/common/src/logging/logger.rs::tests::serial_tests`（8 个测试）。
- `crates/common/src/logging/macros.rs::tests`（2 个测试）。

`logger.rs::tests::sim_tests::test_init_under_madsim_skips_writer_thread_and_forces_bypass` 在模拟下运行并锁定门控行为。

## 范围边界 (Scope boundaries)

契约被刻意收窄。以下削弱是显而易见的，而非疏忽。

### Python 不在 DST 范围内 (Python is not in DST scope)

DST 在原生 Rust 测试框架下运行。在 DST 运行期间不会启动 Python 解释器。`crates/*/src/python/` 下的 PyO3 绑定、`ffi/` 目录以及 `nautilus_trader/` 下的 Python 包作为政策（而非弱点）被排除在契约之外。任何仅可从 Python 调用路径访问的代码都不在范围内；任何可从原生 DST 框架访问的 Rust 路径都必须满足契约，即使该类型也导出了 Python。

`check-dst-conventions` 钩子通过跳过范围内 Crate 中的 `/python/` 和 `/ffi/` 路径来编码这一政策。这些路径背后的时钟、RNG 和线程调用位置不适用于契约。

DST 的主要目标是 Rust 引擎本身的可靠性：订单生命周期、对账、撮合、风险和执行状态机。用户策略的确定性复现是一个稍后的次要目标，一旦策略使用 Rust 编写或通过 Rust 原生测试框架运行，该目标即可实现。在此期间，一个调用 `time.time()`、发起任意网络请求或依赖线程调度的 Python 策略在运行之间可能会改变其命令流；Rust 核心将确定性地处理变化的流，但不能保证从 Python 入口点进行端到端复现。

### 平台受限 (Platform-scoped)

`madsim` 对 `clock_gettime` 和 `getrandom` 的 libc 覆盖是平台相关的。不声称跨平台的位级别可复现性。在 Linux x86_64 上复现失败的种子可能无法在 macOS aarch64 上复现。

### 非别名依赖项会静默逃逸 (Non-aliased dependencies escape silently)

任何通过非别名路径接触 OS 的依赖项（直接 `libc` 调用、`std::net` 绕过、使用 `fastrand` 或 `OsRng` 的 Crate）都会逃出模拟器而不会报错。范围内 Crate 已经过审核；适配器 Crate 和基础设施 Crate 在进入 DST 路径之前需要进行自己的审核。

### 传输层 I/O 未被模拟 (Transport-layer I/O is not simulated)

`tokio-tungstenite`、`tokio-rustls`、`reqwest`、`redis` 和 `sqlx` 在内部使用真实的 `tokio`。在模拟下，WebSocket 和 HTTP I/O 运行在真实网络上。这是有意为之的：初始目标是订单生命周期的确定性，而不是传输故障注入。传输层确定性将需要目前尚不存在的每 Crate `madsim` 垫片。

驱动真实本地主机套接字的测试模块（`crates/network/src/socket/client.rs::tests`、`::rust_tests`；`crates/network/src/websocket/client.rs::tests`、`::rust_tests`；`crates/network/tests/websocket_proxy.rs`）在 `all(feature = "simulation", madsim)` 下通过 cfg 门控排除，因为它们的生产代码路径触及 `dst::time::*`（madsim 时间原语），当从 `#[tokio::test]` 运行时调用时会引起 panic。重试测试模块 (`crates/network/src/retry.rs::tests`, `::proptest_tests`) 在模拟下运行：每个测试属性在 `#[tokio::test(start_paused = true)]` 和 `#[madsim::test]` 之间进行 `cfg_attr` 切换，时间读取和休眠通过 `crate::dst::time` 路由，显式的虚拟时间推进通过 cfg 门控的 `advance_clock` 助手进行，因此同一测试体涵盖了两种运行时。

### 信号处理 (Signal handling)

`nautilus_common::live::dst::signal` 暴露了一个路由后的 `ctrl_c` 重新导出。`crates/live/src/node.rs` 的运行循环通过它进行路由，因此由 `ctrl_c` 驱动的节点关机可以通过 `madsim::runtime::Handle::send_ctrl_c` 在 `cfg(madsim)` 下的测试代码中进行注入。适配器二进制入口点仍然直接调用 `tokio::signal::ctrl_c`，仍被排除在范围外。

### 日志在真实 OS 线程上运行 (Logging runs on real OS threads)

日志子系统通过 `std::thread::Builder` 派生一个写入器线程，并使用 `std::sync::mpsc`。在模拟下，该线程不会被派生，日志事件将被丢弃。日志输出在确定性契约之外：写入器只负责写入，从不读取或修改模拟状态。

### 适配器 (Adapters)

适配器 Crate 不在初始 DST 契约的范围内。每个适配器都有自己的一组 `chrono::Utc::now`、`SystemTime::now`、`Uuid::new_v4` 和传输层调用位置。进入 DST 路径的适配器必须在对其时钟、RNG 和传输的直接使用进行审核后，其行为才能被契约复现。

### 进程全局惰性状态在首次调用时消费 RNG 字节 (Process-global lazy state consumes RNG bytes on first call)

契约在单次运行运行时内有效。一些进程全局惰性初始化在首次调用时会消费 RNG 字节，这对于在同一个进程中运行两次受种子控制的执行并比较追踪记录的测试框架来说非常重要。

- `Ustr::from()` 驻留器在首次使用时分配并种子化其内部 Map。
- `ahash::RandomState` 在首次创建实例时通过 `getrandom`（`madsim` 在 `cfg(madsim)` 下对其进行了挂载）进行种子设定。

单运行时测试（每个种子调用一个测试体，新鲜进程）不受影响：这种消费是受种子控制的执行的一部分，并且会确定性地复现。

在同一个进程中两次调用测试体以对比追踪记录的同种子相等性框架将会看到运行之间的漂移。第 1 次运行支付了惰性初始化成本并消费了 RNG 字节；第 2 次运行继承了预热后的状态，并从 RNG 序列的另一个偏移量开始。

解决方法：在比较之前的运行时外部预热进程全局状态，例如在进程启动时调用一次 `Ustr::from("")` 并构造一个 `ahash::RandomState`。两次运行随后都将从预热后的状态开始，并以相同方式消费 RNG 序列。

### 适配器工厂不再暴露 `Rc<RefCell<Cache>>` (Adapter factories no longer expose `Rc<RefCell<Cache>>`)

提交 `f0ea66da15`（“通过 `CacheView` 标准化适配器缓存访问”）将 `DataClientFactory::create` 和 `ExecutionClientFactory::create` 上的可变 `Rc<RefCell<Cache>>` 参数替换为了 `CacheView`。`CacheView` 暴露了用于读取访问的 `borrow()`，但没有暴露内部的 `Rc` 句柄。

这阻碍了需要在工厂内部构造 `OrderMatchingEngine` 的 DST 风格框架工厂，因为 `OrderMatchingEngine::new` 仍然接受 `Rc<RefCell<Cache>>`，而目前没有公开的访问器可以从 `CacheView` 中恢复该句柄。

解决方法：

- 将框架消费者固定到 `f0ea66da15` 之前的提交。
- 重构框架，使其在工厂外部（核心 `Cache` 句柄仍然可达的位置）构建 `OrderMatchingEngine`，并将构造好的引擎传递给客户端。

长期的应急方案要么是 `CacheView` 上内部句柄的访问器，要么是接受 `CacheView` 的替代 `OrderMatchingEngine` 构造函数。目前代码树中还没有这些内容。

## 与其他测试层的关系 (Relationship to other testing layers)

DST 是对现有测试的补充，而非替代。

| 层次 | 覆盖内容 | DST 关系 |
|-------------------------|------------------------------------------------------|-------------------------------------------------|
| 单元测试 | 纯逻辑、计算、解析器、转换器。 | 保持不变。 |
| 集成测试 | 组件交互、I/O 边界。 | 保持不变。DST 与其并列运行，而非替代。 |
| 属性测试 | 输入域上的不变性（解析器、往返）。 | 保持不变。 |
| 验收测试 | 端到端回测和实盘场景。 | 保持不变。 |
| 确定性模拟 (DST) | 异步计时、调度、恢复正确性。 | 增加了受种子控制的可重放探索。 |

DST 的独特价值在于异步并发与状态机正确性的交集。诸如“在特定唤醒顺序下关机时消息丢失”或“当迭代顺序反转时对账事件丢失”之类的漏洞是其目标类别。除此之外，预先存在的测试层仍是合适的工具。

## 现状 (Status)

截至本代码库当前状态：

- 第 1 层（运行时交换）已实现。`nautilus_common::live::dst` 暴露了 `time`、`task`、`runtime` 和 `signal` 的路由重新导出。`time`、`task` 和 `runtime` 的生产调用位置通过接缝路由；信号调用位置的采用是部分采用（见“范围边界”下的“信号处理”）。
- 第 2 层（非确定性替换）已在 16 个范围内 Crate 中实现。挂钟时间、单调时间、随机性和迭代顺序均已建立接缝。审核结论和剩余允许的调用位置在“实现说明”中列出。
- 通过 `check-dst-conventions` 的静态强制执行已在 pre-commit 和 CI 中激活。钩子涵盖了关键负载条件；`// dst-ok` 标记约定允许在有理由时进行逐行例外处理。
- `cfg(madsim)` 下的构建与测试烟雾门控通过 `dst` 工作流运行（`.github/workflows/dst.yml`，调用 `make cargo-test-sim`）。它使用 `--features simulation` 编译范围内 Crate，并运行目前所有模拟兼容的测试。消费 `nautilus-model` 类型的 Crate（`nautilus-common`、`nautilus-execution`）还运行带有 `--features "simulation,high-precision"` 的第二阶段，以便在两种定点宽度（`QuantityRaw` / `PriceRaw` 为 `u64` vs `u128`）下行使接缝路由的代码路径。
  - `nautilus-common` 全体。此阶段在传播了 `nautilus-core/simulation` 的情况下编译，因此套件中的每个测试都选择了显式的 `wall_clock_now` cfg 分支。普通的 `#[rstest]` 测试在 madsim 运行时外部运行，并通接缝的 `SystemTime::now()` 回退（madsim 的 libc 垫片在运行时外部采取的路径相同）。此阶段中的 `live::dst::tests::test_dst_wall_clock_advances_with_virtual_time` 测试使用 `#[madsim::test]` 并断言 `nanos_since_unix_epoch` 随 `madsim::time::sleep` 推进，因此虚拟挂钟行为在 common 阶段得到了端到端的验证。
  - `nautilus-network` 全体（绑定传输的测试模块在源码处被门控排除）。包括休眠/超时虚拟时间、频率限制器的接缝锁定测试，以及在虚拟时间下行使退避计时的重试套件。
  - `nautilus-execution` 全体。撮合引擎、成交模型和执行引擎状态机在受种子控制的 RNG 确定性调度器下运行。
  - `nautilus-core` 中的跨 Crate 接缝锁定测试（`wall_clock_now` 虚拟时间）。每个阶段都使用其自身 Crate 的 `--features simulation` 运行，并在适用处使用 `#[madsim::test]`，因此显式的 cfg 分支和虚拟时间都得到了验证。

  这些举措共同捕捉了 cfg 门控 DST 接缝中的漂移，并在确定性调度器下行使了范围内状态机；目前尚未实现端到端的确定性验证。
- 端到端运行时验证（在范围内代码路径上进行同种子差异对比）目前不在本代码库范围内。结构化条件（规则 1 到规则 6）已被强制执行；关于种子可以在运行之间产生完全一致的可观察行为的说法在接缝设计上是合理的，但尚未通过回归门控进行验证。

## 延伸阅读 (Further reading)

- `.pre-commit-hooks/check_dst_conventions.sh` 完整定义了五条强制执行规则，并记录了 `// dst-ok` 标记约定。
- 外部参考：[FoundationDB 测试理念](https://apple.github.io/foundationdb/testing.html)、[TigerBeetle 模拟测试博客文章](https://tigerbeetle.com/blog/)，以及确定性运行时的 [madsim 代码库](https://github.com/madsim-rs/madsim)。
