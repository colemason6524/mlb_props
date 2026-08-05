from __future__ import annotations

from collections.abc import Iterable

from .hot_hits_policy import (
    CORE_FIRST_POLICY_VERSION,
    HOT_HITS_POLICY_VERSION,
    hot_hit_discord_eligible as policy_hot_hit_discord_eligible,
    hot_hit_discord_sort_key as policy_hot_hit_discord_sort_key,
    hot_hit_support_count as policy_hot_hit_support_count,
    hot_hit_tier as policy_hot_hit_tier,
    select_hot_hits_card,
)
from .models import Candidate, HotHitCandidate, StarterAssessment
from .pitcher_presentation import (
    BEST_AVAILABLE_LIMIT,
    PitcherPresentation,
    build_pitcher_presentations,
    opportunity_reliability,
)
from .tiers import side_projection_edge


FLAG_LABELS = {
    "GOOD_MATCHUP": "Matchup+",
    "TOUGH_MATCHUP": "Matchup-",
    "WORKLOAD_PLUS": "Workload+",
    "LOW_PITCH": "LowPitch",
    "SHORT_LEASH": "ShortLeash",
    "VOLATILE": "Volatile",
    "FAVORITE": "Fav",
    "DOG": "Dog",
    "PARK_HITTER": "Park+Runs",
    "PARK_PITCHER": "Park+Pitch",
    "TREND_PLUS": "Trend+",
    "SEASON_PLUS": "Season+",
    "LINE_HIGH": "LineHigh",
    "LINE_LOW": "LineLow",
    "UNDER": "Under",
    "THIN": "Thin",
    "K_EFF": "KEff+",
    "K_EFF_LOW": "KEff-",
    "CONTROL_RISK": "Wild",
    "RUN_RISK": "RunRisk",
    "QS_PLUS": "QS+",
    "SHORT_START_RISK": "ShortStart",
    "PATIENT_OPP": "PatientOpp",
    "FREE_SWING_OPP": "ChaseOpp",
    "CONSISTENT": "Consistent",
    "RECENT_WEAK": "RecentWeak",
    "EDGE_PLUS": "Edge+",
    "EDGE_EXTREME": "EdgeExtreme",
    "DEPTH_PLUS": "Depth+",
    "SHORT_LEASH_PLUS": "ShortLeash+",
    "MATCHUP_K_PLUS": "OppK+",
    "MATCHUP_K_MINUS": "OppK-",
    "OPP_OUTS_PLUS": "OppOuts+",
    "NO_START_DATA": "NoStartData",
    "THIN_START_SAMPLE": "ThinStartSample",
    "LIMITED_START_SAMPLE": "LimitedStartSample",
    "LONG_LAYOFF": "LongLayoff",
    "QUICK_RETURN": "QuickReturn",
    "VERY_LOW_RECENT_PITCH_COUNT": "VeryLowRecentPitch",
    "LOW_RECENT_PITCH_COUNT": "LowRecentPitch",
    "WORKLOAD_RAMP": "WorkloadRamp",
    "WORKLOAD_DECLINE": "WorkloadDecline",
    "PITCH_COUNT_VOLATILE": "PitchCountVolatile",
    "OUTS_VOLATILE": "OutsVolatile",
    "MULTIPLE_SHORT_STARTS": "MultipleShortStarts",
    "RECENT_SHORT_START": "RecentShortStart",
    "MIXED_RECENT_ROLE": "MixedRecentRole",
    "INCOMPLETE_BF_DATA": "IncompleteBFData",
    "HOT": "Hot",
    "VERY_HOT": "VeryHot",
    "HIT_STREAK": "HitStreak",
    "ORDER_TOP": "TopOrder",
    "ORDER_VALUE": "ValueOrder",
    "ORDER_LOW": "LowOrder",
    "MATCHUP_PLUS": "Matchup+",
    "MATCHUP_MINUS": "Matchup-",
    "PITCHER_HITS": "PitcherHits",
    "CONTACT_PLUS": "Contact+",
    "PITCHER_K_RISK": "KRisk",
    "WALK_RISK": "WalkRisk",
    "BVP_PLUS": "BvP+",
    "BVP_THIN": "BvPThin",
    "BVP_MINUS": "BvP-",
}


def render_candidates(
    candidates: Iterable[Candidate],
    limit: int = 30,
    min_score: int = 7,
    lean_min_score: int = 4,
    watch_min_score: int = 0,
) -> str:
    presentations = build_pitcher_presentations(
        candidates,
        min_score=min_score,
        lean_min_score=lean_min_score,
        watch_min_score=watch_min_score,
    )
    core = [item for item in presentations if item.recommendation_tier == "core"]
    leans = [item for item in presentations if item.recommendation_tier == "lean"]
    watch = [item for item in presentations if item.recommendation_tier == "watch"]
    best_available = [item for item in presentations if item.display_role == "best_available"]

    rendered_sections: list[str] = []
    if core:
        top = presentations[0]
        rendered_sections.append(
            f"Slate read: {len(core)} Core play(s). Top of slate is #{top.slate_rank} "
            f"{top.candidate.subject_name} {top.candidate.side} {top.candidate.line:.1f} "
            f"at provisional confidence {top.confidence_estimate.confidence_percentage}%."
        )
    elif best_available:
        top = best_available[0]
        rendered_sections.append(
            "Slate read: No Core plays cleared the absolute standard. "
            f"Best available is #{top.slate_rank} {top.candidate.subject_name} "
            f"{top.candidate.side} {top.candidate.line:.1f} "
            f"at provisional confidence {top.confidence_estimate.confidence_percentage}% "
            f"({top.recommendation_tier.title()}); it is not promoted to Core."
        )
    else:
        rendered_sections.append(
            "Slate read: No Core, Lean, or Watch candidates cleared the display floor."
        )
    rendered_sections.append("")

    sections: list[tuple[str, list[PitcherPresentation]]] = []
    if core:
        sections.append(("Core Plays", core[:limit]))
        if leans:
            sections.append(("Leans", leans[:limit]))
        if watch:
            sections.append(("Watchlist", watch[:limit]))
    elif best_available:
        best_ids = {id(item.candidate) for item in best_available}
        sections.append(("Best Available (Lean/Watch; not Core)", best_available[:limit]))
        remaining_leans = [item for item in leans if id(item.candidate) not in best_ids]
        remaining_watch = [item for item in watch if id(item.candidate) not in best_ids]
        if remaining_leans:
            sections.append(("Additional Leans", remaining_leans[:limit]))
        if remaining_watch:
            sections.append(("Additional Watchlist", remaining_watch[:limit]))
    if not sections:
        sections.append(("Candidates", []))

    for title, items in sections:
        rendered_sections.append(f"{title}:")
        rendered_sections.extend(_render_table(items))
        rendered_sections.append("")

    rendered_sections.append(
        "Legend: Provisional Conf is the price-agnostic estimated chance that the listed side clears the posted line; it is not yet calibrated. "
        "Rank follows that estimate within today's eligible board; Tier remains the absolute Core/Lean/Watch recommendation. "
        "Signal is retained as an internal additive diagnostic, not a probability. "
        "Work Rel is display-only workload reliability and does not change the tier. "
        "Proj = today's projected value; Side Edge is positive when the projection supports the listed side; "
        "Proj Outs/BF = projected opportunity; L5/L10 Hit = side-specific recent hit counts; "
        "Avg/Med = rolling strikeout production; Pitch = average pitches thrown; Outs = average outs recorded; "
        "KRate/BBRate = recent strikeout and walk rates by batter faced; Stab = lower recent volatility is better; "
        "Match = blended matchup rating from opponent strikeout tendency, patience, offensive quality, and expected leash context."
    )
    return "\n".join(rendered_sections)


def render_starter_board(assessments: Iterable[StarterAssessment], limit: int = 30) -> str:
    items = list(assessments)[:limit]
    headers = [
        "Pitcher",
        "Team",
        "Opp",
        "Hand",
        "Ks Line",
        "Lean",
        "Side Edge",
        "Signal",
        "Work Rel",
        "Status",
        "Starts",
        "Ks L5",
        "Ks Szn",
        "Outs L5",
        "Outs Szn",
        "Pitch L5",
        "KRate",
        "BBRate",
        "Proj KRate",
        "Ks Match",
        "Outs Match",
        "Ks Sig",
        "Proj Ks",
        "Outs Sig",
        "Proj Outs",
        "Proj BF",
        "Flags",
    ]
    rows: list[list[str]] = []
    for item in items:
        rows.append(
            [
                item.pitcher_name,
                item.team,
                item.opponent,
                item.hand or "-",
                f"{item.strikeout_line:.1f}" if item.strikeout_line is not None else "-",
                item.lean_side or "-",
                f"{_starter_side_edge(item):+.2f}" if item.lean_edge is not None else "-",
                str(item.lean_score) if item.lean_score is not None else "-",
                opportunity_reliability(item),
                _display_shortlist_status(item),
                str(item.season_starts),
                f"{item.avg_strikeouts_last_5:.1f}",
                f"{item.avg_strikeouts_season:.1f}",
                f"{item.avg_outs_last_5:.1f}",
                f"{item.avg_outs_season:.1f}",
                f"{item.avg_pitch_count_last_5:.1f}",
                f"{item.avg_k_rate_last_5:.3f}",
                f"{item.avg_walk_rate_last_5:.3f}",
                f"{item.projected_k_rate:.3f}",
                f"{item.matchup_rating_ks:+.2f}",
                f"{item.matchup_rating_outs:+.2f}",
                f"{item.ks_signal:.1f}",
                f"{item.projected_strikeouts:.1f}",
                f"{item.outs_signal:.1f}",
                f"{item.projected_outs:.1f}",
                f"{item.projected_batters_faced:.1f}",
                ",".join(_display_flag(flag) for flag in item.flags),
            ]
        )

    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    output = ["All Starters:"]
    output.append("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    output.append("  ".join("-" * widths[index] for index in range(len(headers))))
    for row in rows:
        output.append("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))
    if not rows:
        output.append("No probable starters met the minimum-start threshold for the daily board.")
    output.append("")
    output.append(
        "Starter board legend: Ks Line is the posted strikeout line when available; Lean is the model side for that line; "
        "Side Edge is positive when the projection supports the listed side; Signal is the same internal additive balance used by the shortlist, not a confidence percentage; "
        "Work Rel is display-only workload reliability and does not change recommendation tier; "
        "Status shows whether the play qualified, was blocked by the shortlist gate, or just missed the display cutoff. "
        "Ks/Outs Szn = season anchors and Ks/Outs L5 = recent form check; Proj KRate/Ks/Outs/BF = today's projected strikeout rate, strikeouts, outs, and batters faced opportunity."
    )
    return "\n".join(output)


def render_pitcher_props_discord_embeds(
    candidates: Iterable[Candidate],
    screen_date,
    games_count: int,
    prop_line_count: int,
    coverage_status: str,
    min_score: int,
    lean_min_score: int,
    watch_min_score: int,
    core_limit: int = 5,
    watch_limit: int = 5,
) -> list[dict]:
    presentations = build_pitcher_presentations(
        candidates,
        min_score=min_score,
        lean_min_score=lean_min_score,
        watch_min_score=watch_min_score,
    )
    core = [
        item
        for item in presentations
        if item.recommendation_tier == "core"
    ][:core_limit]
    alternatives = [
        item
        for item in presentations
        if item.recommendation_tier in {"lean", "watch"}
    ][: min(watch_limit, BEST_AVAILABLE_LIMIT)]

    description = (
        f"{prop_line_count} prop lines across {games_count} games. "
        f"Coverage `{coverage_status}`. "
        "Tier is the absolute recommendation; # rank is relative to today's eligible board. "
        "Confidence is provisional and price-agnostic; workload reliability is display-only."
    )
    embed = {
        "title": f"MLB Pitcher Props - {screen_date}",
        "description": description,
        "color": _pitcher_props_embed_color(
            [item.candidate for item in core],
            [item.candidate for item in alternatives],
            coverage_status,
        ),
        "fields": [],
        "footer": {
            "text": (
                "Confidence is provisional and excludes sportsbook price. "
                "Confirm line availability and game status before locking."
            )
        },
    }

    if not core:
        embed["fields"].append(
            {
                "name": "Core Standard",
                "value": (
                    "No Core plays cleared the absolute standard. "
                    "Ranked alternatives below remain Lean/Watch and are not promoted to Core."
                ),
                "inline": False,
            }
        )
    else:
        for presentation in core:
            item = presentation.candidate
            prop_label = _pitcher_prop_label(item)
            embed["fields"].append(
                {
                    "name": (
                        f"#{presentation.slate_rank} {presentation.confidence_estimate.label.title()} "
                        f"{presentation.confidence_estimate.confidence_percentage}% | Core | {item.subject_name} "
                        f"{item.side} {item.line:.1f} {prop_label}"
                    ),
                    "value": _pitcher_prop_embed_value(presentation),
                    "inline": False,
                }
            )

    if not alternatives:
        embed["fields"].append(
            {
                "name": "Ranked Alternatives",
                "value": "No Lean or Watch candidates cleared the display floor.",
                "inline": False,
            }
        )
    else:
        for presentation in alternatives:
            item = presentation.candidate
            label = presentation.recommendation_tier.title()
            role = "Best Available" if presentation.display_role == "best_available" else label
            prop_label = _pitcher_prop_label(item)
            embed["fields"].append(
                {
                    "name": (
                        f"#{presentation.slate_rank} {presentation.confidence_estimate.label.title()} "
                        f"{presentation.confidence_estimate.confidence_percentage}% | {role} ({label}) | "
                        f"{item.subject_name} {item.side} {item.line:.1f} {prop_label}"
                    ),
                    "value": _pitcher_prop_embed_value(presentation),
                    "inline": False,
                }
            )

    return [embed]


def _pitcher_prop_embed_value(presentation: PitcherPresentation) -> str:
    item = presentation.candidate
    flags = ", ".join(_display_flag(flag) for flag in item.flags[:6]) or "-"
    opportunity_flags = "-"
    if item.opportunity_shadow is not None:
        opportunity_flags = (
            ", ".join(_display_flag(flag) for flag in item.opportunity_shadow.flags[:4])
            or "None flagged"
        )
    projected = item.projected_outs if item.prop_type == "PITCHER_OUTS_RECORDED" else item.projected_strikeouts
    return (
        f"`{item.team}` vs `{item.opponent}` | Model `{projected:.1f}` "
        f"| Side edge `{side_projection_edge(item):+.1f}` | Workload `{presentation.opportunity_reliability}`\n"
        f"Outs `{item.projected_outs:.1f}` | BF `{item.projected_batters_faced:.1f}` "
        f"| KRate `{item.projected_k_rate:.3f}` | L5 `{item.hits_last_5}/{item.played_last_5}` "
        f"| L10 `{item.hits_last_10}/{item.played_last_10}`\n"
        f"Match `{item.matchup_rating:+.2f}`\n"
        f"Signals: {flags}\n"
        f"Opportunity flags: {opportunity_flags}"
    )


def _pitcher_prop_label(item: Candidate) -> str:
    if item.prop_type == "PITCHER_OUTS_RECORDED":
        return "Outs"
    return "Ks"


def _pitcher_props_embed_color(core: list[Candidate], watch: list[Candidate], coverage_status: str) -> int:
    if coverage_status == "source_failed":
        return 0xE74C3C
    if core:
        return 0x2ECC71
    if watch:
        return 0xF1C40F
    return 0x747F8D


def render_hot_hit_candidates(candidates: Iterable[HotHitCandidate], limit: int = 30) -> str:
    items = list(candidates)[:limit]
    headers = [
        "Batter",
        "Team",
        "Opp",
        "Pos",
        "BO",
        "Bat",
        "Pitcher",
        "PHand",
        "Score",
        "AVG L5",
        "AVG L10",
        "AVG Szn",
        "OBP L5",
        "HitG L5",
        "HitG L10",
        "H/AB L5",
        "P HRate L5",
        "P HRate Szn",
        "P KRate",
        "P BBRate",
        "BvP",
        "Match",
        "xBA10",
        "xBA25",
        "HH25",
        "Est1+H",
        "CQ",
        "Flags",
    ]
    rows: list[list[str]] = []
    for item in items:
        contact = item.contact_quality_shadow
        rows.append(
            [
                item.batter_name,
                item.team,
                item.opponent,
                item.position or "-",
                str(item.batting_order) if item.batting_order is not None else "-",
                item.bat_side or "-",
                item.probable_pitcher,
                item.pitcher_hand or "-",
                str(item.score),
                f"{item.avg_last_5:.3f}",
                f"{item.avg_last_10:.3f}",
                f"{item.season_avg:.3f}",
                f"{item.obp_last_5:.3f}",
                f"{item.hit_games_last_5}/5",
                f"{item.hit_games_last_10}/10",
                f"{item.hits_last_5}/{item.at_bats_last_5}",
                f"{item.pitcher_hits_allowed_rate_last_5:.3f}",
                f"{item.pitcher_hits_allowed_rate_season:.3f}",
                f"{item.pitcher_k_rate_last_5:.3f}",
                f"{item.pitcher_walk_rate_last_5:.3f}",
                _display_bvp(item),
                f"{item.matchup_rating:+.2f}",
                f"{contact.xba_last_10_games:.3f}" if contact and contact.xba_last_10_games is not None else "-",
                f"{contact.xba_last_25_bbe:.3f}" if contact and contact.xba_last_25_bbe is not None else "-",
                f"{contact.hard_hit_rate_last_25_bbe:.1%}" if contact and contact.hard_hit_rate_last_25_bbe is not None else "-",
                f"{contact.estimated_one_hit_probability:.1%}" if contact and contact.estimated_one_hit_probability is not None else "-",
                contact.confidence.title() if contact else "-",
                ",".join(_display_flag(flag) for flag in item.flags),
            ]
        )

    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    output = ["Hot Hit Candidates:"]
    output.append("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    output.append("  ".join("-" * widths[index] for index in range(len(headers))))
    for row in rows:
        output.append("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))
    if not rows:
        output.append("No hitters qualified for the current hot-hits model.")
    output.append("")
    output.append(
        "Hot hits legend: AVG L5 is hits divided by at-bats over the last five games; "
        "HitG is games with at least one hit; P HRate/KRate/BBRate are the probable starter's recent rates by batter faced; "
        "BvP is batter history versus today's probable starter when MLB Stats API exposes it. "
        "xBA10 includes tracked contact plus strikeouts over the last ten games; xBA25 is contact-only over the last 25 tracked batted balls; "
        "HH25 is hard-hit rate and Est1+H is an uncalibrated shadow estimate, not a sportsbook probability."
    )
    return "\n".join(output)


def render_hot_hit_confidence_research(
    candidates: Iterable[HotHitCandidate],
    limit: int = 30,
) -> str:
    items = [item for item in candidates if item.confidence_estimate is not None][:limit]
    headers = [
        "Rank",
        "Batter",
        "Team",
        "BO",
        "Hit Conf",
        "Label",
        "Reliab",
        "PerAB",
        "ExpAB",
        "xBA10",
        "xBA Szn",
        "Current",
        "Current Gate",
    ]
    rows: list[list[str]] = []
    for rank, item in enumerate(items, start=1):
        estimate = item.confidence_estimate
        contact = item.contact_quality_shadow
        if estimate is None:
            continue
        current_status = (
            policy_hot_hit_tier(item)
            if item.current_display_qualified
            else ("Below score" if item.current_gate_qualified else "Research")
        )
        rows.append(
            [
                f"#{rank}",
                item.batter_name,
                item.team,
                str(item.batting_order) if item.batting_order is not None else "-",
                f"{estimate.confidence_percentage}%",
                estimate.label.title(),
                f"{estimate.reliability_weight:.2f}",
                f"{estimate.per_at_bat_probability:.3f}",
                f"{estimate.expected_at_bats:.2f}",
                f"{contact.xba_last_10_games:.3f}"
                if contact and contact.xba_last_10_games is not None
                else "-",
                f"{contact.season_xba:.3f}"
                if contact and contact.season_xba is not None
                else "-",
                current_status,
                ",".join(item.gate_failures) or "Pass",
            ]
        )

    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    output = ["Hot Hits Confidence Research (shadow only):"]
    output.append("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    output.append("  ".join("-" * widths[index] for index in range(len(headers))))
    for row in rows:
        output.append("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))
    if not rows:
        output.append("No hitters qualified for the broader confidence research pool.")
    output.append("")
    output.append(
        "Confidence is provisional, price-agnostic, and excluded from score, tier, and Discord selection. "
        "Current Gate explains why a research profile is absent from the production board."
    )
    return "\n".join(output)


def render_hot_hits_discord_digest(
    candidates: Iterable[HotHitCandidate],
    screen_date,
    limit: int = 8,
) -> str:
    items = list(candidates)[:limit]
    lines = [f"MLB Hot Hits - {screen_date}", ""]
    if not items:
        lines.append("No hot-hit candidates qualified for the current thresholds.")
        return "\n".join(lines)

    for index, item in enumerate(items, start=1):
        flags = ", ".join(_display_flag(flag) for flag in item.flags[:6]) or "-"
        bvp = _display_bvp(item)
        bvp_text = f" | BvP {bvp}" if bvp != "-" else ""
        lines.extend(
            [
                f"{index}. {item.batter_name} ({item.team}) vs {item.probable_pitcher} ({item.pitcher_hand or '?'})",
                (
                    f"   Score {item.score} | L5 {item.avg_last_5:.3f} "
                    f"({item.hits_last_5}/{item.at_bats_last_5}, hit games {item.hit_games_last_5}/5) "
                    f"| Szn {item.season_avg:.3f}"
                ),
                (
                    f"   Pitcher HRate {item.pitcher_hits_allowed_rate_last_5:.3f} "
                    f"| Match {item.matchup_rating:+.2f}{bvp_text}"
                ),
                f"   Flags: {flags}",
            ]
        )
    return "\n".join(lines)


def render_hot_hits_discord_embeds(
    candidates: Iterable[HotHitCandidate],
    screen_date,
    games_count: int,
    checked_count: int,
    limit: int = 4,
    min_score: int = 10,
    card_policy: str = CORE_FIRST_POLICY_VERSION,
    value_limit: int = 2,
) -> list[dict]:
    items = list(candidates)
    selection = select_hot_hits_card(
        items,
        card_policy=card_policy,
        limit=limit,
        value_limit=value_limit,
        min_score=min_score,
    )
    if card_policy == HOT_HITS_POLICY_VERSION:
        return _render_legacy_hot_hits_discord_embed(
            items=items,
            selection=selection,
            screen_date=screen_date,
            games_count=games_count,
            checked_count=checked_count,
            min_score=min_score,
        )
    shown_core = selection.core
    shown_value = selection.value
    shown = selection.shown
    if shown_core:
        core_label = "leg" if len(shown_core) == 1 else "legs"
        recommendation = f"**{len(shown_core)} Core {core_label}**"
    else:
        recommendation = "**No Core play today**"
    description = (
        f"{len(items)} qualified from {checked_count} likely bats across {games_count} games.\n"
        f"Recommended card: {recommendation}. "
        "Only Core names belong in the recommended parlay."
    )
    embed = {
        "title": f"MLB Hot Hits - {screen_date}",
        "description": description,
        "color": _hot_hits_embed_color(shown),
        "fields": [],
        "footer": {"text": "Confirm lineup spot before locking. Odds are a mental model here, not pulled into scoring."},
    }

    if not shown_core:
        embed["fields"].append(
            {
                "name": "Core Card",
                "value": "No Core plays qualified today. Do not force a recommended parlay.",
                "inline": False,
            }
        )

    for index, item in enumerate(shown_core, start=1):
        embed["fields"].append(
            {
                "name": f"Core Card {index} | {item.batter_name} ({item.team}) - Score {item.score}",
                "value": _hot_hit_embed_value(item),
                "inline": False,
            }
        )

    if shown_value:
        embed["fields"].append(
            {
                "name": "⚠️ Optional Value — Higher Risk",
                "value": (
                    "Not part of the recommended Core card. These are fallback research plays only. "
                    "**Play at your own risk.**"
                ),
                "inline": False,
            }
        )

    for item in shown_value:
        embed["fields"].append(
            {
                "name": f"Optional Value | {item.batter_name} ({item.team}) - Score {item.score}",
                "value": _hot_hit_embed_value(item),
                "inline": False,
            }
        )

    if not shown:
        embed["fields"].append(
            {
                "name": "Optional Value",
                "value": f"No supported Value fallback cleared the profile cutoff of {min_score}.",
                "inline": False,
            }
        )

    return [embed]


def _render_legacy_hot_hits_discord_embed(
    *,
    items: list[HotHitCandidate],
    selection,
    screen_date,
    games_count: int,
    checked_count: int,
    min_score: int,
) -> list[dict]:
    shown = selection.shown
    embed = {
        "title": f"MLB Hot Hits - {screen_date}",
        "description": (
            f"{len(items)} qualified from {checked_count} likely bats across {games_count} games.\n"
            "Core: safer hit profile. Value: supported hot hand. "
            "Thin: needs lineup and context help."
        ),
        "color": _hot_hits_embed_color(shown),
        "fields": [],
        "footer": {
            "text": "Confirm lineup spot before locking. Odds are a mental model here, not pulled into scoring."
        },
    }
    if not shown:
        embed["fields"].append(
            {
                "name": "No Discord plays",
                "value": f"No hitters cleared the Discord score/profile cutoff of {min_score}.",
                "inline": False,
            }
        )
        return [embed]

    for tier_name, candidates in (
        ("Core", selection.core),
        ("Value", selection.value),
        ("Thin", selection.thin),
    ):
        for item in candidates:
            embed["fields"].append(
                {
                    "name": f"{tier_name} | {item.batter_name} ({item.team}) - Score {item.score}",
                    "value": _hot_hit_embed_value(item),
                    "inline": False,
                }
            )
    return [embed]


def _hot_hit_tier(item: HotHitCandidate) -> str:
    return policy_hot_hit_tier(item)


def _hot_hit_discord_eligible(item: HotHitCandidate, min_score: int) -> bool:
    return policy_hot_hit_discord_eligible(item, min_score=min_score)


def _hot_hit_support_count(item: HotHitCandidate) -> int:
    return policy_hot_hit_support_count(item)


def _hot_hit_discord_sort_key(item: HotHitCandidate) -> tuple[int, int, int, float, float, float, int]:
    return policy_hot_hit_discord_sort_key(item)


def _hot_hit_embed_value(item: HotHitCandidate) -> str:
    bvp = _display_bvp(item)
    bvp_text = f" | BvP `{bvp}`" if bvp != "-" else ""
    pitcher_context = (
        "Pitcher: no recent data"
        if item.pitcher_hits_allowed_rate_last_5 <= 0 and item.pitcher_k_rate_last_5 <= 0
        else f"Pitcher HRate `{item.pitcher_hits_allowed_rate_last_5:.3f}` | KRate `{item.pitcher_k_rate_last_5:.3f}`"
    )
    matchup = f"Match `{item.matchup_rating:+.2f}`" if item.matchup_rating else "Match `+0.00`"
    return (
        f"vs **{item.probable_pitcher}** ({item.pitcher_hand or '?'}) | BO `{item.batting_order or '-'}`\n"
        f"L5 `{item.avg_last_5:.3f}` ({item.hits_last_5}/{item.at_bats_last_5}, {item.hit_games_last_5}/5 games) "
        f"| L10 `{item.avg_last_10:.3f}` | Szn `{item.season_avg:.3f}`\n"
        f"{pitcher_context} | {matchup}{bvp_text}"
    )


def _hot_hits_embed_color(items: list[HotHitCandidate]) -> int:
    if not items:
        return 0x747F8D
    if items[0].score >= 14:
        return 0x2ECC71
    if items[0].score >= 10:
        return 0xF1C40F
    return 0x95A5A6


def _render_table(presentations: list[PitcherPresentation]) -> list[str]:
    headers = [
        "Rank",
        "Tier",
        "Pitcher",
        "Team",
        "Opp",
        "Prop",
        "Side",
        "Line",
        "Proj",
        "Side Edge",
        "Proj Outs",
        "Proj BF",
        "Conf",
        "Conf Label",
        "Signal",
        "Work Rel",
        "L5 Hit",
        "L10 Hit",
        "Avg L5",
        "Avg L10",
        "Med L10",
        "Pitch L5",
        "Outs L5",
        "KRate",
        "BBRate",
        "Stab",
        "Match",
        "Flags",
    ]
    rows: list[list[str]] = []
    for presentation in presentations:
        candidate = presentation.candidate
        projected_value = candidate.projected_strikeouts if candidate.prop_type == "PITCHER_STRIKEOUTS" else candidate.projected_outs
        rows.append(
            [
                f"#{presentation.slate_rank}",
                presentation.recommendation_tier.title(),
                candidate.subject_name,
                candidate.team,
                candidate.opponent,
                _display_prop_name(candidate.prop_type),
                candidate.side,
                f"{candidate.line:.1f}",
                f"{projected_value:.1f}",
                f"{side_projection_edge(candidate):+.2f}",
                f"{candidate.projected_outs:.1f}",
                f"{candidate.projected_batters_faced:.1f}",
                f"{presentation.confidence_estimate.confidence_percentage}%",
                presentation.confidence_estimate.label.title(),
                f"{candidate.score:+d}",
                presentation.opportunity_reliability,
                f"{candidate.hits_last_5}/{candidate.played_last_5}",
                f"{candidate.hits_last_10}/{candidate.played_last_10}",
                f"{candidate.avg_last_5:.1f}",
                f"{candidate.avg_last_10:.1f}",
                f"{candidate.median_last_10:.1f}",
                f"{candidate.avg_pitch_count_last_5:.1f}",
                f"{candidate.avg_outs_last_5:.1f}",
                f"{candidate.avg_k_rate_last_5:.3f}",
                f"{candidate.avg_walk_rate_last_5:.3f}",
                f"{candidate.workload_stability:.2f}",
                f"{candidate.matchup_rating:+.2f}",
                ",".join(_display_flag(flag) for flag in candidate.flags),
            ]
        )

    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    output = []
    output.append("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    output.append("  ".join("-" * widths[index] for index in range(len(headers))))
    for row in rows:
        output.append("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))
    if not rows:
        output.append("No pitcher props cleared the Core/Lean/Watch display floor.")
    return output


def _starter_side_edge(item: StarterAssessment) -> float:
    edge = float(item.lean_edge or 0.0)
    return -edge if item.lean_side == "UNDER" else edge


def _display_prop_name(prop_type: str) -> str:
    if prop_type == "PITCHER_STRIKEOUTS":
        return "Ks"
    if prop_type == "PITCHER_OUTS_RECORDED":
        return "Outs"
    return prop_type


def _display_flag(flag: str) -> str:
    return FLAG_LABELS.get(flag, flag)


def _display_shortlist_status(item: StarterAssessment) -> str:
    status = item.shortlist_status or "-"
    reason = item.shortlist_reason
    if not reason:
        return status
    if status in {"Qualified", "NoLine", "NoData"}:
        return status
    return f"{status}:{reason}"


def _display_bvp(item: HotHitCandidate) -> str:
    if item.batter_vs_pitcher_avg is None or item.batter_vs_pitcher_ab is None:
        return "-"
    return f"{item.batter_vs_pitcher_avg:.3f}/{item.batter_vs_pitcher_ab}AB"
