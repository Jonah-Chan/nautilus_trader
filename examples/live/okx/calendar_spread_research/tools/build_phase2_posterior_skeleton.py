#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------
"""
Build a posterior-outcome skeleton from a Phase 2 shadow-signal artifact.

The output is a strict `--posterior-path` map for Phase 2. It deliberately marks
all windows missing, so a later live-shadow collector can fill observed 1m, 5m,
30m, and 1h outcomes without inventing a new format.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


POSTERIOR_WINDOWS = ("1m", "5m", "30m", "1h")
DEFAULT_POSTERIOR_SOURCE = "pending_live_shadow_posterior"
DEFAULT_MISSING_REASON = "pending_live_shadow_observation"
DEFAULT_INCLUDED_TIERS = {
    "candidate",
    "shadow_only_warning",
}


def _posterior_entry(event: dict[str, Any], *, source: str, missing_reason: str) -> dict[str, Any]:
    return {
        "source": source,
        "windows": {
            window: {
                "status": "missing",
                "reason": missing_reason,
            }
            for window in POSTERIOR_WINDOWS
        },
        "event_ref": {
            "audit_event_id": event.get("audit_event_id"),
            "basket_id": event.get("basket_id"),
            "bucket_key": event.get("bucket_key"),
            "underlying": event.get("underlying"),
            "settlement": event.get("settlement"),
            "kind": event.get("kind"),
            "strike": event.get("strike"),
            "expiry_pair": event.get("expiry_pair"),
            "shadow_signal_tier": event.get("shadow_signal_tier"),
            "signal_stage": event.get("signal_stage"),
            "execution_policy": event.get("execution_policy"),
        },
    }


def build_posterior_skeleton(
    phase2_analysis: dict[str, Any],
    *,
    include_tiers: set[str] | None = None,
    source: str = DEFAULT_POSTERIOR_SOURCE,
    missing_reason: str = DEFAULT_MISSING_REASON,
) -> dict[str, Any]:
    tiers = include_tiers or DEFAULT_INCLUDED_TIERS
    shadow_events = phase2_analysis.get("shadow_events") or []
    skeleton: dict[str, Any] = {}
    skipped_missing_key = 0
    tier_counts = Counter()

    for event in shadow_events:
        tier = str(event.get("shadow_signal_tier") or "unknown")
        if tier not in tiers:
            continue
        key = event.get("audit_event_id")
        if not key:
            skipped_missing_key += 1
            continue
        tier_counts[tier] += 1
        skeleton[str(key)] = _posterior_entry(
            event,
            source=source,
            missing_reason=missing_reason,
        )

    return {
        "summary": {
            "source_phase2_mode": (phase2_analysis.get("summary") or {}).get("phase2_mode"),
            "source_phase2_events_total": (phase2_analysis.get("summary") or {}).get("events_total"),
            "included_tiers": sorted(tiers),
            "posterior_entries": len(skeleton),
            "posterior_entries_by_tier": dict(sorted(tier_counts.items())),
            "skipped_missing_audit_event_id": skipped_missing_key,
            "posterior_source": source,
            "missing_reason": missing_reason,
            "windows": list(POSTERIOR_WINDOWS),
            "status": "skeleton_missing_windows_only",
        },
        "posterior_outcomes": skeleton,
    }


def write_artifact(artifact: dict[str, Any], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase2_artifact_path", type=Path)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument("--source", default=DEFAULT_POSTERIOR_SOURCE)
    parser.add_argument("--missing-reason", default=DEFAULT_MISSING_REASON)
    parser.add_argument(
        "--include-tier",
        action="append",
        dest="include_tiers",
        default=None,
        help="Shadow signal tier to include. Repeat to include multiple tiers.",
    )
    args = parser.parse_args()

    phase2_analysis = json.loads(args.phase2_artifact_path.read_text(encoding="utf-8"))
    artifact = build_posterior_skeleton(
        phase2_analysis,
        include_tiers=set(args.include_tiers) if args.include_tiers else None,
        source=args.source,
        missing_reason=args.missing_reason,
    )
    write_artifact(artifact, args.output_path)
    print(json.dumps({"output_path": str(args.output_path), "summary": artifact["summary"]}, indent=2))


if __name__ == "__main__":
    main()
