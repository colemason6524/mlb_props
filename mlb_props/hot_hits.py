from __future__ import annotations

from collections.abc import Callable, Iterable
from .config import HotHitsThresholds, Settings
from .hot_hits_policy import HotHitScoringInput, score_hot_hit_candidate
from .models import BatterGameLog, BatterPitcherHistory, Game, HotHitCandidate, PitcherGameLog
from .sources.mlb_stats_api import ProjectedBatter
from .utils import normalize_name


def screen_hot_hitters(
    settings: Settings,
    games: Iterable[Game],
    projected_batters: Iterable[ProjectedBatter],
    logs_by_batter: dict[str, list[BatterGameLog]],
    logs_by_pitcher: dict[str, list[PitcherGameLog]],
    batter_pitcher_history: Callable[[int | None, int | None], BatterPitcherHistory | None] | None = None,
) -> list[HotHitCandidate]:
    thresholds = settings.hot_hits_thresholds
    starter_by_team = _starter_by_opponent_team(games)
    candidates: list[HotHitCandidate] = []

    for batter in projected_batters:
        starter = starter_by_team.get(batter.team)
        if starter is None:
            continue

        logs = logs_by_batter.get(normalize_name(batter.name), [])
        logs = [log for log in logs if log.at_bats > 0]
        if len(logs) < thresholds.min_games:
            continue

        last_5 = logs[: thresholds.recent_window]
        last_10 = logs[: thresholds.form_window]
        if sum(log.at_bats for log in last_5) < thresholds.min_recent_at_bats:
            continue

        avg_last_5 = _batting_average(last_5)
        avg_last_10 = _batting_average(last_10)
        season_avg = _batting_average(logs)
        hit_games_last_5 = sum(1 for log in last_5 if log.hits > 0)
        hit_games_last_10 = sum(1 for log in last_10 if log.hits > 0)

        if not _passes_hot_gate(
            thresholds=thresholds,
            avg_last_5=avg_last_5,
            season_avg=season_avg,
            hit_games_last_5=hit_games_last_5,
        ):
            continue

        pitcher_logs = [
            log
            for log in logs_by_pitcher.get(normalize_name(starter["pitcher_name"]), [])
            if log.did_start
        ]
        pitcher_last_5 = pitcher_logs[:5]
        pitcher_hand = _pitcher_hand(starter, pitcher_logs)
        bvp = batter_pitcher_history(batter.player_id, starter["pitcher_id"]) if batter_pitcher_history else None
        matchup_rating = _matchup_rating(pitcher_logs=pitcher_logs, pitcher_last_5=pitcher_last_5, bvp=bvp)
        score, flags = _score_candidate(
            thresholds=thresholds,
            batter=batter,
            avg_last_5=avg_last_5,
            avg_last_10=avg_last_10,
            season_avg=season_avg,
            hit_games_last_5=hit_games_last_5,
            hit_games_last_10=hit_games_last_10,
            pitcher_logs=pitcher_logs,
            pitcher_last_5=pitcher_last_5,
            bvp=bvp,
            matchup_rating=matchup_rating,
        )

        if score < thresholds.min_display_score:
            continue

        candidates.append(
            HotHitCandidate(
                batter_name=batter.name,
                batter_id=batter.player_id,
                team=batter.team,
                opponent=batter.opponent,
                bat_side=next((log.bat_side for log in logs if log.bat_side), ""),
                position=batter.position or next((log.position for log in logs if log.position), ""),
                batting_order=batter.batting_order,
                probable_pitcher=starter["pitcher_name"],
                probable_pitcher_id=starter["pitcher_id"],
                pitcher_hand=pitcher_hand,
                games_played=len(logs),
                avg_last_5=avg_last_5,
                avg_last_10=avg_last_10,
                season_avg=season_avg,
                obp_last_5=_on_base_percentage(last_5),
                hit_games_last_5=hit_games_last_5,
                hit_games_last_10=hit_games_last_10,
                at_bats_last_5=sum(log.at_bats for log in last_5),
                hits_last_5=sum(log.hits for log in last_5),
                hits_last_10=sum(log.hits for log in last_10),
                season_hits=sum(log.hits for log in logs),
                season_at_bats=sum(log.at_bats for log in logs),
                pitcher_hits_allowed_rate_last_5=_pitcher_hits_allowed_rate(pitcher_last_5),
                pitcher_hits_allowed_rate_season=_pitcher_hits_allowed_rate(pitcher_logs),
                pitcher_k_rate_last_5=_pitcher_k_rate(pitcher_last_5),
                pitcher_walk_rate_last_5=_pitcher_walk_rate(pitcher_last_5),
                batter_vs_pitcher_avg=bvp.batting_average if bvp else None,
                batter_vs_pitcher_ab=bvp.at_bats if bvp else None,
                matchup_rating=matchup_rating,
                score=score,
                flags=flags,
            )
        )

    return sorted(
        candidates,
        key=lambda item: (
            item.score,
            item.avg_last_5,
            item.hit_games_last_5,
            item.matchup_rating,
            item.season_avg,
        ),
        reverse=True,
    )


def _starter_by_opponent_team(games: Iterable[Game]) -> dict[str, dict]:
    starters: dict[str, dict] = {}
    for game in games:
        starters[game.away_team] = {
            "pitcher_name": game.probable_home_pitcher,
            "pitcher_id": game.probable_home_pitcher_id,
            "pitcher_team": game.home_team,
        }
        starters[game.home_team] = {
            "pitcher_name": game.probable_away_pitcher,
            "pitcher_id": game.probable_away_pitcher_id,
            "pitcher_team": game.away_team,
        }
    return {team: starter for team, starter in starters.items() if starter["pitcher_name"]}


def _passes_hot_gate(
    thresholds: HotHitsThresholds,
    avg_last_5: float,
    season_avg: float,
    hit_games_last_5: int,
) -> bool:
    return (
        avg_last_5 >= thresholds.hot_avg_min
        and season_avg >= thresholds.min_season_avg
        and hit_games_last_5 >= thresholds.min_recent_hit_games
    )


def _score_candidate(
    thresholds: HotHitsThresholds,
    batter: ProjectedBatter,
    avg_last_5: float,
    avg_last_10: float,
    season_avg: float,
    hit_games_last_5: int,
    hit_games_last_10: int,
    pitcher_logs: list[PitcherGameLog],
    pitcher_last_5: list[PitcherGameLog],
    bvp: BatterPitcherHistory | None,
    matchup_rating: float,
) -> tuple[int, list[str]]:
    pitcher_hit_rate_recent = _pitcher_hits_allowed_rate(pitcher_last_5)
    result = score_hot_hit_candidate(
        HotHitScoringInput(
            avg_last_5=avg_last_5,
            avg_last_10=avg_last_10,
            season_avg=season_avg,
            hit_games_last_5=hit_games_last_5,
            hit_games_last_10=hit_games_last_10,
            batting_order=batter.batting_order,
            matchup_rating=matchup_rating,
            pitcher_hits_allowed_rate_last_5=pitcher_hit_rate_recent,
            pitcher_k_rate_last_5=_pitcher_k_rate(pitcher_last_5),
            pitcher_walk_rate_last_5=_pitcher_walk_rate(pitcher_last_5),
            pitcher_has_data=bool(pitcher_logs),
            batter_vs_pitcher_ab=bvp.at_bats if bvp is not None else 0,
            batter_vs_pitcher_avg=bvp.batting_average if bvp is not None else None,
            batter_vs_pitcher_available=bvp is not None,
        ),
        strong_hot_avg=thresholds.strong_hot_avg,
    )
    return result.score, result.flags


def _matchup_rating(
    pitcher_logs: list[PitcherGameLog],
    pitcher_last_5: list[PitcherGameLog],
    bvp: BatterPitcherHistory | None,
) -> float:
    if not pitcher_logs:
        return 0.0
    hit_rate_recent = _pitcher_hits_allowed_rate(pitcher_last_5)
    hit_rate_season = _pitcher_hits_allowed_rate(pitcher_logs)
    k_rate_recent = _pitcher_k_rate(pitcher_last_5)
    walk_rate_recent = _pitcher_walk_rate(pitcher_last_5)
    rating = ((hit_rate_recent - 0.250) * 3.0) + ((hit_rate_season - 0.245) * 2.0)
    rating -= (k_rate_recent - 0.225) * 1.8
    rating -= max(0.0, walk_rate_recent - 0.085) * 1.2
    if bvp is not None and bvp.at_bats >= 6 and bvp.batting_average is not None:
        rating += (bvp.batting_average - 0.250) * 0.35
    return round(rating, 2)


def _batting_average(logs: list[BatterGameLog]) -> float:
    at_bats = sum(log.at_bats for log in logs)
    if at_bats <= 0:
        return 0.0
    return round(sum(log.hits for log in logs) / at_bats, 3)


def _on_base_percentage(logs: list[BatterGameLog]) -> float:
    plate_appearances = sum(log.plate_appearances for log in logs)
    if plate_appearances <= 0:
        return 0.0
    return round((sum(log.hits for log in logs) + sum(log.walks for log in logs)) / plate_appearances, 3)


def _pitcher_hits_allowed_rate(logs: list[PitcherGameLog]) -> float:
    batters_faced = sum(log.batters_faced for log in logs)
    if batters_faced <= 0:
        return 0.0
    return round(sum(log.hits_allowed for log in logs) / batters_faced, 3)


def _pitcher_k_rate(logs: list[PitcherGameLog]) -> float:
    batters_faced = sum(log.batters_faced for log in logs)
    if batters_faced <= 0:
        return 0.0
    return round(sum(log.strikeouts for log in logs) / batters_faced, 3)


def _pitcher_walk_rate(logs: list[PitcherGameLog]) -> float:
    batters_faced = sum(log.batters_faced for log in logs)
    if batters_faced <= 0:
        return 0.0
    return round(sum(log.walks for log in logs) / batters_faced, 3)


def _pitcher_hand(starter: dict, pitcher_logs: list[PitcherGameLog]) -> str:
    return next((log.hand for log in pitcher_logs if log.hand), "") or starter.get("pitcher_hand", "")
