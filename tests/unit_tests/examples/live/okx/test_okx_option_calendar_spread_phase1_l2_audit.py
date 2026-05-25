# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------

from collections import Counter
from decimal import Decimal
from types import SimpleNamespace

import pytest

from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
    OrderLegPlan,
)
from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
    CandidateEvalStatus,
)
from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
    DepthAuditStatus,
)
from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
    DepthLevel,
)
from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
    SelectiveL2ExecutionAuditConfig,
)
from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
    SelectiveL2ExecutionAuditStrategy,
)
from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
    _format_execution_audit_log_value,
)
from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
    audit_leg_depth,
)
from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
    build_l2_leg_audits_for_legs,
)
from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
    build_l2_leg_audits_for_opportunity,
)
from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
    build_node_components,
)
from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
    calculate_vwap,
)
from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
    depth_levels_for_side,
)
from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
    evaluate_candidate_for_l2_audit,
)
from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
    l2_leg_baskets_for_opportunities,
)
from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
    parse_args,
)
from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
    select_l2_subscription_ids,
)
from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
    validate_l2_audit_config,
)
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.test_kit.stubs.data import TestDataStubs


class FakePhase1Chain:
    def __init__(self, ts_event: int, call_quote: QuoteTick | None = None):
        self.ts_event = ts_event
        self._call_quote = call_quote

    def get_call_quote(self, strike):
        return self._call_quote

    def get_put_quote(self, strike):
        return None


def _quote(instrument_id: InstrumentId, bid: str, ask: str, ts_event: int) -> QuoteTick:
    return QuoteTick(
        instrument_id=instrument_id,
        bid_price=Price.from_str(bid),
        ask_price=Price.from_str(ask),
        bid_size=Quantity.from_str("10"),
        ask_size=Quantity.from_str("10"),
        ts_event=ts_event,
        ts_init=ts_event,
    )


def _phase1_pair():
    near_id = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    far_id = InstrumentId.from_str("BTC-USD-260525-70000-C.OKX")
    return SimpleNamespace(
        option_kind="CALL",
        strike_price=Price.from_str("70000"),
        near=SimpleNamespace(instrument_id=near_id),
        far=SimpleNamespace(instrument_id=far_id),
    )


def test_calculate_vwap_consumes_buy_levels_in_price_order():
    levels = (
        DepthLevel(price=Decimal(100), size=Decimal(1)),
        DepthLevel(price=Decimal(101), size=Decimal(2)),
    )

    result = calculate_vwap(levels, Decimal(2))

    assert result.status == DepthAuditStatus.L2_EXECUTABLE
    assert result.executable_qty == Decimal(2)
    assert result.vwap_price == Decimal("100.5")
    assert result.worst_price == Decimal(101)
    assert result.notional == Decimal(201)


def test_calculate_vwap_reports_insufficient_depth_with_partial_quantity():
    levels = (
        DepthLevel(price=Decimal(100), size=Decimal(1)),
        DepthLevel(price=Decimal(101), size=Decimal("0.5")),
    )

    result = calculate_vwap(levels, Decimal(2))

    assert result.status == DepthAuditStatus.INSUFFICIENT_DEPTH
    assert result.executable_qty == Decimal("1.5")
    assert result.vwap_price == Decimal("100.3333333333333333333333333")
    assert result.worst_price == Decimal(101)


def test_depth_levels_for_side_uses_asks_for_buy_and_bids_for_sell():
    depth = TestDataStubs.order_book_depth10(levels=3, ts_event=1_000_000_000)

    buy_levels = depth_levels_for_side(depth, OrderSide.BUY)
    sell_levels = depth_levels_for_side(depth, OrderSide.SELL)

    assert [level.price for level in buy_levels[:3]] == [
        Decimal("100.00"),
        Decimal("101.00"),
        Decimal("102.00"),
    ]
    assert [level.price for level in sell_levels[:3]] == [
        Decimal("99.00"),
        Decimal("98.00"),
        Decimal("97.00"),
    ]


def test_audit_leg_depth_classifies_missing_and_stale_books():
    missing = audit_leg_depth(
        depth=None,
        side=OrderSide.BUY,
        requested_qty=Decimal(1),
        now_ns=2_000_000_000,
        stale_ms=500,
    )
    stale = audit_leg_depth(
        depth=TestDataStubs.order_book_depth10(ts_event=1_000_000_000),
        side=OrderSide.BUY,
        requested_qty=Decimal(1),
        now_ns=2_000_000_000,
        stale_ms=500,
    )

    assert missing.status == DepthAuditStatus.MISSING_BOOK
    assert missing.book_age_ms is None
    assert stale.status == DepthAuditStatus.STALE_BOOK
    assert stale.book_age_ms == Decimal(1000)


def test_audit_leg_depth_reports_warming_up_before_missing_book():
    warming_up = audit_leg_depth(
        depth=None,
        side=OrderSide.BUY,
        requested_qty=Decimal(1),
        now_ns=2_000_000_000,
        stale_ms=500,
        subscription_age_ms=Decimal(250),
        warmup_ms=1_000,
    )

    assert warming_up.status == DepthAuditStatus.WARMING_UP
    assert warming_up.book_age_ms is None
    assert warming_up.depth_levels_seen == 0


def test_audit_leg_depth_reports_not_selected_before_missing_book():
    not_selected = audit_leg_depth(
        depth=None,
        side=OrderSide.BUY,
        requested_qty=Decimal(1),
        now_ns=2_000_000_000,
        stale_ms=500,
        selected_for_l2=False,
    )

    assert not_selected.status == DepthAuditStatus.NOT_SELECTED_FOR_L2
    assert not_selected.book_age_ms is None
    assert not_selected.depth_levels_seen == 0

def test_execution_audit_log_formatter_trims_noisy_decimals_and_expiry_ns():
    assert _format_execution_audit_log_value(
        "basket_id",
        "calendar:BTC:BTC:CALL:76000:1779868800000000000->1779955200000000000",
    ) == "calendar:BTC:BTC:CALL:76000:2026-05-27->2026-05-28"
    assert _format_execution_audit_log_value(
        "near_dte_days",
        Decimal("2.269698903958333333333333333"),
    ) == "2.27"
    assert _format_execution_audit_log_value(
        "strike_moneyness",
        "0.9870129870129870129870129870",
    ) == "0.987"
    assert _format_execution_audit_log_value(
        "quote_age_ms",
        Decimal("352705.698999"),
    ) == "352705.699"
    assert _format_execution_audit_log_value(
        "fair_value_estimate",
        Decimal("0.001750000"),
    ) == "0.00175"


def test_audit_leg_depth_returns_l2_executable_when_fresh_depth_suffices():
    depth = TestDataStubs.order_book_depth10(levels=2, ts_event=1_000_000_000)

    audit = audit_leg_depth(
        depth=depth,
        side=OrderSide.SELL,
        requested_qty=Decimal(150),
        now_ns=1_250_000_000,
        stale_ms=500,
    )

    assert audit.status == DepthAuditStatus.L2_EXECUTABLE
    assert audit.instrument_id == depth.instrument_id
    assert audit.executable_qty == Decimal(150)
    assert audit.vwap_price == Decimal("98.66666666666666666666666667")
    assert audit.book_age_ms == Decimal(250)
    assert audit.depth_levels_seen == 2


def test_select_l2_subscription_ids_prioritizes_active_legs_and_caps_candidates():
    active_near = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    active_far = InstrumentId.from_str("BTC-USD-260525-70000-C.OKX")
    eth_near = InstrumentId.from_str("ETH-USD-260524-4000-C.OKX")
    eth_far = InstrumentId.from_str("ETH-USD-260525-4000-C.OKX")
    later_near = InstrumentId.from_str("BTC-USD-260526-70000-C.OKX")

    selected = select_l2_subscription_ids(
        candidate_baskets=((eth_near, eth_far), (later_near, active_far)),
        active_leg_ids=(active_near, active_far),
        max_candidate_baskets=1,
        max_leg_subscriptions=3,
    )

    assert selected == (active_near, active_far, eth_near)


def test_select_l2_subscription_ids_can_skip_partial_candidate_baskets():
    active_near = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    active_far = InstrumentId.from_str("BTC-USD-260525-70000-C.OKX")
    eth_near = InstrumentId.from_str("ETH-USD-260524-4000-C.OKX")
    eth_far = InstrumentId.from_str("ETH-USD-260525-4000-C.OKX")
    later_near = InstrumentId.from_str("BTC-USD-260526-70000-C.OKX")
    later_far = InstrumentId.from_str("BTC-USD-260527-70000-C.OKX")

    selected = select_l2_subscription_ids(
        candidate_baskets=((eth_near, eth_far), (later_near, later_far)),
        active_leg_ids=(active_near, active_far),
        max_candidate_baskets=2,
        max_leg_subscriptions=3,
        complete_candidate_baskets_only=True,
    )

    assert selected == (active_near, active_far)


def test_select_l2_subscription_ids_complete_baskets_counts_only_missing_legs():
    active_near = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    active_far = InstrumentId.from_str("BTC-USD-260525-70000-C.OKX")
    new_far = InstrumentId.from_str("BTC-USD-260526-70000-C.OKX")

    selected = select_l2_subscription_ids(
        candidate_baskets=((active_far, new_far),),
        active_leg_ids=(active_near, active_far),
        max_candidate_baskets=1,
        max_leg_subscriptions=3,
        complete_candidate_baskets_only=True,
    )

    assert selected == (active_near, active_far, new_far)


def test_select_l2_subscription_ids_validates_caps():
    with pytest.raises(ValueError, match="max_candidate_baskets"):
        select_l2_subscription_ids(
            candidate_baskets=(),
            active_leg_ids=(),
            max_candidate_baskets=-1,
            max_leg_subscriptions=1,
        )
    with pytest.raises(ValueError, match="max_leg_subscriptions"):
        select_l2_subscription_ids(
            candidate_baskets=(),
            active_leg_ids=(),
            max_candidate_baskets=1,
            max_leg_subscriptions=-1,
        )


def test_l2_config_validation_rejects_unsupported_okx_depth():
    with pytest.raises(ValueError, match="l2_depth"):
        validate_l2_audit_config(SelectiveL2ExecutionAuditConfig(l2_depth=1))

    with pytest.raises(ValueError, match="l2_stale_ms"):
        validate_l2_audit_config(SelectiveL2ExecutionAuditConfig(l2_stale_ms=-1))

    with pytest.raises(ValueError, match="l2_warmup_ms"):
        validate_l2_audit_config(SelectiveL2ExecutionAuditConfig(l2_warmup_ms=-1))

    with pytest.raises(ValueError, match="l2_retention_ms"):
        validate_l2_audit_config(SelectiveL2ExecutionAuditConfig(l2_retention_ms=-1))
    with pytest.raises(ValueError, match="l2_min_hold_ms"):
        validate_l2_audit_config(SelectiveL2ExecutionAuditConfig(l2_min_hold_ms=-1))
    with pytest.raises(ValueError, match="l2_no_depth_retention_ms"):
        validate_l2_audit_config(SelectiveL2ExecutionAuditConfig(l2_no_depth_retention_ms=-1))
    with pytest.raises(ValueError, match="l2_retained_max_age_ms"):
        validate_l2_audit_config(SelectiveL2ExecutionAuditConfig(l2_retained_max_age_ms=-1))
    with pytest.raises(ValueError, match="max_l2_subscription_changes_per_scan"):
        validate_l2_audit_config(SelectiveL2ExecutionAuditConfig(max_l2_subscription_changes_per_scan=-1))


def test_l2_leg_baskets_for_opportunities_uses_open_leg_order():
    near = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    far = InstrumentId.from_str("BTC-USD-260525-70000-C.OKX")
    opportunity = type("Opportunity", (), {})()
    opportunity.open_legs = (
        OrderLegPlan("open_sell_near", near, OrderSide.SELL, Decimal(1), Decimal("0.10"), TimeInForce.GTC),
        OrderLegPlan("open_buy_far", far, OrderSide.BUY, Decimal(1), Decimal("0.15"), TimeInForce.GTC),
    )

    assert l2_leg_baskets_for_opportunities((opportunity,)) == ((near, far),)


def test_build_l2_leg_audits_for_opportunity_maps_depth_by_leg_id():
    near = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    far = InstrumentId.from_str("BTC-USD-260525-70000-C.OKX")
    opportunity = type("Opportunity", (), {})()
    opportunity.open_legs = (
        OrderLegPlan("open_sell_near", near, OrderSide.SELL, Decimal(150), Decimal("0.10"), TimeInForce.GTC),
        OrderLegPlan("open_buy_far", far, OrderSide.BUY, Decimal(1), Decimal("0.15"), TimeInForce.GTC),
    )

    audits = build_l2_leg_audits_for_opportunity(
        opportunity=opportunity,
        depth_by_id={
            near: TestDataStubs.order_book_depth10(
                instrument_id=near,
                levels=2,
                ts_event=1_000_000_000,
            ),
        },
        now_ns=1_250_000_000,
        stale_ms=500,
    )

    assert audits[0][0].instrument_id == near
    assert audits[0][1].status == DepthAuditStatus.L2_EXECUTABLE
    assert audits[0][1].executable_qty == Decimal(150)
    assert audits[1][0].instrument_id == far
    assert audits[1][1].status == DepthAuditStatus.MISSING_BOOK


def test_build_l2_leg_audits_for_legs_supports_close_side_exit_roles():
    near = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    far = InstrumentId.from_str("BTC-USD-260525-70000-C.OKX")
    close_legs = (
        OrderLegPlan("close_buy_near", near, OrderSide.BUY, Decimal(1), Decimal("0.10"), TimeInForce.GTC),
        OrderLegPlan("close_sell_far", far, OrderSide.SELL, Decimal(1), Decimal("0.15"), TimeInForce.GTC),
    )

    audits = build_l2_leg_audits_for_legs(
        legs=close_legs,
        depth_by_id={
            near: TestDataStubs.order_book_depth10(
                instrument_id=near,
                levels=2,
                ts_event=1_000_000_000,
            ),
            far: TestDataStubs.order_book_depth10(
                instrument_id=far,
                levels=2,
                ts_event=1_000_000_000,
            ),
        },
        now_ns=1_250_000_000,
        stale_ms=500,
    )

    assert audits[0][0].role == "close_buy_near"
    assert audits[0][1].side == OrderSide.BUY
    assert audits[0][1].status == DepthAuditStatus.L2_EXECUTABLE
    assert audits[0][1].notional == Decimal("100.00")
    assert audits[1][0].role == "close_sell_far"
    assert audits[1][1].side == OrderSide.SELL
    assert audits[1][1].status == DepthAuditStatus.L2_EXECUTABLE
    assert audits[1][1].notional == Decimal("99.00")


def test_build_l2_leg_audits_marks_recent_subscriptions_as_warming_up_and_unselected_legs():
    near = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    far = InstrumentId.from_str("BTC-USD-260525-70000-C.OKX")
    opportunity = type("Opportunity", (), {})()
    opportunity.open_legs = (
        OrderLegPlan("open_sell_near", near, OrderSide.SELL, Decimal(1), Decimal("0.10"), TimeInForce.GTC),
        OrderLegPlan("open_buy_far", far, OrderSide.BUY, Decimal(1), Decimal("0.15"), TimeInForce.GTC),
    )

    audits = build_l2_leg_audits_for_opportunity(
        opportunity=opportunity,
        depth_by_id={},
        now_ns=2_000_000_000,
        stale_ms=500,
        subscription_started_ns_by_id={near: 1_500_000_000},
        warmup_ms=1_000,
    )

    assert audits[0][1].status == DepthAuditStatus.WARMING_UP
    assert audits[1][1].status == DepthAuditStatus.NOT_SELECTED_FOR_L2


def test_retains_recent_l2_subscriptions_within_remaining_cap():
    current = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    retained = InstrumentId.from_str("BTC-USD-260525-70000-C.OKX")
    expired = InstrumentId.from_str("ETH-USD-260525-4000-C.OKX")
    strategy = SelectiveL2ExecutionAuditStrategy(
        SelectiveL2ExecutionAuditConfig(max_l2_leg_subscriptions=2, l2_retention_ms=5_000),
    )
    strategy._l2_depth_subscriptions = {retained, expired}
    strategy._l2_subscription_last_requested_ns_by_id = {
        retained: 8_000_000_000,
        expired: 1_000_000_000,
    }

    selected = strategy._retain_recent_l2_subscriptions(
        current_selected=(current,),
        now_ns=10_000_000_000,
    )

    assert selected == (current, retained)


def test_l2_min_hold_keeps_existing_subscriptions_ahead_of_new_candidates():
    sticky = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    replacement = InstrumentId.from_str("ETH-USD-260524-4000-C.OKX")
    strategy = SelectiveL2ExecutionAuditStrategy(
        SelectiveL2ExecutionAuditConfig(
            max_l2_leg_subscriptions=1,
            l2_retention_ms=0,
            l2_min_hold_ms=30_000,
        ),
    )
    strategy._l2_depth_subscriptions = {sticky}
    strategy._l2_subscription_started_ns_by_id = {sticky: 9_000_000_000}

    selected = strategy._retain_recent_l2_subscriptions(
        current_selected=(replacement,),
        now_ns=10_000_000_000,
    )

    assert selected == (sticky,)


def test_l2_prefer_current_candidates_places_current_selection_before_sticky_hold():
    sticky = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    current = InstrumentId.from_str("ETH-USD-260524-4000-C.OKX")
    strategy = SelectiveL2ExecutionAuditStrategy(
        SelectiveL2ExecutionAuditConfig(
            max_l2_leg_subscriptions=1,
            l2_retention_ms=0,
            l2_min_hold_ms=30_000,
            l2_prefer_current_candidates=True,
        ),
    )
    strategy._l2_depth_subscriptions = {sticky}
    strategy._l2_subscription_started_ns_by_id = {sticky: 9_000_000_000}

    selected = strategy._retain_recent_l2_subscriptions(
        current_selected=(current,),
        now_ns=10_000_000_000,
    )

    assert selected == (current,)


def test_l2_no_depth_retention_timeout_drops_only_retained_no_depth_subscription():
    current = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    no_depth = InstrumentId.from_str("BTC-USD-260525-70000-C.OKX")
    has_depth = InstrumentId.from_str("ETH-USD-260524-4000-C.OKX")
    strategy = SelectiveL2ExecutionAuditStrategy(
        SelectiveL2ExecutionAuditConfig(
            max_l2_leg_subscriptions=3,
            l2_retention_ms=60_000,
            l2_no_depth_retention_ms=10_000,
        ),
    )
    strategy._l2_depth_subscriptions = {no_depth, has_depth}
    strategy._l2_subscription_started_ns_by_id = {
        no_depth: 1_000_000_000,
        has_depth: 1_000_000_000,
    }
    strategy._l2_subscription_last_requested_ns_by_id = {
        no_depth: 1_000_000_000,
        has_depth: 1_000_000_000,
    }
    strategy._l2_depth_first_received_ns_by_id = {
        has_depth: 2_000_000_000,
    }

    selected = strategy._retain_recent_l2_subscriptions(
        current_selected=(current,),
        now_ns=12_000_000_001,
    )

    assert selected == (current, has_depth)


def test_l2_no_depth_retention_timeout_does_not_drop_current_or_active_leg():
    active = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    current = InstrumentId.from_str("ETH-USD-260524-4000-C.OKX")
    strategy = SelectiveL2ExecutionAuditStrategy(
        SelectiveL2ExecutionAuditConfig(
            max_l2_leg_subscriptions=2,
            l2_retention_ms=60_000,
            l2_no_depth_retention_ms=10_000,
        ),
    )
    strategy._l2_depth_subscriptions = {active, current}
    strategy._l2_subscription_started_ns_by_id = {
        active: 1_000_000_000,
        current: 1_000_000_000,
    }
    strategy._l2_subscription_last_requested_ns_by_id = {
        active: 1_000_000_000,
        current: 1_000_000_000,
    }

    selected = strategy._retain_recent_l2_subscriptions(
        current_selected=(current, active),
        active_leg_ids=(active,),
        now_ns=12_000_000_001,
    )

    assert selected == (active, current)


def test_l2_retained_max_age_drops_only_retained_old_subscription():
    current = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    old_retained = InstrumentId.from_str("BTC-USD-260525-70000-C.OKX")
    fresh_retained = InstrumentId.from_str("ETH-USD-260524-4000-C.OKX")
    strategy = SelectiveL2ExecutionAuditStrategy(
        SelectiveL2ExecutionAuditConfig(
            max_l2_leg_subscriptions=3,
            l2_retention_ms=60_000,
            l2_retained_max_age_ms=30_000,
        ),
    )
    strategy._l2_depth_subscriptions = {old_retained, fresh_retained}
    strategy._l2_subscription_started_ns_by_id = {
        old_retained: 1_000_000_000,
        fresh_retained: 20_000_000_000,
    }
    strategy._l2_subscription_last_requested_ns_by_id = {
        old_retained: 25_000_000_000,
        fresh_retained: 25_000_000_000,
    }

    selected = strategy._retain_recent_l2_subscriptions(
        current_selected=(current,),
        now_ns=40_000_000_001,
    )

    assert selected == (current, fresh_retained)


def test_l2_retained_max_age_does_not_drop_current_or_active_leg():
    active = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    current = InstrumentId.from_str("ETH-USD-260524-4000-C.OKX")
    strategy = SelectiveL2ExecutionAuditStrategy(
        SelectiveL2ExecutionAuditConfig(
            max_l2_leg_subscriptions=2,
            l2_retention_ms=60_000,
            l2_retained_max_age_ms=30_000,
        ),
    )
    strategy._l2_depth_subscriptions = {active, current}
    strategy._l2_subscription_started_ns_by_id = {
        active: 1_000_000_000,
        current: 1_000_000_000,
    }
    strategy._l2_subscription_last_requested_ns_by_id = {
        active: 39_000_000_000,
        current: 39_000_000_000,
    }

    selected = strategy._retain_recent_l2_subscriptions(
        current_selected=(current, active),
        active_leg_ids=(active,),
        now_ns=40_000_000_001,
    )

    assert selected == (active, current)


def test_l2_min_hold_allows_expired_subscription_rotation():
    expired = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    replacement = InstrumentId.from_str("ETH-USD-260524-4000-C.OKX")
    strategy = SelectiveL2ExecutionAuditStrategy(
        SelectiveL2ExecutionAuditConfig(
            max_l2_leg_subscriptions=1,
            l2_retention_ms=0,
            l2_min_hold_ms=30_000,
        ),
    )
    strategy._l2_depth_subscriptions = {expired}
    strategy._l2_subscription_started_ns_by_id = {expired: 1_000_000_000}

    selected = strategy._retain_recent_l2_subscriptions(
        current_selected=(replacement,),
        now_ns=40_000_000_001,
    )

    assert selected == (replacement,)


def test_l2_min_hold_does_not_block_active_legs():
    sticky = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    active = InstrumentId.from_str("ETH-USD-260524-4000-C.OKX")
    strategy = SelectiveL2ExecutionAuditStrategy(
        SelectiveL2ExecutionAuditConfig(
            max_l2_leg_subscriptions=1,
            l2_retention_ms=0,
            l2_min_hold_ms=30_000,
        ),
    )
    strategy._l2_depth_subscriptions = {sticky}
    strategy._l2_subscription_started_ns_by_id = {sticky: 9_000_000_000}

    selected = strategy._retain_recent_l2_subscriptions(
        current_selected=(active,),
        active_leg_ids=(active,),
        now_ns=10_000_000_000,
    )

    assert selected == (active,)


def test_l2_subscription_change_budget_throttles_normal_unsubscribes_but_force_clears_all():
    first = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    second = InstrumentId.from_str("BTC-USD-260525-70000-C.OKX")
    strategy = SelectiveL2ExecutionAuditStrategy(
        SelectiveL2ExecutionAuditConfig(max_l2_subscription_changes_per_scan=1),
    )
    strategy._l2_depth_subscriptions = {first, second}
    strategy._l2_subscription_started_ns_by_id = {first: 1, second: 1}
    strategy._l2_subscription_last_requested_ns_by_id = {first: 1, second: 1}
    unsubscribed = []
    strategy.unsubscribe_order_book_depth = lambda **kwargs: unsubscribed.append(kwargs["instrument_id"])

    strategy._apply_l2_depth_subscription_set(set())

    assert len(unsubscribed) == 1
    assert len(strategy._l2_depth_subscriptions) == 1

    strategy._apply_l2_depth_subscription_set(set(), force=True)

    assert set(unsubscribed) == {first, second}
    assert strategy._l2_depth_subscriptions == set()


def test_l2_unsubscribe_bookkeeping_tolerates_reentrant_subscription_mutation():
    instrument_id = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    strategy = SelectiveL2ExecutionAuditStrategy(SelectiveL2ExecutionAuditConfig())
    strategy._l2_depth_subscriptions = {instrument_id}
    strategy._l2_subscription_started_ns_by_id = {instrument_id: 1}
    strategy._l2_subscription_last_requested_ns_by_id = {instrument_id: 1}
    strategy._l2_depth_first_received_ns_by_id = {instrument_id: 2}
    strategy._l2_depth_last_received_ns_by_id = {instrument_id: 3}
    strategy._l2_depth_update_count_by_id[instrument_id] = 1

    def unsubscribe_order_book_depth(**kwargs):
        assert kwargs["instrument_id"] == instrument_id
        strategy._l2_depth_subscriptions.discard(instrument_id)

    strategy.unsubscribe_order_book_depth = unsubscribe_order_book_depth

    strategy._apply_l2_depth_subscription_set(set())

    assert strategy._l2_depth_subscriptions == set()
    assert strategy._l2_subscription_started_ns_by_id == {}
    assert strategy._l2_subscription_last_requested_ns_by_id == {}
    assert strategy._l2_depth_first_received_ns_by_id == {}
    assert strategy._l2_depth_last_received_ns_by_id == {}
    assert strategy._l2_depth_update_count_by_id == Counter()


def test_order_book_depth_delivery_metrics_track_first_last_and_count():
    strategy = SelectiveL2ExecutionAuditStrategy(SelectiveL2ExecutionAuditConfig())
    depth = TestDataStubs.order_book_depth10(ts_event=1_000_000_000)
    instrument_id = depth.instrument_id
    strategy._l2_depth_subscriptions = {instrument_id}
    timestamps = iter((10_000_000_000, 10_250_000_000))
    strategy._timestamp_ns = lambda: next(timestamps)

    strategy.on_order_book_depth(depth)
    strategy.on_order_book_depth(depth)

    assert strategy._latest_l2_depths[instrument_id] is depth
    assert strategy._l2_depth_first_received_ns_by_id[instrument_id] == 10_000_000_000
    assert strategy._l2_depth_last_received_ns_by_id[instrument_id] == 10_250_000_000
    assert strategy._l2_depth_update_count_by_id[instrument_id] == 2


def test_l2_subscribe_bookkeeping_blocks_reentrant_cap_overshoot():
    existing_a = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    existing_b = InstrumentId.from_str("BTC-USD-260525-70000-C.OKX")
    existing_c = InstrumentId.from_str("BTC-USD-260526-70000-C.OKX")
    new_a = InstrumentId.from_str("ETH-USD-260524-4000-C.OKX")
    new_b = InstrumentId.from_str("ETH-USD-260525-4000-C.OKX")
    selected = {existing_a, existing_b, existing_c, new_a, new_b}
    strategy = SelectiveL2ExecutionAuditStrategy(
        SelectiveL2ExecutionAuditConfig(max_l2_leg_subscriptions=4),
    )
    strategy._l2_depth_subscriptions = {existing_a, existing_b, existing_c}
    strategy._l2_subscription_started_ns_by_id = {existing_a: 1, existing_b: 1, existing_c: 1}
    strategy._l2_subscription_last_requested_ns_by_id = {existing_a: 1, existing_b: 1, existing_c: 1}
    strategy._timestamp_ns = lambda: 10
    subscribed = []

    def subscribe_order_book_depth(**kwargs):
        subscribed.append(kwargs["instrument_id"])
        if len(subscribed) == 1:
            strategy._apply_l2_depth_subscription_set(selected)

    strategy.subscribe_order_book_depth = subscribe_order_book_depth

    strategy._apply_l2_depth_subscription_set(selected)

    assert len(subscribed) == 1
    assert len(strategy._l2_depth_subscriptions) == 4


def test_remembers_only_fully_selected_l2_audit_opportunities_and_expires_them():
    selected_near = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    selected_far = InstrumentId.from_str("BTC-USD-260525-70000-C.OKX")
    partial_far = InstrumentId.from_str("BTC-USD-260526-70000-C.OKX")
    remembered = SimpleNamespace(
        basket_id="remembered",
        open_legs=(
            OrderLegPlan("open_sell_near", selected_near, OrderSide.SELL, Decimal(1), Decimal("0.10"), TimeInForce.GTC),
            OrderLegPlan("open_buy_far", selected_far, OrderSide.BUY, Decimal(1), Decimal("0.15"), TimeInForce.GTC),
        ),
    )
    partial = SimpleNamespace(
        basket_id="partial",
        open_legs=(
            OrderLegPlan("open_sell_near", selected_near, OrderSide.SELL, Decimal(1), Decimal("0.10"), TimeInForce.GTC),
            OrderLegPlan("open_buy_far", partial_far, OrderSide.BUY, Decimal(1), Decimal("0.15"), TimeInForce.GTC),
        ),
    )
    strategy = SelectiveL2ExecutionAuditStrategy(
        SelectiveL2ExecutionAuditConfig(max_l2_leg_subscriptions=2, l2_retention_ms=5_000),
    )
    strategy._basket_id = lambda opportunity: opportunity.basket_id

    strategy._remember_l2_audit_opportunities(
        opportunities=(remembered, partial),
        current_selected=(selected_near, selected_far),
        now_ns=10_000_000_000,
    )

    assert tuple(strategy._l2_audit_opportunities_by_basket_id) == ("remembered",)

    strategy._expire_retained_l2_audit_opportunities(now_ns=16_000_000_001)

    assert strategy._l2_audit_opportunities_by_basket_id == {}
    assert strategy._l2_audit_opportunity_last_requested_ns_by_basket_id == {}


def test_emit_retained_l2_audits_skips_current_candidates_and_reaudits_retained_candidates():
    near = InstrumentId.from_str("BTC-USD-260524-70000-C.OKX")
    far = InstrumentId.from_str("BTC-USD-260525-70000-C.OKX")
    current = SimpleNamespace(
        basket_id="current",
        open_legs=(
            OrderLegPlan("open_sell_near", near, OrderSide.SELL, Decimal(1), Decimal("0.10"), TimeInForce.GTC),
            OrderLegPlan("open_buy_far", far, OrderSide.BUY, Decimal(1), Decimal("0.15"), TimeInForce.GTC),
        ),
    )
    retained = SimpleNamespace(
        basket_id="retained",
        open_legs=(
            OrderLegPlan("open_sell_near", near, OrderSide.SELL, Decimal(1), Decimal("0.10"), TimeInForce.GTC),
            OrderLegPlan("open_buy_far", far, OrderSide.BUY, Decimal(1), Decimal("0.15"), TimeInForce.GTC),
        ),
    )
    emitted = []
    strategy = SelectiveL2ExecutionAuditStrategy(SelectiveL2ExecutionAuditConfig())
    strategy._basket_id = lambda opportunity: opportunity.basket_id
    strategy._log_execution_audit = lambda opportunity, *, audit_source: emitted.append(
        (opportunity.basket_id, audit_source),
    )
    strategy._l2_subscription_started_ns_by_id = {near: 1, far: 1}
    strategy._l2_audit_opportunities_by_basket_id = {
        "current": current,
        "retained": retained,
    }

    strategy._emit_retained_l2_audits({"current"})

    assert emitted == [("retained", "retained")]


def test_l2_retention_default_covers_warmup_and_candidate_audit_interval():
    config = SelectiveL2ExecutionAuditConfig()

    assert config.l2_retention_ms >= (
        config.l2_warmup_ms + config.candidate_log_interval_secs * 1_000
    )


def test_evaluate_candidate_for_l2_audit_reports_rejection_reasons():
    pair = _phase1_pair()
    now_ns = 1_000_000_000_000

    missing_chain = evaluate_candidate_for_l2_audit(
        pair=pair,
        near_chain=None,
        far_chain=FakePhase1Chain(now_ns),
        now_ns=now_ns,
        stale_quote_ms=5_000,
        max_cross_series_skew_ms=1_000,
        order_qty=Decimal(1),
        time_in_force=TimeInForce.GTC,
    )
    stale_chain = evaluate_candidate_for_l2_audit(
        pair=pair,
        near_chain=FakePhase1Chain(now_ns - 6_000_000_000),
        far_chain=FakePhase1Chain(now_ns),
        now_ns=now_ns,
        stale_quote_ms=5_000,
        max_cross_series_skew_ms=10_000,
        order_qty=Decimal(1),
        time_in_force=TimeInForce.GTC,
    )
    missing_quote = evaluate_candidate_for_l2_audit(
        pair=pair,
        near_chain=FakePhase1Chain(now_ns),
        far_chain=FakePhase1Chain(now_ns),
        now_ns=now_ns,
        stale_quote_ms=5_000,
        max_cross_series_skew_ms=1_000,
        order_qty=Decimal(1),
        time_in_force=TimeInForce.GTC,
    )

    assert missing_chain.status == CandidateEvalStatus.MISSING_CHAIN
    assert missing_chain.max_chain_age_ms is None
    assert missing_chain.cross_series_skew_ms is None
    assert stale_chain.status == CandidateEvalStatus.STALE_CHAIN
    assert stale_chain.max_chain_age_ms == 6_000
    assert stale_chain.cross_series_skew_ms == 6_000
    assert missing_quote.status == CandidateEvalStatus.MISSING_QUOTE
    assert missing_quote.max_chain_age_ms == 0
    assert missing_quote.cross_series_skew_ms == 0


def test_evaluate_candidate_for_l2_audit_reports_cross_series_skew_metric():
    pair = _phase1_pair()
    now_ns = 1_000_000_000_000

    result = evaluate_candidate_for_l2_audit(
        pair=pair,
        near_chain=FakePhase1Chain(now_ns),
        far_chain=FakePhase1Chain(now_ns - 2_500_000_000),
        now_ns=now_ns,
        stale_quote_ms=5_000,
        max_cross_series_skew_ms=1_000,
        order_qty=Decimal(1),
        time_in_force=TimeInForce.GTC,
    )

    assert result.status == CandidateEvalStatus.CROSS_SERIES_SKEW
    assert result.max_chain_age_ms == 2_500
    assert result.cross_series_skew_ms == 2_500


def test_evaluate_candidate_for_l2_audit_returns_opportunity_for_executable_l1_quotes():
    pair = _phase1_pair()
    now_ns = 1_000_000_000_000

    result = evaluate_candidate_for_l2_audit(
        pair=pair,
        near_chain=FakePhase1Chain(
            now_ns,
            _quote(pair.near.instrument_id, "0.10", "0.11", now_ns),
        ),
        far_chain=FakePhase1Chain(
            now_ns,
            _quote(pair.far.instrument_id, "0.14", "0.15", now_ns),
        ),
        now_ns=now_ns,
        stale_quote_ms=5_000,
        max_cross_series_skew_ms=1_000,
        order_qty=Decimal(1),
        time_in_force=TimeInForce.GTC,
    )

    assert result.status == CandidateEvalStatus.OPPORTUNITY
    assert result.opportunity is not None
    assert result.opportunity.open_long_cost == Decimal("0.05")


def test_phase1_build_node_components_preserves_phase0_config_and_adds_l2_controls():
    args = parse_args(
        [
            "--underlyings",
            "BTC",
            "--max-opportunities-per-scan",
            "2",
            "--max-l2-candidate-baskets",
            "4",
            "--max-l2-leg-subscriptions",
            "6",
            "--l2-depth",
            "10",
            "--l2-stale-ms",
            "250",
            "--l2-warmup-ms",
            "750",
            "--l2-retention-ms",
            "3000",
            "--l2-min-hold-ms",
            "30000",
            "--l2-no-depth-retention-ms",
            "10000",
            "--l2-retained-max-age-ms",
            "30000",
            "--l2-prefer-current-candidates",
            "--l2-complete-candidate-baskets-only",
            "--max-l2-subscription-changes-per-scan",
            "4",
            "--timeout-connection-seconds",
            "300",
        ],
    )

    node_config, strategy_config = build_node_components(args)

    assert node_config.timeout_connection == 300
    assert isinstance(strategy_config, SelectiveL2ExecutionAuditConfig)
    assert strategy_config.underlyings == ("BTC",)
    assert strategy_config.max_opportunities_per_scan == 2
    assert strategy_config.max_l2_candidate_baskets == 4
    assert strategy_config.max_l2_leg_subscriptions == 6
    assert strategy_config.l2_depth == 10
    assert strategy_config.l2_stale_ms == 250
    assert strategy_config.l2_warmup_ms == 750
    assert strategy_config.l2_retention_ms == 3000
    assert strategy_config.l2_min_hold_ms == 30000
    assert strategy_config.l2_no_depth_retention_ms == 10000
    assert strategy_config.l2_retained_max_age_ms == 30000
    assert strategy_config.l2_prefer_current_candidates is True
    assert strategy_config.l2_complete_candidate_baskets_only is True
    assert strategy_config.max_l2_subscription_changes_per_scan == 4
