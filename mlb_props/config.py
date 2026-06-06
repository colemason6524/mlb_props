from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"
CONFIG_DIR = ROOT / "config"
OUTPUTS_DIR = ROOT / "outputs"

PITCHER_STRIKEOUTS = "PITCHER_STRIKEOUTS"
PITCHER_OUTS_RECORDED = "PITCHER_OUTS_RECORDED"
BATTER_HITS = "BATTER_HITS"
SUPPORTED_PROP_TYPES = [PITCHER_STRIKEOUTS, PITCHER_OUTS_RECORDED]
ODDS_API_MARKETS = {
    PITCHER_STRIKEOUTS: "pitcher_strikeouts",
    PITCHER_OUTS_RECORDED: "pitcher_outs",
}
MARKET_TO_PROP = {value: key for key, value in ODDS_API_MARKETS.items()}

ODDS_API_TEAM_ABBR = {
    "Arizona Diamondbacks": "AZ",
    "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL",
    "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC",
    "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN",
    "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL",
    "Detroit Tigers": "DET",
    "Houston Astros": "HOU",
    "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA",
    "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA",
    "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN",
    "New York Mets": "NYM",
    "New York Yankees": "NYY",
    "Athletics": "ATH",
    "Oakland Athletics": "ATH",
    "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD",
    "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA",
    "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSH",
}

PARK_RUN_FACTORS = {
    "AZ": 1.01,
    "ATL": 1.03,
    "BAL": 0.97,
    "BOS": 1.02,
    "CHC": 1.01,
    "CWS": 1.03,
    "CIN": 1.09,
    "CLE": 0.98,
    "COL": 1.12,
    "DET": 0.97,
    "HOU": 0.99,
    "KC": 1.02,
    "LAA": 1.00,
    "LAD": 0.98,
    "MIA": 0.95,
    "MIL": 1.02,
    "MIN": 1.01,
    "NYM": 0.98,
    "NYY": 1.03,
    "ATH": 0.95,
    "PHI": 1.03,
    "PIT": 0.98,
    "SD": 0.96,
    "SF": 0.94,
    "SEA": 0.95,
    "STL": 0.99,
    "TB": 0.97,
    "TEX": 1.05,
    "TOR": 1.02,
    "WSH": 1.01,
}

TEAM_ABBR_TO_FANDUEL_SLUG = {
    "AZ": "arizona-diamondbacks",
    "ATL": "atlanta-braves",
    "BAL": "baltimore-orioles",
    "BOS": "boston-red-sox",
    "CHC": "chicago-cubs",
    "CWS": "chicago-white-sox",
    "CIN": "cincinnati-reds",
    "CLE": "cleveland-guardians",
    "COL": "colorado-rockies",
    "DET": "detroit-tigers",
    "HOU": "houston-astros",
    "KC": "kansas-city-royals",
    "LAA": "los-angeles-angels",
    "LAD": "los-angeles-dodgers",
    "MIA": "miami-marlins",
    "MIL": "milwaukee-brewers",
    "MIN": "minnesota-twins",
    "NYM": "new-york-mets",
    "NYY": "new-york-yankees",
    "ATH": "athletics",
    "PHI": "philadelphia-phillies",
    "PIT": "pittsburgh-pirates",
    "SD": "san-diego-padres",
    "SF": "san-francisco-giants",
    "SEA": "seattle-mariners",
    "STL": "st-louis-cardinals",
    "TB": "tampa-bay-rays",
    "TEX": "texas-rangers",
    "TOR": "toronto-blue-jays",
    "WSH": "washington-nationals",
}

TEAM_ABBR_TO_MLB_ID = {
    "AZ": 109,
    "ATL": 144,
    "BAL": 110,
    "BOS": 111,
    "CHC": 112,
    "CWS": 145,
    "CIN": 113,
    "CLE": 114,
    "COL": 115,
    "DET": 116,
    "HOU": 117,
    "KC": 118,
    "LAA": 108,
    "LAD": 119,
    "MIA": 146,
    "MIL": 158,
    "MIN": 142,
    "NYM": 121,
    "NYY": 147,
    "ATH": 133,
    "PHI": 143,
    "PIT": 134,
    "SD": 135,
    "SF": 137,
    "SEA": 136,
    "STL": 138,
    "TB": 139,
    "TEX": 140,
    "TOR": 141,
    "WSH": 120,
}


@dataclass
class Thresholds:
    min_starts: int = 5
    recent_window: int = 5
    form_window: int = 10
    primary_hits_last_5: int = 3
    support_hits_last_10: int = 6
    min_delta: float = 0.6
    strong_delta: float = 1.4
    low_pitch_count: int = 85
    strong_pitch_count: int = 95
    high_outs_volatility: float = 4.5
    high_k_volatility: float = 2.2
    minimum_innings_recent: float = 4.2


@dataclass
class HotHitsThresholds:
    min_games: int = 10
    recent_window: int = 5
    form_window: int = 10
    min_recent_at_bats: int = 12
    hot_avg_min: float = 0.350
    strong_hot_avg: float = 0.400
    min_season_avg: float = 0.240
    min_recent_hit_games: int = 4
    min_display_score: int = 7
    max_batters_per_team: int = 9
    include_bvp: bool = True
    discord_min_score: int = 10


@dataclass
class Settings:
    odds_api_key: str = ""
    odds_api_regions: str = "us"
    odds_api_bookmakers: list[str] = field(default_factory=lambda: ["fanduel", "draftkings"])
    screen_date: date = field(default_factory=date.today)
    cache_ttl_hours: int = 24
    lines_cache_ttl_minutes: int = 15
    supported_prop_types: list[str] = field(default_factory=lambda: SUPPORTED_PROP_TYPES.copy())
    thresholds: Thresholds = field(default_factory=Thresholds)
    hot_hits_thresholds: HotHitsThresholds = field(default_factory=HotHitsThresholds)
    player_aliases: dict[str, str] = field(default_factory=dict)
    include_under_candidates: bool = True
    export_history: bool = False
    min_display_score: int = 7
    display_limit: int = 30
    data_mode: str = "sample"
    line_source: str = "fanduel"


def load_settings() -> Settings:
    odds_api_key = os.environ.get("ODDS_API_KEY", "").strip()
    screen_date_str = os.environ.get("SCREEN_DATE", "").strip()
    screen_date = date.today()
    if screen_date_str:
        screen_date = datetime.strptime(screen_date_str, "%Y-%m-%d").date()

    bookmakers_str = os.environ.get("ODDS_API_BOOKMAKERS", "fanduel,draftkings").strip()
    bookmakers = [item.strip() for item in bookmakers_str.split(",") if item.strip()]

    prop_types_str = os.environ.get("SCREEN_PROP_TYPES", "").strip()
    supported_prop_types = SUPPORTED_PROP_TYPES.copy()
    if prop_types_str:
        requested = [item.strip().upper() for item in prop_types_str.split(",") if item.strip()]
        supported_prop_types = [item for item in SUPPORTED_PROP_TYPES if item in requested]

    aliases_path = CONFIG_DIR / "player_aliases.json"
    aliases = {}
    if aliases_path.exists():
        aliases = json.loads(aliases_path.read_text())

    return Settings(
        odds_api_key=odds_api_key,
        odds_api_regions=os.environ.get("ODDS_API_REGIONS", "us").strip(),
        odds_api_bookmakers=bookmakers,
        screen_date=screen_date,
        cache_ttl_hours=int(os.environ.get("CACHE_TTL_HOURS", "24")),
        lines_cache_ttl_minutes=int(os.environ.get("LINES_CACHE_TTL_MINUTES", "15")),
        supported_prop_types=supported_prop_types,
        hot_hits_thresholds=HotHitsThresholds(
            min_games=int(os.environ.get("HOT_HITS_MIN_GAMES", "10")),
            recent_window=int(os.environ.get("HOT_HITS_RECENT_WINDOW", "5")),
            form_window=int(os.environ.get("HOT_HITS_FORM_WINDOW", "10")),
            min_recent_at_bats=int(os.environ.get("HOT_HITS_MIN_RECENT_AB", "12")),
            hot_avg_min=float(os.environ.get("HOT_HITS_AVG_MIN", "0.350")),
            strong_hot_avg=float(os.environ.get("HOT_HITS_STRONG_AVG", "0.400")),
            min_season_avg=float(os.environ.get("HOT_HITS_MIN_SEASON_AVG", "0.240")),
            min_recent_hit_games=int(os.environ.get("HOT_HITS_MIN_HIT_GAMES", "4")),
            min_display_score=int(os.environ.get("HOT_HITS_MIN_SCORE", "7")),
            max_batters_per_team=int(os.environ.get("HOT_HITS_MAX_BATTERS_PER_TEAM", "9")),
            include_bvp=os.environ.get("HOT_HITS_INCLUDE_BVP", "true").strip().lower() not in {"0", "false", "no"},
            discord_min_score=int(os.environ.get("HOT_HITS_DISCORD_MIN_SCORE", "10")),
        ),
        player_aliases=aliases,
        include_under_candidates=os.environ.get("INCLUDE_UNDERS", "true").strip().lower() not in {"0", "false", "no"},
        export_history=os.environ.get("EXPORT_HISTORY", "false").strip().lower() in {"1", "true", "yes"},
        min_display_score=int(os.environ.get("MIN_DISPLAY_SCORE", "7")),
        display_limit=int(os.environ.get("DISPLAY_LIMIT", "30")),
        data_mode=os.environ.get("DATA_MODE", "sample").strip().lower() or "sample",
        line_source=os.environ.get("LINE_SOURCE", "fanduel").strip().lower() or "fanduel",
    )
