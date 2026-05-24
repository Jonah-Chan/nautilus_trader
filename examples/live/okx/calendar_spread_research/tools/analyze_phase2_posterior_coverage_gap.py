#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------
"""
审计 Phase 2 posterior coverage 缺口.

本工具只读取已有 posterior outcomes 和可选的 fair-value robustness 产物.它不会
连接 OKX、启动 Nautilus、执行 sandbox 或提交订单.目标是把 30m/1h posterior
缺口从笼统的 "missing" 拆成可复核的 gate 证据, 并说明它是否足以支持 Phase 3.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any


LONG_WINDOWS = {"30m", "1h"}


def _decimal(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def _fmt_ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _sign(value: Decimal) -> str:
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "zero"


def _window_names(posterior: dict[str, Any]) -> list[str]:
    summary_windows = (posterior.get("summary") or {}).get("windows")
    if isinstance(summary_windows, list) and all(isinstance(item, str) for item in summary_windows):
        return summary_windows
    observed: set[str] = set()
    for outcome in (posterior.get("posterior_outcomes") or {}).values():
        if isinstance(outcome, dict):
            observed.update(str(window) for window in (outcome.get("windows") or {}))
    return sorted(observed)


def _robustness_summary(robustness: dict[str, Any] | None) -> dict[str, Any]:
    if not robustness:
        return {
            "provided": False,
            "phase2_gate_interpretation": None,
            "base_positive_event_count": None,
            "fragile_positive_event_count": None,
            "all_scenarios_without_stable_positive_bucket": None,
        }
    summary = robustness.get("summary") or {}
    return {
        "provided": True,
        "phase2_gate_interpretation": summary.get("phase2_gate_interpretation"),
        "base_positive_event_count": summary.get("base_positive_event_count"),
        "fragile_positive_event_count": summary.get("fragile_positive_event_count"),
        "all_scenarios_without_stable_positive_bucket": (
            summary.get("all_scenarios_without_stable_positive_bucket")
        ),
    }


def build_posterior_coverage_gap_report(  # noqa: C901
    posterior: dict[str, Any],
    *,
    robustness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    outcomes = posterior.get("posterior_outcomes") or {}
    windows = _window_names(posterior)
    entries = len(outcomes)
    status_by_window: dict[str, Counter[str]] = {window: Counter() for window in windows}
    missing_by_window: dict[str, Counter[str]] = {window: Counter() for window in windows}
    observed_edge_signs: dict[str, Counter[str]] = {window: Counter() for window in windows}
    observed_edge_values: dict[str, list[Decimal]] = {window: [] for window in windows}
    tier_by_window: dict[str, Counter[str]] = {window: Counter() for window in windows}

    for outcome in outcomes.values():
        if not isinstance(outcome, dict):
            continue
        tier = str((outcome.get("event_ref") or {}).get("shadow_signal_tier") or "unknown")
        for window, payload in (outcome.get("windows") or {}).items():
            if window not in status_by_window or not isinstance(payload, dict):
                continue
            status = str(payload.get("status") or "unknown")
            status_by_window[window][status] += 1
            if status == "missing":
                missing_by_window[window][str(payload.get("reason") or "unknown")] += 1
            if status == "observed":
                tier_by_window[window][tier] += 1
                value = _decimal(payload.get("observed_edge_after_costs"))
                if value is not None:
                    observed_edge_signs[window][_sign(value)] += 1
                    observed_edge_values[window].append(value)

    window_reports: list[dict[str, Any]] = []
    fully_missing_windows: list[str] = []
    for window in windows:
        observed = status_by_window[window].get("observed", 0)
        missing = status_by_window[window].get("missing", 0)
        total = sum(status_by_window[window].values())
        if total and missing == total:
            fully_missing_windows.append(window)
        values = observed_edge_values[window]
        window_reports.append(
            {
                "window": window,
                "total": total,
                "observed": observed,
                "missing": missing,
                "coverage_ratio": _fmt_ratio(observed, total),
                "status_counts": dict(sorted(status_by_window[window].items())),
                "missing_reason_counts": dict(sorted(missing_by_window[window].items())),
                "observed_edge_after_costs_sign_counts": dict(
                    sorted(observed_edge_signs[window].items()),
                ),
                "observed_edge_after_costs_min": format(min(values), "f") if values else None,
                "observed_edge_after_costs_max": format(max(values), "f") if values else None,
                "observed_by_shadow_signal_tier": dict(sorted(tier_by_window[window].items())),
            },
        )

    long_missing = [window for window in fully_missing_windows if window in LONG_WINDOWS]
    all_observed_signs = Counter(
        sign
        for counter in observed_edge_signs.values()
        for sign, count in counter.items()
        for _ in range(count)
    )
    robustness_state = _robustness_summary(robustness)
    has_positive_observed_edge = all_observed_signs.get("positive", 0) > 0
    has_robust_positive_bucket = robustness_state.get("all_scenarios_without_stable_positive_bucket") is False

    if long_missing and not has_positive_observed_edge and not has_robust_positive_bucket:
        interpretation = "not_promotable_long_windows_missing_and_no_positive_signal"
    elif long_missing:
        interpretation = "not_promotable_long_windows_missing"
    elif has_positive_observed_edge or has_robust_positive_bucket:
        interpretation = "review_required_positive_signal_seen"
    else:
        interpretation = "not_promotable_no_positive_signal"

    return {
        "report_type": "okx_calendar_spread_phase2_posterior_coverage_gap",
        "boundary": {
            "mode": "offline_existing_posterior_artifacts_only",
            "no_okx_connection": True,
            "no_real_orders": True,
            "no_sandbox_execution": True,
            "no_phase0_rerun": True,
            "phase3_entry_allowed": False,
        },
        "summary": {
            "posterior_entries": entries,
            "windows": windows,
            "fully_missing_windows": fully_missing_windows,
            "long_window_missing": long_missing,
            "observed_edge_after_costs_sign_counts": dict(sorted(all_observed_signs.items())),
            "has_positive_observed_edge": has_positive_observed_edge,
            "has_robust_positive_bucket": has_robust_positive_bucket,
            "robustness": robustness_state,
            "phase2_gate_interpretation": interpretation,
            "recommended_next_action": (
                "keep_phase2_data_only_prioritize_fair_value_l2_then_posterior_coverage"
            ),
        },
        "window_coverage": window_reports,
        "gate_notes": [
            "long_window_missing_is_a_gate_failure_not_a_promotion_excuse",
            "short_window_observed_edges_do_not_show_positive_after_costs",
            "posterior_coverage_should_not_trigger_phase3_without_robust_positive_bucket",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Phase 2 后验覆盖缺口审计",
        "",
        "## 边界",
        "",
        "- 模式:仅使用已有 posterior outcomes / robustness 产物.",
        "- 不连接 OKX,不发真实订单,不执行 sandbox,不重跑 Phase 0.",
        "- 本报告不能放行 Phase 3,只能补强 Phase 2 gate 证据.",
        "",
        "## 摘要",
        "",
        f"- 后验条目数: `{report['summary']['posterior_entries']}`.",
        f"- 完全缺失窗口: `{report['summary']['fully_missing_windows']}`.",
        f"- 长窗口缺失: `{report['summary']['long_window_missing']}`.",
        f"- 已观测 edge 符号: `{report['summary']['observed_edge_after_costs_sign_counts']}`.",
        f"- 鲁棒性摘要: `{report['summary']['robustness']}`.",
        f"- gate 解释: `{report['summary']['phase2_gate_interpretation']}`.",
        "",
        "## 窗口覆盖",
        "",
    ]
    for row in report["window_coverage"]:
        lines.append(
            "- "
            f"{row['window']}: total=`{row['total']}`, observed=`{row['observed']}`, "
            f"missing=`{row['missing']}`, coverage_ratio=`{row['coverage_ratio']}`, "
            f"edge_signs=`{row['observed_edge_after_costs_sign_counts']}`, "
            f"missing_reasons=`{row['missing_reason_counts']}`.",
        )
    gate_note_text = {
        "long_window_missing_is_a_gate_failure_not_a_promotion_excuse": (
            "30m/1h 长窗口缺失是 gate 失败项, 不能作为 promotion 例外."
        ),
        "short_window_observed_edges_do_not_show_positive_after_costs": (
            "短窗口已观测 edge 在扣除成本后没有正样本."
        ),
        "posterior_coverage_should_not_trigger_phase3_without_robust_positive_bucket": (
            "没有非脆弱正 bucket 时, posterior coverage 本身不能触发 Phase 3."
        ),
    }
    lines.extend(
        [
            "",
            "## 门槛备注",
            "",
        ],
    )
    lines.extend(f"- {gate_note_text.get(note, note)}" for note in report["gate_notes"])
    lines.extend(
        [
            "",
            "## 建议",
            "",
            f"- `{report['summary']['recommended_next_action']}`.",
        ],
    )
    return "\n".join(lines) + "\n"


def write_outputs(report: dict[str, Any], output_json_path: Path, output_md_path: Path | None) -> None:
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if output_md_path is not None:
        output_md_path.parent.mkdir(parents=True, exist_ok=True)
        output_md_path.write_text(render_markdown(report), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("posterior_outcomes_path", type=Path)
    parser.add_argument("--robustness-path", type=Path, default=None)
    parser.add_argument("--output-json-path", type=Path, required=True)
    parser.add_argument("--output-md-path", type=Path, default=None)
    args = parser.parse_args()

    posterior = json.loads(args.posterior_outcomes_path.read_text(encoding="utf-8"))
    robustness = (
        json.loads(args.robustness_path.read_text(encoding="utf-8"))
        if args.robustness_path is not None
        else None
    )
    report = build_posterior_coverage_gap_report(posterior, robustness=robustness)
    write_outputs(report, args.output_json_path, args.output_md_path)
    print(json.dumps({"output_path": str(args.output_json_path), "summary": report["summary"]}, indent=2))


if __name__ == "__main__":
    main()
