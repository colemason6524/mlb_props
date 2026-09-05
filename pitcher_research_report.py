from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from mlb_props.pitcher_grading import (
    CONFIDENCE_BANDS,
    OPEN_STATUSES,
    VOID_STATUSES,
    MlbGradingClient,
    active_vs_shadow_metrics,
    bootstrap_by_date,
    brier_and_calibration,
    disagreement_analysis,
    earliest_first_pitch,
    graded_to_dict,
    load_history,
    resolve_candidates,
    segment_summary,
    stability_analysis,
    strict_eligible_candidates,
    price_shadow_analysis,
    _confidence_band,
    _edge_band,
    _hit_rate,
    _l5_band,
    _reliability_band,
)

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_HISTORY_DIR = REPO_ROOT / ".analysis" / "schema6" / "history"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grade pitcher schema history read-only.")
    parser.add_argument("--history-dir", type=Path, default=DEFAULT_HISTORY_DIR)
    parser.add_argument("--schema-version", type=int, default=6)
    parser.add_argument("--primary-since", type=date.fromisoformat, default=date(2026, 8, 5))
    parser.add_argument("--primary-through", type=date.fromisoformat, default=date(2026, 8, 17))
    parser.add_argument("--extension-through", type=date.fromisoformat, default=date(2026, 8, 19))
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / ".analysis" / "schema6")
    return parser.parse_args(argv)


def first_pitch_by_date(
    args: argparse.Namespace, client: MlbGradingClient
) -> dict[date, str]:
    result: dict[date, str] = {}
    start = args.primary_since
    end = args.today
    current = start
    while current <= end:
        try:
            games = client.fetch_schedule(current)
            if games:
                earliest = min(games, key=lambda game: game["game_date"] or "")
                result[current] = earliest["game_date"]
        except Exception:
            pass
        current = current.fromordinal(current.toordinal() + 1)
    return result


def _population_rows(history, args: argparse.Namespace) -> dict[str, list]:
    primary = [
        row
        for row in history.candidates
        if args.primary_since <= row.screen_date <= args.primary_through
    ]
    extension = [
        row
        for row in history.candidates
        if args.primary_through < row.screen_date <= args.extension_through
    ]
    pending = [
        row for row in history.candidates if args.extension_through < row.screen_date <= args.today
    ]
    primary_tiers = [row for row in primary if row.tier != "none"]
    extension_tiers = [row for row in extension if row.tier != "none"]
    pending_tiers = [row for row in pending if row.tier != "none"]
    return {
        "primary": primary,
        "primary_tiers": primary_tiers,
        "extension": extension,
        "extension_tiers": extension_tiers,
        "pending": pending,
        "pending_tiers": pending_tiers,
    }


def _resolution_counts(rows) -> dict[str, int]:
    return dict(Counter(row.outcome for row in rows))


def _recommendation_section(rows, title: str) -> list[str]:
    lines = [f"## {title}", ""]
    graded = [row for row in rows if row.outcome in {"win", "loss"}]
    hits = _hit_rate(rows)
    lines.append(f"- Rows: {len(rows)}")
    lines.append(f"- Graded (win/loss): {len(graded)}")
    lines.append(f"- Wins: {sum(1 for row in graded if row.outcome == 'win')}")
    lines.append(f"- Losses: {sum(1 for row in graded if row.outcome == 'loss')}")
    lines.append(f"- Pushes: {sum(1 for row in rows if row.outcome == 'push')}")
    lines.append(f"- Voids: {sum(1 for row in rows if row.outcome in VOID_STATUSES)}")
    lines.append(f"- Open/unresolved: {sum(1 for row in rows if row.outcome in OPEN_STATUSES)}")
    if hits is not None:
        lines.append(f"- Hit rate: {hits * 100:.1f}%")
    tiers = Counter(row.tier for row in rows)
    lines.append(f"- Tiers: {', '.join(f'{tier} {count}' for tier, count in sorted(tiers.items()))}")
    by_side = {
        side: [row for row in rows if row.side == side] for side in ("OVER", "UNDER")
    }
    for side, group in by_side.items():
        side_hits = _hit_rate(group)
        lines.append(
            f"- {side}: {len(group)} rows, "
            f"{sum(1 for row in group if row.outcome == 'win')}W/"
            f"{sum(1 for row in group if row.outcome == 'loss')}L, "
            f"{sum(1 for row in group if row.outcome == 'push')}P"
            + (f", {side_hits * 100:.1f}% hit" if side_hits is not None else "")
        )
    lines.append("")
    return lines


def _projection_stat_line(label: str, stat: dict) -> str:
    if stat["mae"] is None:
        return f"  - {label}: no resolved rows"
    bias = stat["bias"] if stat["bias"] is not None else 0.0
    rmse = stat["rmse"] if stat["rmse"] is not None else 0.0
    return (
        f"  - {label}: bias {bias:+.2f}, MAE {stat['mae']:.2f}, RMSE {rmse:.2f}"
    )


def _projection_section(rows, title: str) -> list[str]:
    lines = [f"## {title}", ""]
    metrics = active_vs_shadow_metrics(rows)
    k = metrics["k"]
    bf = metrics["bf"]
    outs = metrics["outs"]
    lines.append(f"- K projection rows (n={k['n']}):")
    lines.append(_projection_stat_line("Active", k["active"]))
    lines.append(_projection_stat_line("Shadow", k["shadow"]))
    if k["active"]["mae"] is not None and k["shadow"]["mae"] is not None:
        lines.append(
            f"  - Shadow minus active MAE: {k['shadow']['mae'] - k['active']['mae']:+.2f}"
        )
    lines.append(f"- BF projection rows (n={bf['n']}):")
    lines.append(_projection_stat_line("Active", bf["active"]))
    lines.append(_projection_stat_line("Shadow", bf["shadow"]))
    if bf["active"]["mae"] is not None and bf["shadow"]["mae"] is not None:
        lines.append(
            f"  - Shadow minus active MAE: {bf['shadow']['mae'] - bf['active']['mae']:+.2f}"
        )
    lines.append(f"- Outs projection rows (n={outs['n']}):")
    lines.append(_projection_stat_line("Active", outs["active"]))
    if outs["opportunity_shadow"]["mae"] is not None:
        lines.append(
            f"  - Opportunity shadow: bias {outs['opportunity_shadow']['bias']:+.2f}, "
            f"MAE {outs['opportunity_shadow']['mae']:.2f}, "
            f"RMSE {outs['opportunity_shadow']['rmse']:.2f}"
        )
    if outs["opportunity_bf"]["mae"] is not None:
        lines.append(
            f"  - Opportunity BF: bias {outs['opportunity_bf']['bias']:+.2f}, "
            f"MAE {outs['opportunity_bf']['mae']:.2f}"
        )
    if outs["pitch_budget"]["mae"] is not None:
        lines.append(
            f"  - Opportunity pitch budget: bias {outs['pitch_budget']['bias']:+.2f}, "
            f"MAE {outs['pitch_budget']['mae']:.2f}"
        )
    lines.append("")
    return lines


def _confidence_section(rows, title: str) -> list[str]:
    lines = [f"## {title}", ""]
    stats = brier_and_calibration(rows)
    lines.append(f"- Graded confidence estimates: {stats['graded_n']}")
    if stats["active_brier"] is not None:
        lines.append(f"- Active Brier: {stats['active_brier']:.3f}")
    if stats["shadow_brier"] is not None:
        lines.append(
            f"- Shadow Brier: {stats['shadow_brier']:.3f} "
            f"(n={stats['common_brier_n']}, common population)"
        )
        if stats["active_brier"] is not None:
            lines.append(f"- Shadow minus active Brier: {stats['shadow_brier'] - stats['active_brier']:+.3f}")
    lines.append("")
    lines.append("| Band | n | Avg forecast | Observed | Gap | Pushes |")
    lines.append("|---|---|---|---|---|---|")
    for band in CONFIDENCE_BANDS + ("unknown",):
        entry = stats["bands"].get(band)
        if not entry:
            continue
        lines.append(
            f"| {band} | {entry['n']} | {entry['avg_forecast'] * 100:.1f}% | "
            f"{entry['observed'] * 100:.1f}% | {entry['gap'] * 100:+.1f}pts | "
            f"{entry['pushes']} |"
        )
    lines.append("")
    return lines


def _segment_table(rows, title: str) -> list[str]:
    lines = [f"## {title}", ""]
    lines.append(
        "| Segment | n | Graded | Hit rate | W-L | P | V | Open | Active K MAE | Shadow K MAE | Active BF MAE | Shadow BF MAE |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    summaries = segment_summary(rows, lambda row: _l5_band(row))
    lines.extend(_render_segment_rows(summaries))
    lines.append("")
    lines.append("### By side")
    lines.append("")
    side_summaries = segment_summary(rows, lambda row: row.side)
    lines.append("| Segment | n | Graded | Hit rate | W-L | P | V | Open |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for summary in side_summaries:
        lines.append(
            f"| {summary['segment']} | {summary['n']} | {summary['graded']} | "
            f"{summary['hit_rate'] * 100:.1f}% | "
            f"{summary['wins']}-{summary['losses']} | {summary['pushes']} | "
            f"{summary['voids']} | {summary['open']} |"
        )
    lines.append("")
    lines.append("### By tier")
    lines.append("")
    tier_summaries = segment_summary(rows, lambda row: row.tier)
    for summary in tier_summaries:
        if summary["segment"] == "none":
            continue
        lines.append(
            f"- {summary['segment']}: n={summary['n']}, graded={summary['graded']}, "
            f"hit {summary['hit_rate'] * 100:.1f}% if graded, "
            f"active K MAE {summary['active_k_mae']}, shadow K MAE {summary['shadow_k_mae']}"
        )
    lines.append("")
    lines.append("### By projection edge")
    lines.append("")
    edge_summaries = segment_summary(rows, lambda row: _edge_band(row))
    for summary in edge_summaries:
        lines.append(
            f"- {summary['segment']}: n={summary['n']}, hit {summary['hit_rate'] * 100:.1f}% if graded, "
            f"active K MAE {summary['active_k_mae']}, shadow K MAE {summary['shadow_k_mae']}"
        )
    lines.append("")
    lines.append("### By workload reliability")
    lines.append("")
    reliability_summaries = segment_summary(rows, lambda row: _reliability_band(row))
    for summary in reliability_summaries:
        short_rate = (
            f"{summary['short_outing_rate'] * 100:.1f}%"
            if summary["short_outing_rate"] is not None
            else "-"
        )
        lines.append(
            f"- {summary['segment']}: n={summary['n']}, graded={summary['graded']}, "
            f"short-outing rate {short_rate}, "
            f"active K MAE {summary['active_k_mae']}, shadow K MAE {summary['shadow_k_mae']}"
        )
    lines.append("")
    return lines


def _render_segment_rows(summaries: list[dict]) -> list[str]:
    lines: list[str] = []
    for summary in summaries:
        hit = f"{summary['hit_rate'] * 100:.1f}%" if summary["hit_rate"] is not None else "-"
        lines.append(
            f"| {summary['segment']} | {summary['n']} | {summary['graded']} | {hit} | "
            f"{summary['wins']}-{summary['losses']} | {summary['pushes']} | "
            f"{summary['voids']} | {summary['open']} | "
            f"{_fmt(summary['active_k_mae'])} | {_fmt(summary['shadow_k_mae'])} | "
            f"{_fmt(summary['active_bf_mae'])} | {_fmt(summary['shadow_bf_mae'])} |"
        )
    return lines


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _disagreement_section(rows, title: str) -> list[str]:
    lines = [f"## {title}", ""]
    stats = disagreement_analysis(rows)
    lines.append(f"- Paired rows: {stats['paired_n']}")
    lines.append(f"- Side flips: {stats['side_flips']}")
    lines.append(f"- K projection moves >= 0.5: {stats['big_k_moves']}")
    lines.append(f"- Confidence moves >= 2pts: {stats['big_confidence_moves']}")
    lines.append(f"- Avg edge diff (shadow minus active): {stats['avg_edge_diff']:+.3f}")
    lines.append(
        f"- Shadow K error better/worse/tied: {stats['shadow_k_error_better']}/"
        f"{stats['shadow_k_error_worse']}/{stats['shadow_k_error_tied']}"
    )
    if stats["side_flip_rows"]:
        lines.append("")
        lines.append("Side flips:")
        for row in stats["side_flip_rows"]:
            lines.append(
                f"- {row['screen_date']} {row['pitcher']} line {row['line']}: "
                f"active {row['active_proj']} vs shadow {row['shadow_proj']} "
                f"(actual {row['actual']}, {row['outcome']})"
            )
    lines.append("")
    return lines


def _stability_section(rows, title: str) -> list[str]:
    lines = [f"## {title}", ""]
    stats = stability_analysis(rows)
    lines.append(f"- Distinct pitchers: {stats['distinct_pitchers']}")
    lines.append(f"- Overall active/shadow K MAE: {stats['all_active_k_mae']}/{stats['all_shadow_k_mae']}")
    lines.append("")
    lines.append("| Date | n | Graded | Hit | Active K MAE | Shadow K MAE | Active BF MAE | Shadow BF MAE | Shadow better/worse |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for entry in stats["dates"]:
        hit = f"{entry['hit_rate'] * 100:.1f}%" if entry["hit_rate"] is not None else "-"
        lines.append(
            f"| {entry['screen_date']} | {entry['n']} | {entry['graded']} | {hit} | "
            f"{_fmt(entry['active_k_mae'])} | {_fmt(entry['shadow_k_mae'])} | "
            f"{_fmt(entry['active_bf_mae'])} | {_fmt(entry['shadow_bf_mae'])} | "
            f"{entry['shadow_k_error_better']}/{entry['shadow_k_error_worse']} |"
        )
    lines.append("")
    lines.append("### Leave-one-slate-out (primary)")
    lines.append("")
    lines.append("| Excluded | n | Active K MAE | Shadow K MAE | Shadow delta |")
    lines.append("|---|---|---|---|---|")
    for entry in stats["leave_one_out"]:
        lines.append(
            f"| {entry['excluded']} | {entry['n']} | {_fmt(entry['active_k_mae'])} | "
            f"{_fmt(entry['shadow_k_mae'])} | {entry['shadow_delta']:+.2f} |"
        )
    lines.append("")
    lines.append("### Repeated-pitcher concentration")
    lines.append("")
    for pitcher, count in stats["pitcher_counts"].items():
        if count >= 2:
            lines.append(f"- {pitcher}: {count}")
    lines.append("")
    return lines


def _bootstrap_section(rows, title: str) -> list[str]:
    lines = [f"## {title}", ""]
    stats = bootstrap_by_date(rows)
    if not stats:
        lines.append("- No rows for bootstrap.")
        lines.append("")
        return lines
    lines.append(f"- Date-blocked bootstrap (n={stats['iterations']})")
    lines.append(f"- Median shadow minus active K MAE: {stats['median_shadow_minus_active_k_mae']:+.3f}")
    lines.append(f"- 2.5%/97.5%: {stats['pct2_5']:+.3f} / {stats['pct97_5']:+.3f}")
    lines.append(f"- P(shadow worse): {stats['shadow_worse_pct'] * 100:.1f}%")
    lines.append("")
    return lines


def build_report(args: argparse.Namespace, history, populations) -> str:
    lines: list[str] = []
    lines.append("# Pitcher Schema-6 Grading Report")
    lines.append("")
    lines.append(f"- Generated (local): {datetime.now().astimezone().isoformat()}")
    lines.append(f"- Schema version: {args.schema_version}")
    lines.append(f"- Primary window: {args.primary_since} to {args.primary_through}")
    extension_start = date.fromordinal(args.primary_through.toordinal() + 1)
    lines.append(f"- Extension window: {extension_start} to {args.extension_through}")
    lines.append(f"- Pending through: {args.today}")
    lines.append("")

    lines.append("## Integrity And Resolution")
    lines.append("")
    lines.append(f"- Snapshots: {len(history.entries)}")
    for entry in history.entries:
        timing = "unknown" if entry.pregame is None else ("pregame" if entry.pregame else "POST-START")
        delivery = "unknown" if entry.discord_delivered is None else ("delivered" if entry.discord_delivered else "NOT-DELIVERED")
        conflicts = "; ".join(entry.conflicts) or "none"
        lines.append(
            f"- {entry.file_name}: screen={entry.screen_date}, schema={entry.schema_version}, "
            f"candidates={entry.candidate_count}, shadows={entry.shadow_count}, "
            f"tiers core/lean/watch={entry.core_count}/{entry.lean_count}/{entry.watch_count}, "
            f"FanDuel pages {entry.fanduel_pages_loaded}/{entry.fanduel_pages_expected}, "
            f"K lines {entry.fanduel_k_lines}, timing={timing}, delivery={delivery}, "
            f"conflicts=[{conflicts}]"
        )
    for error in history.integrity_errors:
        lines.append(f"- INTEGRITY ERROR: {error}")
    lines.append("")

    eligible = strict_eligible_candidates(history)
    eligible_pregame_only = strict_eligible_candidates(history, require_delivered=False)
    lines.append("### Strict grading eligibility (schema-7 audit identity)")
    lines.append("")
    lines.append(
        f"- Pregame + delivered: {len(eligible)} of {len(history.candidates)} candidates "
        f"({len(history.entries)} snapshots)"
    )
    lines.append(
        f"- Pregame only (delivery optional): {len(eligible_pregame_only)} of "
        f"{len(history.candidates)} candidates"
    )
    lines.append("")


    lines.append("### Primary population")
    lines.append("")
    lines.extend(_recommendation_section(populations["primary_tiers"], "Core/Lean/Watch Recommendations (Primary)"))
    lines.extend(_recommendation_section(populations["primary"], "Qualified Research Profiles (Primary)"))

    lines.append("### Extension population")
    lines.append("")
    lines.extend(_recommendation_section(populations["extension_tiers"], "Core/Lean/Watch Recommendations (Extension)"))
    lines.extend(_recommendation_section(populations["extension"], "Qualified Research Profiles (Extension)"))

    lines.append("### Pending")
    lines.append("")
    lines.append(f"- Pending profiles: {len(populations['pending'])}")
    lines.append(f"- Pending tier opinions: {len(populations['pending_tiers'])}")
    lines.append("")

    lines.extend(_projection_section(populations["primary"], "Paired Projection Metrics (Primary)"))
    lines.extend(_projection_section(populations["primary_tiers"], "Paired Projection Metrics (Primary Tiers)"))
    lines.extend(_projection_section(populations["extension"], "Paired Projection Metrics (Extension)"))

    lines.extend(_confidence_section(populations["primary"], "Confidence Calibration (Primary)"))
    lines.extend(_confidence_section(populations["primary_tiers"], "Confidence Calibration (Primary Tiers)"))

    lines.append("## Price Shadow (Primary)")
    lines.append("")
    price_stats = price_shadow_analysis(populations["primary"])
    lines.append(f"- Priced rows: {price_stats['priced_rows']}")
    lines.append(f"- Price supports side: {price_stats['price_supports_side']}")
    lines.append(f"- Price against side: {price_stats['price_against_side']}")
    lines.append(f"- Unpriced rows: {price_stats['unpriced_rows']}")
    lines.append("")

    lines.extend(_segment_table(populations["primary"], "Segments (Primary)"))
    lines.extend(_segment_table(populations["primary_tiers"], "Tier Segments (Primary Tiers)"))

    lines.extend(_disagreement_section(populations["primary"], "Active-Shadow Disagreements (Primary)"))

    lines.extend(_stability_section(populations["primary"], "Slate Stability (Primary)"))
    lines.extend(_bootstrap_section(populations["primary"], "Date-Blocked Bootstrap (Primary)"))

    return "\n".join(lines)


def build_summary_json(args: argparse.Namespace, history, populations) -> dict:
    primary = populations["primary"]
    primary_tiers = populations["primary_tiers"]
    return {
        "arguments": {
            "history_dir": str(args.history_dir),
            "schema_version": args.schema_version,
            "primary_since": args.primary_since.isoformat(),
            "primary_through": args.primary_through.isoformat(),
            "extension_through": args.extension_through.isoformat(),
            "today": args.today.isoformat(),
        },
        "populations": {
            key: {"count": len(rows), "resolution": _resolution_counts(rows)}
            for key, rows in populations.items()
        },
        "primary_projection_metrics": active_vs_shadow_metrics(primary),
        "primary_tier_projection_metrics": active_vs_shadow_metrics(primary_tiers),
        "primary_confidence": brier_and_calibration(primary),
        "primary_tier_confidence": brier_and_calibration(primary_tiers),
        "primary_segments": {
            "l5": segment_summary(primary, lambda row: _l5_band(row)),
            "side": segment_summary(primary, lambda row: row.side),
            "tier": segment_summary(primary, lambda row: row.tier),
            "edge": segment_summary(primary, lambda row: _edge_band(row)),
            "reliability": segment_summary(primary, lambda row: _reliability_band(row)),
        },
        "primary_disagreements": disagreement_analysis(primary),
        "primary_stability": stability_analysis(primary),
        "primary_bootstrap": bootstrap_by_date(primary),
        "primary_price_shadow": price_shadow_analysis(primary),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.history_dir.is_dir():
        print(f"history dir not found: {args.history_dir}", file=sys.stderr)
        return 2
    history = load_history(args.history_dir, args.schema_version)
    client = MlbGradingClient(cache_dir=args.cache_dir)
    first_pitch = first_pitch_by_date(args, client)
    earliest_first_pitch(history.entries, first_pitch)
    resolve_candidates(history.candidates, client, today=args.today)
    populations = _population_rows(history, args)

    report = build_report(args, history, populations)
    summary = build_summary_json(args, history, populations)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "pitcher_schema6_manifest.json"
    rows_path = args.output_dir / "pitcher_schema6_resolved_rows.json"
    report_path = args.output_dir / "pitcher_schema6_report.md"
    summary_path = args.output_dir / "pitcher_schema6_summary.json"

    manifest_payload = {
        "entries": [
            {
                **{
                    field_name: getattr(entry, field_name)
                    for field_name in (
                        "file_name",
                        "file_hash",
                        "screen_date",
                        "exported_at",
                        "schema_version",
                        "model_version",
                        "tier_policy_version",
                        "confidence_model_version",
                        "display_policy_version",
                        "opportunity_shadow_version",
                        "recency_shadow_version",
                        "run_note",
                        "mode",
                        "candidate_count",
                        "shadow_count",
                        "core_count",
                        "lean_count",
                        "watch_count",
                        "games",
                        "prop_lines",
                        "coverage_floor",
                        "coverage_status",
                        "fanduel_pages_expected",
                        "fanduel_pages_loaded",
                        "fanduel_k_lines",
                        "dk_k_lines",
                        "dk_outs_lines",
                        "earliest_first_pitch_utc",
                        "pregame",
                        "discord_delivered",
                        "discord_sent_at",
                        "conflicts",
                        "errors",
                    )
                }
            }
            for entry in history.entries
        ],
        "integrity_errors": history.integrity_errors,
    }
    rows_payload = [graded_to_dict(row) for row in history.candidates]
    with open(manifest_path, "w") as handle:
        json.dump(manifest_payload, handle, indent=2, sort_keys=True, default=str)
    with open(rows_path, "w") as handle:
        json.dump(rows_payload, handle, indent=2, sort_keys=True, default=str)
    with open(summary_path, "w") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True, default=str)
    with open(report_path, "w") as handle:
        handle.write(report)

    print(report)
    print(f"\nWrote {manifest_path}")
    print(f"Wrote {rows_path}")
    print(f"Wrote {summary_path}")
    print(f"Wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
