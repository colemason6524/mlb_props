from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from ..config import OUTPUTS_DIR
from ..models import Game, MatchupContext, PropLine
from .draftkings import MlbDraftKingsSource
from .fanduel import MlbFanDuelSource


class MlbScrapeSource:
    def __init__(self, shared_cache, lines_cache) -> None:
        self.fanduel = MlbFanDuelSource(shared_cache, lines_cache)
        self.draftkings = MlbDraftKingsSource(shared_cache, lines_cache)
        self._matchup_contexts: dict[tuple[str, str], MatchupContext] = {}

    def fetch_prop_lines(self, games: list[Game], supported_prop_types: list[str]) -> list[PropLine]:
        fanduel_lines = self.fanduel.fetch_prop_lines(games, supported_prop_types)
        draftkings_lines = self.draftkings.fetch_prop_lines(games, supported_prop_types)
        deduped: dict[tuple[str, str], PropLine] = {}
        for line in [*fanduel_lines, *draftkings_lines]:
            deduped[(line.subject_name_norm, line.prop_type)] = line
        self._matchup_contexts = self.fanduel.fetch_matchup_contexts(date.today())
        return sorted(deduped.values(), key=lambda item: (item.team, item.subject_name_raw, item.prop_type))

    def fetch_matchup_contexts(self, screen_date: date) -> dict[tuple[str, str], MatchupContext]:
        self._matchup_contexts = self.fanduel.fetch_matchup_contexts(screen_date)
        return self._matchup_contexts

    def diagnostics(self) -> dict[str, int]:
        merged: dict[str, int] = {}
        for prefix, source in (("fanduel", self.fanduel), ("draftkings", self.draftkings)):
            diagnostics = source.diagnostics()
            for key, value in diagnostics.items():
                merged[f"{prefix}_{key}"] = value
        return merged

    def export_diagnostics_snapshot(self, screen_date: date) -> Path | None:
        fanduel_path = self.fanduel.export_diagnostics_snapshot(screen_date)
        draftkings_path = self.draftkings.export_diagnostics_snapshot(screen_date)
        if fanduel_path is None and draftkings_path is None:
            return None

        diagnostics_dir = OUTPUTS_DIR / "diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = diagnostics_dir / f"scrape_sources_{screen_date.isoformat()}_{timestamp}.json"
        payload = {
            "screen_date": screen_date.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "diagnostics": self.diagnostics(),
            "snapshots": {
                "fanduel": str(fanduel_path) if fanduel_path else None,
                "draftkings": str(draftkings_path) if draftkings_path else None,
            },
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        return path
