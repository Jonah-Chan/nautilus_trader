# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

from textwrap import dedent

from examples.live.okx.calendar_spread_research.tools.analyze_dynamic_calendar_pnl import (
    analyze_log_text,
)


def test_analyzer_splits_repeated_basket_id_into_distinct_cycles():
    basket_id = "calendar:BTC:BTC:CALL:100:1->2"
    log_text = dedent(
        f"""
        2026-05-22T00:00:00.000000000Z [INFO] T.DynamicCalendarSpreadStrategy: CALENDAR_CANDIDATE | basket_id={basket_id} | underlying=BTC | settlement=BTC | kind=CALL | strike=100 | near=BTC-1.OKX SELL qty=1 limit=1 tif=IOC | far=BTC-2.OKX BUY qty=1 limit=2 tif=IOC | open_long_cost=1 | dry_run=False
        2026-05-22T00:00:01.000000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] OrderInitialized(instrument_id=BTC-1.OKX, client_order_id=O1, side=SELL, type=LIMIT, quantity=1, time_in_force=IOC, post_only=False, reduce_only=False, options={{'price': '1', 'display_qty': None, 'expire_time_ns': 0}}, tags=['{basket_id}', 'open_sell_near'])
        2026-05-22T00:00:01.100000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] OrderInitialized(instrument_id=BTC-2.OKX, client_order_id=O2, side=BUY, type=LIMIT, quantity=1, time_in_force=IOC, post_only=False, reduce_only=False, options={{'price': '2', 'display_qty': None, 'expire_time_ns': 0}}, tags=['{basket_id}', 'open_buy_far'])
        2026-05-22T00:00:01.200000000Z [INFO] T.DynamicCalendarSpreadStrategy: Calendar basket submitted | basket_id={basket_id}
        2026-05-22T00:00:01.300000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] OrderFilled(instrument_id=BTC-1.OKX, client_order_id=O1, order_side=SELL, order_type=LIMIT, last_qty=1, last_px=1 USD, commission=0.01 BTC, liquidity_side=TAKER, ts_event=1)
        2026-05-22T00:00:01.400000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] OrderFilled(instrument_id=BTC-2.OKX, client_order_id=O2, order_side=BUY, order_type=LIMIT, last_qty=1, last_px=2 USD, commission=0.02 BTC, liquidity_side=TAKER, ts_event=1)
        2026-05-22T00:00:31.000000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] OrderInitialized(instrument_id=BTC-1.OKX, client_order_id=O3, side=BUY, type=LIMIT, quantity=1, time_in_force=IOC, post_only=False, reduce_only=True, options={{'price': '1.1', 'display_qty': None, 'expire_time_ns': 0}}, tags=['{basket_id}:close', 'close_buy_near'])
        2026-05-22T00:00:31.100000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] OrderInitialized(instrument_id=BTC-2.OKX, client_order_id=O4, side=SELL, type=LIMIT, quantity=1, time_in_force=IOC, post_only=False, reduce_only=True, options={{'price': '2.2', 'display_qty': None, 'expire_time_ns': 0}}, tags=['{basket_id}:close', 'close_sell_far'])
        2026-05-22T00:00:31.200000000Z [INFO] T.DynamicCalendarSpreadStrategy: Calendar close basket submitted | basket_id={basket_id}:close | reason=max_open_seconds
        2026-05-22T00:00:31.300000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] OrderFilled(instrument_id=BTC-1.OKX, client_order_id=O3, order_side=BUY, order_type=LIMIT, last_qty=1, last_px=1.1 USD, commission=0.01 BTC, liquidity_side=TAKER, ts_event=2)
        2026-05-22T00:00:31.350000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] PositionClosed(instrument_id=BTC-1.OKX, opening_order_id=O1, closing_order_id=O3, realized_pnl=-0.12 BTC, unrealized_pnl=0.00 BTC, duration_ns=30000000000)
        2026-05-22T00:00:31.400000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] OrderFilled(instrument_id=BTC-2.OKX, client_order_id=O4, order_side=SELL, order_type=LIMIT, last_qty=1, last_px=2.2 USD, commission=0.02 BTC, liquidity_side=TAKER, ts_event=2)
        2026-05-22T00:00:31.450000000Z [INFO] T.DynamicCalendarSpreadStrategy: Calendar spread state moved to FLAT after close fills
        2026-05-22T00:00:31.500000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] PositionClosed(instrument_id=BTC-2.OKX, opening_order_id=O2, closing_order_id=O4, realized_pnl=0.10 BTC, unrealized_pnl=0.00 BTC, duration_ns=30000000000)
        2026-05-22T00:00:40.000000000Z [INFO] T.DynamicCalendarSpreadStrategy: CALENDAR_CANDIDATE | basket_id={basket_id} | underlying=BTC | settlement=BTC | kind=CALL | strike=100 | near=BTC-1.OKX SELL qty=1 limit=1 tif=IOC | far=BTC-2.OKX BUY qty=1 limit=2 tif=IOC | open_long_cost=1 | dry_run=False
        2026-05-22T00:00:41.000000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] OrderInitialized(instrument_id=BTC-1.OKX, client_order_id=O5, side=SELL, type=LIMIT, quantity=1, time_in_force=IOC, post_only=False, reduce_only=False, options={{'price': '1', 'display_qty': None, 'expire_time_ns': 0}}, tags=['{basket_id}', 'open_sell_near'])
        2026-05-22T00:00:41.100000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] OrderInitialized(instrument_id=BTC-2.OKX, client_order_id=O6, side=BUY, type=LIMIT, quantity=1, time_in_force=IOC, post_only=False, reduce_only=False, options={{'price': '2', 'display_qty': None, 'expire_time_ns': 0}}, tags=['{basket_id}', 'open_buy_far'])
        2026-05-22T00:00:41.200000000Z [INFO] T.DynamicCalendarSpreadStrategy: Calendar basket submitted | basket_id={basket_id}
        2026-05-22T00:00:41.300000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] OrderFilled(instrument_id=BTC-1.OKX, client_order_id=O5, order_side=SELL, order_type=LIMIT, last_qty=1, last_px=1 USD, commission=0.01 BTC, liquidity_side=TAKER, ts_event=3)
        2026-05-22T00:00:41.400000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] OrderFilled(instrument_id=BTC-2.OKX, client_order_id=O6, order_side=BUY, order_type=LIMIT, last_qty=1, last_px=2 USD, commission=0.02 BTC, liquidity_side=TAKER, ts_event=3)
        """,
    )

    analysis = analyze_log_text(log_text)

    assert analysis["summary"]["cycle_count"] == 2
    assert analysis["summary"]["completed_cycles"] == 1
    assert analysis["summary"]["open_or_unclosed_cycles"] == 1
    assert [cycle["sequence"] for cycle in analysis["cycles"]] == [1, 2]
    assert analysis["cycles"][0]["cycle_id"].endswith("#0001")
    assert analysis["cycles"][1]["cycle_id"].endswith("#0002")
    assert analysis["cycles"][0]["realized_pnl_by_currency"] == {"BTC": "-0.02"}


def test_analyzer_separates_account_balance_delta_fees_and_position_pnl_by_asset():
    basket_id = "calendar:ETH:ETH:CALL:4000:1->2"
    log_text = dedent(
        f"""
        2026-05-22T00:00:00.000000000Z [INFO] T.Portfolio: Updated AccountState(account_id=OKX-001, balances=[AccountBalance(total=10 BTC, locked=0 BTC, free=10 BTC), AccountBalance(total=100 ETH, locked=0 ETH, free=100 ETH), AccountBalance(total=1_000 USD, locked=0 USD, free=1_000 USD)], margins=[])
        2026-05-22T00:00:01.000000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] OrderInitialized(instrument_id=ETH-1.OKX, client_order_id=E1, side=SELL, type=LIMIT, quantity=1, time_in_force=IOC, post_only=False, reduce_only=False, options={{'price': '1', 'display_qty': None, 'expire_time_ns': 0}}, tags=['{basket_id}', 'open_sell_near'])
        2026-05-22T00:00:01.100000000Z [INFO] T.DynamicCalendarSpreadStrategy: Calendar basket submitted | basket_id={basket_id}
        2026-05-22T00:00:01.200000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] OrderFilled(instrument_id=ETH-1.OKX, client_order_id=E1, order_side=SELL, order_type=LIMIT, last_qty=1, last_px=1 USD, commission=0.03 ETH, liquidity_side=TAKER, ts_event=1)
        2026-05-22T00:00:02.000000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] OrderInitialized(instrument_id=ETH-1.OKX, client_order_id=E2, side=BUY, type=LIMIT, quantity=1, time_in_force=IOC, post_only=False, reduce_only=True, options={{'price': '1.1', 'display_qty': None, 'expire_time_ns': 0}}, tags=['{basket_id}:close', 'close_buy_near'])
        2026-05-22T00:00:02.100000000Z [INFO] T.DynamicCalendarSpreadStrategy: Calendar close basket submitted | basket_id={basket_id}:close | reason=max_open_seconds
        2026-05-22T00:00:02.200000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] OrderFilled(instrument_id=ETH-1.OKX, client_order_id=E2, order_side=BUY, order_type=LIMIT, last_qty=1, last_px=1.1 USD, commission=0.04 ETH, liquidity_side=TAKER, ts_event=2)
        2026-05-22T00:00:02.300000000Z [INFO] T.DynamicCalendarSpreadStrategy: Calendar spread state moved to FLAT after close fills
        2026-05-22T00:00:02.400000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] PositionClosed(instrument_id=ETH-1.OKX, opening_order_id=E1, closing_order_id=E2, realized_pnl=-0.12 ETH, unrealized_pnl=0.00 ETH, duration_ns=1000000000)
        2026-05-22T00:00:03.000000000Z [INFO] T.Portfolio: Updated AccountState(account_id=OKX-001, balances=[AccountBalance(total=10 BTC, locked=0 BTC, free=10 BTC), AccountBalance(total=99.88 ETH, locked=0 ETH, free=99.88 ETH), AccountBalance(total=1_000 USD, locked=0 USD, free=1_000 USD)], margins=[])
        """,
    )

    analysis = analyze_log_text(log_text)
    summary = analysis["summary"]

    assert summary["cycles_by_underlying"] == {"ETH": 1}
    assert summary["cycles_by_settlement"] == {"ETH": 1}
    assert summary["balance_delta"]["ETH"] == "-0.12"
    assert summary["fees_by_currency"] == {"ETH": "0.07"}
    assert summary["position_closed_realized_pnl_by_currency"] == {"ETH": "-0.12"}


def test_analyzer_classifies_close_only_records():
    basket_id = "calendar:BTC:BTC:CALL:100:1->2"
    log_text = dedent(
        f"""
        2026-05-22T00:00:00.000000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] OrderInitialized(instrument_id=BTC-1.OKX, client_order_id=C1, side=BUY, type=LIMIT, quantity=1, time_in_force=IOC, post_only=False, reduce_only=True, options={{'price': '1', 'display_qty': None, 'expire_time_ns': 0}}, tags=['{basket_id}:close', 'close_buy_near'])
        2026-05-22T00:00:00.100000000Z [INFO] T.DynamicCalendarSpreadStrategy: <--[EVT] OrderFilled(instrument_id=BTC-1.OKX, client_order_id=C1, order_side=BUY, order_type=LIMIT, last_qty=1, last_px=1 USD, commission=0.01 BTC, liquidity_side=TAKER, ts_event=1)
        """,
    )

    analysis = analyze_log_text(log_text)

    assert analysis["summary"]["cycle_count"] == 1
    assert analysis["summary"]["close_only_cycles"] == 1
    assert analysis["cycles"][0]["status"] == "CLOSE_ONLY"


def test_analyzer_strips_ansi_color_sequences():
    log_text = (
        "\x1b[1m2026-05-22T00:00:00.000000000Z\x1b[0m "
        "\x1b[36m[INFO] T.DynamicCalendarSpreadStrategy: "
        "Calendar basket submitted | basket_id=calendar:BTC:BTC:CALL:100:1->2\x1b[0m\n"
    )

    analysis = analyze_log_text(log_text)

    assert analysis["summary"]["records_total"] == 1
    assert analysis["summary"]["cycle_count"] == 1


def test_analyzer_accepts_rust_components_with_colons():
    log_text = (
        "2026-05-22T00:00:00.000000000Z [WARN] "
        "DYN-CALENDAR-001.nautilus_network::websocket::client: "
        "Sockudo backend does not support proxy_url\n"
    )

    analysis = analyze_log_text(log_text)

    assert analysis["summary"]["records_total"] == 1
    assert analysis["summary"]["warnings_total"] == 1
