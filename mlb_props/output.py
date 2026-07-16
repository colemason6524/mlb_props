from __future__ import annotations

from collections.abc import Iterable

from .models import Candidate, HotHitCandidate, StarterAssessment


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
    all_candidates = list(candidates)
    core_candidates = sorted(
        (candidate for candidate in all_candidates if candidate.score >= min_score),
        key=lambda item: (item.score, abs(item.projected_strikeouts - item.line), item.hits_last_5, item.delta_avg_last_5),
        reverse=True,
    )
    lean_candidates = sorted(
        (candidate for candidate in all_candidates if lean_min_score <= candidate.score < min_score),
        key=lambda item: (item.score, abs(item.projected_strikeouts - item.line), item.hits_last_5, item.delta_avg_last_5),
        reverse=True,
    )
    watch_candidates = sorted(
        (candidate for candidate in all_candidates if watch_min_score <= candidate.score < lean_min_score),
        key=lambda item: (item.score, abs(item.projected_strikeouts - item.line), item.hits_last_5, item.delta_avg_last_5),
        reverse=True,
    )

    sections: list[tuple[str, list[Candidate]]] = []
    if core_candidates:
        sections.append(("Core Plays", core_candidates[:limit]))
    if lean_candidates:
        sections.append(("Leans", lean_candidates[:limit]))
    if watch_candidates:
        sections.append(("Watchlist", watch_candidates[:limit]))
    if not sections:
        sections.append(("Candidates", []))

    rendered_sections: list[str] = []
    for title, items in sections:
        rendered_sections.append(f"{title}:")
        rendered_sections.extend(_render_table(items))
        rendered_sections.append("")

    rendered_sections.append(
        "Legend: Proj = today's projected value for that prop; Proj Outs/BF = projected outs and batters faced opportunity; "
        "Edge = projected value minus posted line; L5/L10 Hit = side-specific recent hit counts; "
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
        "Edge",
        "Score",
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
                f"{item.lean_edge:+.2f}" if item.lean_edge is not None else "-",
                str(item.lean_score) if item.lean_score is not None else "-",
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
        "Edge is the raw gap between Projected Ks and the posted line; Score uses the same strikeout over/under scoring logic as the shortlist; "
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
    items = list(candidates)
    sorted_items = sorted(
        items,
        key=lambda item: (item.score, abs(item.projected_strikeouts - item.line), item.projected_outs),
        reverse=True,
    )
    core = [item for item in sorted_items if item.score >= min_score][:core_limit]
    watch = [
        item
        for item in sorted_items
        if watch_min_score <= item.score < min_score
    ][:watch_limit]

    description = (
        f"{prop_line_count} prop lines across {games_count} games. "
        f"Coverage `{coverage_status}`. "
        "Core is the stricter board; Watchlist is five names worth monitoring, not auto-plays."
    )
    embed = {
        "title": f"MLB Pitcher Props - {screen_date}",
        "description": description,
        "color": _pitcher_props_embed_color(core, watch, coverage_status),
        "fields": [],
        "footer": {"text": "Confirm line availability and game status before locking. Saved by scheduled pregame run."},
    }

    if not core:
        embed["fields"].append(
            {
                "name": "Core Plays",
                "value": "No core plays cleared today's score threshold.",
                "inline": False,
            }
        )
    else:
        for item in core:
            prop_label = _pitcher_prop_label(item)
            embed["fields"].append(
                {
                    "name": f"Core | {item.subject_name} {item.side} {item.line:.1f} {prop_label} - Score {item.score}",
                    "value": _pitcher_prop_embed_value(item),
                    "inline": False,
                }
            )

    if not watch:
        embed["fields"].append(
            {
                "name": "Five To Watch",
                "value": "No watchlist names cleared the display floor.",
                "inline": False,
            }
        )
    else:
        for item in watch:
            label = "Lean" if item.score >= lean_min_score else "Watch"
            prop_label = _pitcher_prop_label(item)
            embed["fields"].append(
                {
                    "name": f"{label} | {item.subject_name} {item.side} {item.line:.1f} {prop_label} - Score {item.score}",
                    "value": _pitcher_prop_embed_value(item),
                    "inline": False,
                }
            )

    return [embed]


def _pitcher_prop_embed_value(item: Candidate) -> str:
    flags = ", ".join(_display_flag(flag) for flag in item.flags[:6]) or "-"
    projected = item.projected_outs if item.prop_type == "PITCHER_OUTS_RECORDED" else item.projected_strikeouts
    return (
        f"`{item.team}` vs `{item.opponent}` | Proj `{projected:.1f}` "
        f"| Edge `{projected - item.line:+.1f}` | Match `{item.matchup_rating:+.2f}`\n"
        f"Outs `{item.projected_outs:.1f}` | BF `{item.projected_batters_faced:.1f}` "
        f"| KRate `{item.projected_k_rate:.3f}` | L5 `{item.hits_last_5}/{item.played_last_5}` "
        f"| L10 `{item.hits_last_10}/{item.played_last_10}`\n"
        f"Flags: {flags}"
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
        "Flags",
    ]
    rows: list[list[str]] = []
    for item in items:
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
        "BvP is batter history versus today's probable starter when MLB Stats API exposes it."
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
    limit: int = 8,
    min_score: int = 10,
) -> list[dict]:
    items = list(candidates)
    eligible = [item for item in items if _hot_hit_discord_eligible(item, min_score)]
    eligible = sorted(eligible, key=_hot_hit_discord_sort_key, reverse=True)
    core = [item for item in eligible if _hot_hit_tier(item) == "Core"]
    value = [item for item in eligible if _hot_hit_tier(item) == "Value"]
    thin = [item for item in eligible if _hot_hit_tier(item) == "Thin"]
    shown_core = core[:limit]
    shown_value = value[: max(0, limit - len(shown_core))]
    shown_thin = thin[: max(0, limit - len(shown_core) - len(shown_value))]
    shown = shown_core + shown_value + shown_thin
    description = (
        f"{len(items)} qualified from {checked_count} likely bats across {games_count} games.\n"
        f"Core: safer hit profile. Value: hot hand with potentially softer market profile. Thin: needs price/lineup help."
    )
    embed = {
        "title": f"MLB Hot Hits - {screen_date}",
        "description": description,
        "color": _hot_hits_embed_color(shown),
        "fields": [],
        "footer": {"text": "Confirm lineup spot before locking. Odds are a mental model here, not pulled into scoring."},
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

    for item in shown_core:
        embed["fields"].append(
            {
                "name": f"Core | {item.batter_name} ({item.team}) - Score {item.score}",
                "value": _hot_hit_embed_value(item),
                "inline": False,
            }
        )

    for item in shown_value:
        embed["fields"].append(
            {
                "name": f"Value | {item.batter_name} ({item.team}) - Score {item.score}",
                "value": _hot_hit_embed_value(item),
                "inline": False,
            }
        )

    for item in shown_thin:
        embed["fields"].append(
            {
                "name": f"Thin | {item.batter_name} ({item.team}) - Score {item.score}",
                "value": _hot_hit_embed_value(item),
                "inline": False,
            }
        )

    return [embed]


def _hot_hit_tier(item: HotHitCandidate) -> str:
    batting_order = item.batting_order or 99
    low_order = batting_order >= 8
    value_order = 5 <= batting_order <= 7
    matchup_floor = item.matchup_rating > -0.20
    strong_contact_spot = item.pitcher_hits_allowed_rate_last_5 >= 0.280 or item.matchup_rating >= 0.20
    hot_hand = item.avg_last_5 >= 0.400 or item.hit_games_last_5 >= 5
    support_count = _hot_hit_support_count(item)

    if item.score >= 14 and batting_order <= 4 and matchup_floor and support_count >= 2:
        return "Core"
    if item.score >= 12 and hot_hand and matchup_floor and not low_order and support_count >= 2:
        return "Value"
    if item.score >= 11 and value_order and hot_hand and strong_contact_spot and support_count >= 2:
        return "Value"
    return "Thin"


def _hot_hit_discord_eligible(item: HotHitCandidate, min_score: int) -> bool:
    batting_order = item.batting_order or 99
    support_count = _hot_hit_support_count(item)
    if item.score >= 14 and support_count >= 1 and batting_order <= 7:
        return True
    if item.score >= max(min_score, 12) and support_count >= 2 and batting_order <= 7:
        return True
    return False


def _hot_hit_support_count(item: HotHitCandidate) -> int:
    return sum(
        [
            bool(item.batting_order is not None and item.batting_order <= 4),
            item.matchup_rating >= 0.20,
            item.pitcher_hits_allowed_rate_last_5 >= 0.260,
            item.season_avg >= 0.280,
            item.avg_last_5 >= 0.380,
        ]
    )


def _hot_hit_discord_sort_key(item: HotHitCandidate) -> tuple[int, int, int, float, float, float, int]:
    batting_order = item.batting_order or 99
    return (
        _hot_hit_support_count(item),
        item.score,
        1 if batting_order <= 4 else 0,
        item.matchup_rating,
        item.pitcher_hits_allowed_rate_last_5,
        item.season_avg,
        -batting_order,
    )


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


def _render_table(candidates: list[Candidate]) -> list[str]:
    headers = [
        "Pitcher",
        "Team",
        "Opp",
        "Prop",
        "Side",
        "Line",
        "Proj",
        "Edge",
        "Proj Outs",
        "Proj BF",
        "Score",
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
    for candidate in candidates:
        projected_value = candidate.projected_strikeouts if candidate.prop_type == "PITCHER_STRIKEOUTS" else candidate.projected_outs
        rows.append(
            [
                candidate.subject_name,
                candidate.team,
                candidate.opponent,
                _display_prop_name(candidate.prop_type),
                candidate.side,
                f"{candidate.line:.1f}",
                f"{projected_value:.1f}",
                f"{projected_value - candidate.line:+.2f}",
                f"{candidate.projected_outs:.1f}",
                f"{candidate.projected_batters_faced:.1f}",
                str(candidate.score),
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
        output.append("No pitcher props qualified for the current model and score threshold.")
    return output


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
