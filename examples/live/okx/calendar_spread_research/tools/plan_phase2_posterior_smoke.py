#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------
"""
Plan the next Phase 2 no-order posterior coverage smoke from bucket evidence.

This tool is intentionally offline. It reads an existing bucket-evidence JSON
artifact and emits a deterministic plan for a later live-shadow-only run. It
does not connect to OKX, does not start Nautilus, and cannot submit orders.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


LONG_WINDOWS = ("30m", "1h")
SHORT_WINDOWS = ("1m", "5m")


def _count(payload: dict[str, Any], *path: str) -> int:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return 0
        current = current.get(key)
    if isinstance(current, bool):
        return int(current)
    if isinstance(current, int):
        return current
    return 0


def _window_missing_count(bucket: dict[str, Any], window: str) -> int:
    return _count(bucket, "posterior_window_status_counts", window, "missing")


def _window_observed_count(bucket: dict[str, Any], window: str) -> int:
    return _count(bucket, "posterior_window_status_counts", window, "observed")


def _short_observed_count(bucket: dict[str, Any]) -> int:
    return sum(_window_observed_count(bucket, window) for window in SHORT_WINDOWS)


def _long_missing_count(bucket: dict[str, Any]) -> int:
    return sum(_window_missing_count(bucket, window) for window in LONG_WINDOWS)


def _candidate_count(bucket: dict[str, Any]) -> int:
    return _count(bucket, "shadow_signal_tier_counts", "candidate")


def _score_bucket(bucket: dict[str, Any]) -> int:
    candidate_count = _candidate_count(bucket)
    expected_count = _count(bucket, "expected_edge_after_costs", "count")
    short_observed = _short_observed_count(bucket)
    long_missing = _long_missing_count(bucket)
    return candidate_count * 1000 + expected_count * 100 + short_observed * 10 + long_missing


def _target_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    expected_count = _count(bucket, "expected_edge_after_costs", "count")
    return {
        "bucket_key": bucket.get("bucket_key"),
        "underlying": bucket.get("underlying"),
        "kind": bucket.get("kind"),
        "strike": bucket.get("strike"),
        "expiry_pair": bucket.get("expiry_pair"),
        "total_events": bucket.get("total_events"),
        "candidate_events": _candidate_count(bucket),
        "expected_edge_after_costs": bucket.get("expected_edge_after_costs") or {},
        "short_observed_windows": _short_observed_count(bucket),
        "missing_30m": _window_missing_count(bucket, "30m"),
        "missing_1h": _window_missing_count(bucket, "1h"),
        "priority_score": _score_bucket(bucket),
        "reason": (
            "candidate bucket with computed proxy-cost evidence and missing long posterior "
            "coverage; use only for no-order shadow observation."
            if expected_count > 0
            else "candidate bucket with missing long posterior coverage but no computed proxy-cost "
            "edge yet; include only as low-count exploratory no-order shadow coverage."
        ),
    }


def _select_target_buckets(bucket_evidence: dict[str, Any], max_buckets: int) -> list[dict[str, Any]]:
    buckets = bucket_evidence.get("buckets") or []
    eligible = [
        bucket
        for bucket in buckets
        if _candidate_count(bucket) > 0 and _long_missing_count(bucket) > 0
    ]
    eligible.sort(
        key=lambda bucket: (
            -_score_bucket(bucket),
            str(bucket.get("underlying") or ""),
            str(bucket.get("bucket_key") or ""),
        ),
    )
    return [_target_bucket(bucket) for bucket in eligible[:max_buckets]]


def build_posterior_smoke_plan(
    bucket_evidence: dict[str, Any],
    *,
    max_buckets: int = 6,
) -> dict[str, Any]:
    targets = _select_target_buckets(bucket_evidence, max_buckets=max_buckets)
    overall = bucket_evidence.get("overall") or {}
    has_positive_evidence = bool(overall.get("positive_expected_edge_buckets")) or bool(
        overall.get("positive_observed_edge_buckets"),
    )
    return {
        "report_type": "okx_calendar_spread_phase2_posterior_smoke_plan",
        "source_report_type": bucket_evidence.get("report_type"),
        "boundary": {
            "mode": "offline_plan_only",
            "no_okx_connection": True,
            "no_real_orders": True,
            "no_sandbox_execution": True,
            "no_phase0_rerun": True,
            "live_run_requires_explicit_user_request": True,
        },
        "source_verdict": overall.get("promotion_verdict"),
        "recommended_next_action": (
            "short_no_order_wider_posterior_smoke_after_explicit_request"
            if not has_positive_evidence
            else "review_positive_bucket_before_any_live_smoke"
        ),
        "proposed_live_shadow_shape": {
            "posterior_windows_seconds": [60, 300, 1800, 3600],
            "minimum_runtime_minutes": 70,
            "maximum_runtime_minutes": 90,
            "execution_enabled": False,
            "dry_run": True,
            "sandbox_execution": False,
            "real_orders": False,
            "max_single_run_hours": 1.5,
            "target_tmux_window_if_requested": "run-okx window 6 run-cal",
        },
        "acceptance_criteria": [
            "No real orders, no sandbox execution, and zero sandbox fill rows.",
            "No OKX 50011, _UM, contextual 60018, traceback, or unbalanced L2 subscribe/unsubscribe.",
            "At least one target candidate bucket records observed 30m or 1h posterior windows.",
            "Report remains non-promoting unless executable observed edges become positive after costs.",
            "Preserve l1_mid_calendar_proxy_not_edge_model as diagnostic context, not production fair value.",
        ],
        "target_buckets": targets,
        "target_summary": {
            "selected_bucket_count": len(targets),
            "selected_candidate_events": sum(int(bucket["candidate_events"] or 0) for bucket in targets),
            "selected_missing_30m": sum(int(bucket["missing_30m"] or 0) for bucket in targets),
            "selected_missing_1h": sum(int(bucket["missing_1h"] or 0) for bucket in targets),
        },
    }


def render_markdown(plan: dict[str, Any]) -> str:
    shape = plan["proposed_live_shadow_shape"]
    lines = [
        "# Phase 2 posterior smoke 计划",
        "",
        "## 边界",
        "",
        "- 模式:只生成离线计划.",
        "- 本 planner 不连接 OKX.",
        "- 未来如需执行,也必须是 no-order live shadow:无真实订单、无 sandbox execution.",
        "- 不重跑 Phase 0,不提升 Phase 1 sandbox execution.",
        "",
        "## 推荐",
        "",
        f"- source verdict:`{plan['source_verdict']}`.",
        f"- recommended next action:`{plan['recommended_next_action']}`.",
        f"- proposed windows seconds:`{shape['posterior_windows_seconds']}`.",
        f"- runtime bound:`{shape['minimum_runtime_minutes']}`-`{shape['maximum_runtime_minutes']}` 分钟.",
        f"- 如明确请求执行,目标 tmux window:`{shape['target_tmux_window_if_requested']}`.",
        "",
        "## 验收条件",
        "",
    ]
    lines.extend(f"- {criterion}" for criterion in plan["acceptance_criteria"])
    lines.extend(
        [
            "",
            "## 目标 bucket 摘要",
            "",
            f"- `{plan['target_summary']}`",
            "",
            "## 目标 buckets",
            "",
        ],
    )
    for bucket in plan["target_buckets"]:
        lines.extend(
            [
                f"### {bucket['bucket_key']}",
                "",
                f"- candidate events:`{bucket['candidate_events']}`;total events:`{bucket['total_events']}`.",
                f"- 扣成本后的 expected edge:`{bucket['expected_edge_after_costs']}`.",
                f"- short observed windows:`{bucket['short_observed_windows']}`.",
                f"- missing 30m/1h:`{bucket['missing_30m']}` / `{bucket['missing_1h']}`.",
                f"- 原因:{bucket['reason']}",
                "",
            ],
        )
    return "\n".join(lines)


def write_plan(plan: dict[str, Any], output_json: Path, output_markdown: Path | None) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    if output_markdown is not None:
        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        output_markdown.write_text(render_markdown(plan), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket-evidence", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, default=None)
    parser.add_argument("--max-buckets", type=int, default=6)
    args = parser.parse_args()

    if args.max_buckets <= 0:
        raise ValueError("--max-buckets must be positive")

    plan = build_posterior_smoke_plan(
        json.loads(args.bucket_evidence.read_text(encoding="utf-8")),
        max_buckets=args.max_buckets,
    )
    write_plan(plan, args.output_json, args.output_markdown)
    print(
        json.dumps(
            {
                "output_json": str(args.output_json),
                "recommended_next_action": plan["recommended_next_action"],
                "target_summary": plan["target_summary"],
            },
            indent=2,
            ensure_ascii=False,
        ),
    )


if __name__ == "__main__":
    main()
