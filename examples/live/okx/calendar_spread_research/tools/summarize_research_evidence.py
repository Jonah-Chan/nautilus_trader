#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------
"""
Summarize calendar-spread research evidence across Phase 0, Phase 1, and Phase 2.

This report is intentionally offline. It does not read exchange credentials,
does not start Nautilus, and does not submit orders. Its purpose is to preserve
the current evidence-backed answers to the recurring review questions:

- whether the strategy is BTC-only or market-window/configuration dependent,
- why the Phase 0 lifecycle sample loses money,
- what sandbox fill prices represent,
- whether Phase 2 currently has executable positive-edge evidence.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any


def _decimal(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def _sum_decimal_map(values: dict[str, Any] | None) -> Decimal:
    total = Decimal(0)
    for raw in (values or {}).values():
        value = _decimal(raw)
        if value is not None:
            total += value
    return total


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts = Counter(str(item.get(key) or "unknown") for item in items)
    return dict(sorted(counts.items()))


def _phase0_summary(phase0_summary: dict[str, Any], phase0_cycles: list[dict[str, Any]]) -> dict[str, Any]:
    realized_signs = Counter()
    entry_exit = Counter()
    fill_sources = Counter()

    for cycle in phase0_cycles:
        realized = _sum_decimal_map(cycle.get("realized_pnl_by_currency"))
        if realized < 0:
            realized_signs["negative"] += 1
        elif realized > 0:
            realized_signs["positive"] += 1
        else:
            realized_signs["zero"] += 1

        entry = _decimal((cycle.get("candidate") or {}).get("entry_executable_cost"))
        exit_value = _decimal((cycle.get("close_observability") or {}).get("exit_executable_value"))
        if entry is not None and exit_value is not None:
            if entry > exit_value:
                entry_exit["entry_cost_gt_exit_value"] += 1
            elif entry < exit_value:
                entry_exit["entry_cost_lt_exit_value"] += 1
            else:
                entry_exit["entry_cost_eq_exit_value"] += 1

        fill_sources[str((cycle.get("candidate") or {}).get("simulated_fill_price_source") or "unknown")] += 1

    return {
        "artifact_status": "accepted_flow_validation_sample",
        "cycles": {
            "total": len(phase0_cycles),
            "completed": phase0_summary.get("completed_cycles"),
            "open_or_unclosed": phase0_summary.get("open_or_unclosed_cycles"),
            "close_only": phase0_summary.get("close_only_cycles"),
            "by_underlying": phase0_summary.get("cycles_by_underlying") or _count_by(phase0_cycles, "underlying"),
        },
        "realized_pnl_cycle_sign_counts": dict(sorted(realized_signs.items())),
        "entry_exit_executable_counts": dict(sorted(entry_exit.items())),
        "fees_by_currency": phase0_summary.get("fees_by_currency") or {},
        "position_closed_realized_pnl_by_currency": (
            phase0_summary.get("position_closed_realized_pnl_by_currency") or {}
        ),
        "fill_price_source_counts": dict(sorted(fill_sources.items())),
        "interpretation": (
            "Phase 0 is a mechanical flow-validation harness: it crosses L1 bid/ask to open, "
            "closes after the lifecycle timer, and pays sandbox taker fees. Negative cycles here "
            "validate cost visibility; they do not prove Phase 2 signal edge is absent."
        ),
    }


def _phase1_summary(phase1: dict[str, Any]) -> dict[str, Any]:
    summary = phase1.get("summary") or {}
    calibration = summary.get("phase1_policy_calibration") or {}
    candidates_by_underlying = summary.get("candidates_by_underlying") or {}
    selected_policy_counts = summary.get("selected_audit_event_execution_policy_counts") or (
        summary.get("selected_execution_policy_counts") or {}
    )
    event_gate = summary.get("event_gate") or {}

    return {
        "artifact_status": calibration.get("verdict") or "unknown",
        "next_action": calibration.get("next_action"),
        "candidates_by_underlying": candidates_by_underlying,
        "audit_rows_by_underlying": summary.get("audit_rows_by_underlying") or {},
        "audit_events": summary.get("audit_events"),
        "audit_event_execution_policy_counts": summary.get("audit_event_execution_policy_counts") or {},
        "selected_execution_policy_counts": selected_policy_counts,
        "event_gate": {
            "full_fresh_l2_executable_ratio": event_gate.get("full_fresh_l2_executable_ratio"),
            "non_cap_full_fresh_l2_executable_ratio": event_gate.get(
                "non_cap_full_fresh_l2_executable_ratio",
            ),
            "cap_blocked_event_ratio": event_gate.get("cap_blocked_event_ratio"),
            "selected_delayed_depth_warning_ratio": event_gate.get(
                "selected_delayed_depth_warning_ratio",
            ),
        },
        "l2_load": {
            "subscribe_count": summary.get("subscribe_count"),
            "unsubscribe_count": summary.get("unsubscribe_count"),
            "max_active_l2_subscriptions": summary.get("max_active_l2_subscriptions"),
            "final_active_l2_subscriptions": summary.get("final_active_l2_subscriptions"),
            "http_50011_errors": summary.get("http_50011_errors"),
            "errors_total": summary.get("errors_total"),
            "warnings_total": summary.get("warnings_total"),
        },
        "sandbox_fill_rows": summary.get("sandbox_fill_rows"),
        "sandbox_fill_quality_boundary": summary.get("sandbox_fill_quality_boundary"),
        "blockers": calibration.get("blockers") or [],
        "interpretation": (
            "The latest Phase 1 sample is data-only and operationally clean, but not sandbox-execution "
            "ready: complete fresh two-leg coverage is low and delayed-depth warning events remain high."
        ),
    }


def _phase2_summary(phase2: dict[str, Any]) -> dict[str, Any]:
    summary = phase2.get("summary") or {}
    fair_value_status = summary.get("fair_value_estimate_status_counts") or {}
    fair_value_sources = summary.get("fair_value_estimate_source_counts") or {}
    has_fair_value_proxy = any(
        "proxy" in str(source) or "not_edge_model" in str(source)
        for source in fair_value_sources
    )
    has_fair_value_estimates = int(fair_value_status.get("present") or 0) > 0
    if has_fair_value_proxy:
        interpretation = (
            "Phase 2 currently has shadow inputs, L2-derived entry costs for fresh events, "
            "live fair-value context, and L1-mid calendar proxy estimates. The proxy is "
            "explicitly not a production fair-value/edge model, so the artifact can record "
            "negative/false-positive samples but makes no positive-edge claim."
        )
    elif has_fair_value_estimates:
        interpretation = (
            "Phase 2 currently has shadow inputs, L2-derived entry costs for fresh events, "
            "and explicit fair-value estimates, but it remains shadow-only until posterior "
            "coverage and bucket evidence support an executable positive-edge claim."
        )
    else:
        interpretation = (
            "Phase 2 currently has shadow inputs, L2-derived entry costs for fresh events, "
            "and optional posterior windows, but no fair-value estimates in the current "
            "artifact; therefore it makes no positive-edge claim."
        )
    return {
        "artifact_status": summary.get("phase2_mode"),
        "source_phase1_verdict": summary.get("source_phase1_verdict"),
        "events_total": summary.get("events_total"),
        "shadow_input_events": summary.get("shadow_input_events"),
        "blocked_events": summary.get("blocked_events"),
        "shadow_signal_tier_counts": summary.get("shadow_signal_tier_counts") or {},
        "signal_stage_counts": summary.get("signal_stage_counts") or {},
        "shadow_signal_tiers_by_underlying": summary.get("shadow_signal_tiers_by_underlying") or {},
        "entry_executable_cost_status_counts": summary.get("entry_executable_cost_status_counts") or {},
        "fair_value_estimate_status_counts": fair_value_status,
        "fair_value_estimate_source_counts": fair_value_sources,
        "cost_model_status_counts": summary.get("cost_model_status_counts") or {},
        "posterior_window_status_counts": summary.get("posterior_window_status_counts") or {},
        "posterior_source_counts": summary.get("posterior_source_counts") or {},
        "static_cost_policy": summary.get("static_cost_policy") or {},
        "edge_model_status": summary.get("edge_model_status"),
        "interpretation": interpretation,
    }


def build_research_evidence_report(
    *,
    phase0_summary: dict[str, Any],
    phase0_cycles: list[dict[str, Any]],
    phase1_analysis: dict[str, Any],
    phase2_analysis: dict[str, Any],
) -> dict[str, Any]:
    phase0 = _phase0_summary(phase0_summary, phase0_cycles)
    phase1 = _phase1_summary(phase1_analysis)
    phase2 = _phase2_summary(phase2_analysis)

    return {
        "report_type": "okx_calendar_spread_research_evidence",
        "answers": {
            "is_strategy_btc_only": (
                "No. The accepted Phase 0 flow-validation sample completed both BTC and ETH cycles. "
                "BTC-heavy Phase 1/2 artifacts reflect market-window, candidate ranking, and selective "
                "L2-cap pressure, not a BTC-only strategy contract."
            ),
            "why_all_loss": (
                "The accepted Phase 0 sample is expected to lose because it mechanically opens at "
                "executable L1 bid/ask, closes after a timer, and pays sandbox taker fees. It is a "
                "lifecycle/cost-visibility harness, not a positive-edge signal."
            ),
            "sandbox_execution_price_source": (
                "Phase 0 sandbox fills are Nautilus sandbox OrderFilled events generated from strategy "
                "limit orders priced from L1 bid/ask. They are not OKX exchange fills and not L2 VWAP "
                "fill-quality evidence. Phase 1 currently has data-only L2 VWAP audit rows with zero "
                "sandbox fills."
            ),
            "docs_code_alignment": (
                "The current code/artifacts align with the documented boundary: Phase 1 remains "
                "data-only until delayed-depth and cap-blocking are calibrated; Phase 2 remains "
                "shadow-only/no-order and requires production fair-value/bucket evidence before "
                "edge claims. L1-mid proxy context is diagnostic, not a production edge model."
            ),
        },
        "phase0": phase0,
        "phase1": phase1,
        "phase2": phase2,
    }


def render_markdown(report: dict[str, Any]) -> str:
    phase0 = report["phase0"]
    phase1 = report["phase1"]
    phase2 = report["phase2"]
    answers = report["answers"]
    return "\n".join(
        [
            "# OKX 日历价差研究证据",
            "",
            "## 关键回答",
            "",
            f"- 是否只做 BTC:{answers['is_strategy_btc_only']}",
            f"- 为什么全是亏损样本:{answers['why_all_loss']}",
            f"- sandbox 价格来源:{answers['sandbox_execution_price_source']}",
            f"- 文档和代码是否对齐:{answers['docs_code_alignment']}",
            "",
            "## 证据",
            "",
            f"- Phase 0 按标的统计的 cycle:`{phase0['cycles']['by_underlying']}`.",
            f"- Phase 0 realized PnL 符号统计:`{phase0['realized_pnl_cycle_sign_counts']}`.",
            f"- Phase 0 entry/exit executable 对比:`{phase0['entry_exit_executable_counts']}`.",
            f"- Phase 0 fill price source:`{phase0['fill_price_source_counts']}`.",
            f"- Phase 1 按标的统计的 candidates:`{phase1['candidates_by_underlying']}`.",
            f"- Phase 1 selected execution policies:`{phase1['selected_execution_policy_counts']}`.",
            f"- Phase 1 event gate:`{phase1['event_gate']}`.",
            f"- Phase 1 L2 load:`{phase1['l2_load']}`.",
            f"- Phase 2 按标的统计的 shadow tiers:`{phase2['shadow_signal_tiers_by_underlying']}`.",
            f"- Phase 2 signal stages:`{phase2['signal_stage_counts']}`.",
            f"- Phase 2 fair-value status:`{phase2['fair_value_estimate_status_counts']}`.",
            f"- Phase 2 fair-value sources:`{phase2['fair_value_estimate_source_counts']}`.",
            f"- Phase 2 posterior windows:`{phase2['posterior_window_status_counts']}`.",
            "",
            "## 当前边界",
            "",
            (
                "Phase 1 在完整 fresh 两腿 L2 coverage 与 delayed-depth ratio 达到 "
                "sandbox-sample policy 前保持 data-only.Phase 2 在生产级 fair-value/bucket "
                "证据和更长 posterior window 可用前保持 shadow-only."
            ),
            "",
        ],
    )


def write_report(report: dict[str, Any], output_json: Path, output_markdown: Path | None) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if output_markdown is not None:
        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        output_markdown.write_text(render_markdown(report), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase0-summary", type=Path, required=True)
    parser.add_argument("--phase0-cycles", type=Path, required=True)
    parser.add_argument("--phase1-artifact", type=Path, required=True)
    parser.add_argument("--phase2-artifact", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, default=None)
    args = parser.parse_args()

    report = build_research_evidence_report(
        phase0_summary=json.loads(args.phase0_summary.read_text(encoding="utf-8")),
        phase0_cycles=json.loads(args.phase0_cycles.read_text(encoding="utf-8")),
        phase1_analysis=json.loads(args.phase1_artifact.read_text(encoding="utf-8")),
        phase2_analysis=json.loads(args.phase2_artifact.read_text(encoding="utf-8")),
    )
    write_report(report, args.output_json, args.output_markdown)
    print(json.dumps({"output_json": str(args.output_json), "summary": report["answers"]}, indent=2))


if __name__ == "__main__":
    main()
