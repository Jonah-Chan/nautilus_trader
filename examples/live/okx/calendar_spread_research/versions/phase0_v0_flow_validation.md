# Phase 0 `v0_flow_validation`

## 定位

`../strategies/phase0_v0_flow_validation.py` 已冻结为 `v0_flow_validation`。它验证 OKX option discovery、`OptionChainSlice` 订阅、BTC+ETH universe、L1 bid/ask 开平仓参数、Nautilus sandbox order lifecycle，以及 SCANNING -> OPEN -> CLOSING -> FLAT 的基础状态机。

它不是生产策略，也不得吸收 positive-expectancy signal、execution planner、risk/recovery 或 near-production 逻辑。

根目录 `../../okx_option_calendar_spread_dynamic.py` 只是兼容入口。所有日历价差策略实现都归 `../strategies/` 管理。

## 已完成工作

- BTC+ETH live market data + Nautilus sandbox execution run。
- Phase 0 策略代码迁入 research strategy 目录，并保留 fixed two-leg reference。
- 按 open-close cycle 做 PnL 归因。
- 报告拆分 `AccountBalance` delta、`PositionClosed.realized_pnl`、fees、unclosed state、warnings、errors。
- 增加 entry/exit executable prices、spreads、quote age、lifecycle duration、simulated fill source 可观测字段。
- 增加单腿成交、IOC partial/cancel、reduce-only close reject、missing close quote、restart existing position、subscription recovery 单元 fixture。
- 增加 activation/listing-age guard，避免 OKX 新上市 strike 在 `bbo-tbt` 可订阅前进入订阅。
- OPEN 状态下重申 active-leg quote subscription，避免 option-chain rebalance 移除 close quote path。
- adapter/cache 和 Phase 0 normalizer 两层过滤 unsupported `_UM` option-like instruments。
- 增加 `--stop-after-flat-seconds` 和 `--stop-after-completed-baskets`。
- 增加 `--time-in-force IOC|GTC|FOK`，分离 flow validation 与 sandbox IOC cancel semantics。

## 证据与结论

接受证据：

- log：`../../../../.omx/artifacts/dynamic-calendar-pnl/phase0_btc_eth_gtc_stopflat_2h_20260523T040010Z.log`
- HTML：`../../../../.omx/artifacts/dynamic-calendar-pnl/pnl_report_phase0_btc_eth_gtc_stopflat_2h.html`
- summary JSON：`../../../../.omx/artifacts/dynamic-calendar-pnl/pnl_summary_phase0_btc_eth_gtc_stopflat_2h.json`

结果：

- 144 cycles，144 completed，BTC=59，ETH=85。
- unclosed=0，close-only=0，unmatched fills=0，fills=576，`PositionClosed` events=288。
- 无 contextual OKX `60018`，无 `_UM`，无 `InvalidStateTrigger`，无 `Cannot cancel an order with INITIALIZED`，无 `FAILED_NEEDS_FLATTEN`，无 traceback。
- 所有 cycle 都是负 PnL，原因是机械 bid/ask crossing + sandbox fee；这是 flow harness 的成本基线，不是 signal edge 证据。
- 6 条 `[ERROR]` 为 websocket/TLS transport reset，策略随后继续完成 cycle 并回到 repeated `CALENDAR_STATUS | state=FLAT`。生产级 disconnect policy 归 Phase 4。

被拒绝但保留为诊断证据的 run：

- `phase0_btc_eth_2h_20260522T151536Z.log`：被新上市合约 `bbo-tbt` activation/listing-age 问题拒绝。
- `phase0_btc_eth_clean2h_20260522T155028Z.log`：active near-leg quote 被 rebalance 移除，导致 OPEN 状态缺失 close quote。
- `phase0_btc_eth_patched2h_20260522T155902Z.log`：生命周期可用，但 `_UM` option-like row 进入 cache 导致 OKX `60018`。
- `phase0_btc_eth_stopflat_2h_20260523T014412Z.log`：213 cycles lifecycle clean，但 sandbox IOC cancellation 产生 matching-engine error，证明 IOC sandbox semantics 不能直接当作交易所 IOC 证据。

## 门槛

- BTC/ETH discovery、settlement、balance、PnL path 已有接受证据。
- cycle 按 open-close 而不是重复 basket id 分组。
- account delta、realized PnL、fees、unclosed state 分离显示。
- 接受证据无未解释 matching/sandbox error。
- `_UM` cache instruments 不进入接受证据的 option-chain quote subscription。
- Phase 1-5 已按 Phase 0 真实证据收紧边界。

## 停止条件

Phase 0 已接受为可信 flow-validation harness。后续盈利、signal、execution planner、risk/recovery 都必须进入 Phase 1+ 模块；Phase 0 只允许 narrow validation bug fix。
