"""Consolidate MLB history evidence into one deduplicated, hashed directory.

Read-only over the sources (Mac outputs/pulls, the New Volume Windows
checkout and backup, and the transferred ZIP archives). Writes a working
copy plus manifest under evidence/history/. Originals are never modified.

Usage:
  python3 scripts/consolidate_history.py           # real run
  python3 scripts/consolidate_history.py --dry-run # print plan, no writes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEW_VOLUME = Path("/Volumes/New Volume")

MAC_OUT = ROOT / "outputs/history"
MAC_PFW = ROOT / "pitcher_props_from_windows"
MAC_HFW = ROOT / "hot_hits_from_windows"
MAC_GFW = ROOT / "game_markets_from_windows"
MAC_SCHEMA6 = ROOT / ".analysis/schema6/history"

WIN_OUT = NEW_VOLUME / "mlb_props/outputs/history"
WIN_BACKUP = NEW_VOLUME / "mlb_props_history_backup/hot_hits"

ZIPS = [
    ROOT / "pitcher_props_weekly_logs.zip",
    ROOT / "mlb_props_logs_new.zip",
    ROOT / "mlb_props_logs 2.zip",
    ROOT / "mlb_props_logs_week.zip",
    NEW_VOLUME / "mlb_props_logs.zip",
    NEW_VOLUME / "mlb_props/mlb_props_logs.zip",
]

STREAM_FILES = {
    "pitcher_props": "pitcher_props_*.json",
    "hot_hits": "hot_hits_*.json",
    "game_markets": "game_markets_*.json",
}
STREAM_DIRNAME = {
    "pitcher_props": "pitcher",
    "hot_hits": "hot_hits",
    "game_markets": "game_markets",
}

# Highest priority first. For a (stream, screen_date), the earliest source
# list that has any non-empty export wins; directory sources always beat
# zip-extracted sources.
SOURCE_PRIORITY = {
    "pitcher_props": [
        ("schema6", MAC_SCHEMA6, False),
        ("win_checkout", WIN_OUT, False),
        ("mac_out", MAC_OUT, True),
        ("mac_pfw_incl_dirs", MAC_PFW, True),
        ("mac_pfw", MAC_PFW, False),
    ],
    "hot_hits": [
        ("win_checkout", WIN_OUT, False),
        ("win_backup", WIN_BACKUP, False),
        ("mac_out", MAC_OUT, False),
        ("mac_hfw_incl_dirs", MAC_HFW, True),
        ("mac_hfw", MAC_HFW, False),
    ],
    "game_markets": [
        ("win_checkout", WIN_OUT, False),
        ("mac_out", MAC_OUT, True),
        ("mac_gfw_incl_dirs", MAC_GFW, True),
        ("mac_gfw", MAC_GFW, False),
    ],
}
ZIP_PRIORITY_PREFIX_ORDER = ["pitcher_props_weekly_logs", "mlb_props_logs_new", "mlb_props_logs 2", "mlb_props_logs_week", "mlb_props_logs"]
DUP_SOURCE_LABELS = {}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_file(name: str, source_path: str, data: bytes, stream: str, source: str, origin: str, member: str | None = None) -> dict:
    info: dict = {
        "file": name,
        "stream": stream,
        "source": source,
        "origin": origin,
        "source_path": source_path,
        "member": member,
        "bytes": len(data),
        "sha256": sha256_bytes(data),
    }
    try:
        doc = json.loads(data.decode("utf-8", "replace"))
    except Exception as exc:
        info["error"] = f"unparseable: {exc}"
        return info

    info["screen_date"] = doc.get("screen_date")
    exported = doc.get("exported_at") or doc.get("generated_at")
    info["exported_at"] = exported
    info["history_schema_version"] = doc.get("history_schema_version")
    info["model_version"] = doc.get("model_version")
    candidates = doc.get("candidates") or []
    info["candidates"] = len(candidates)

    if stream == "pitcher_props":
        info["priced_candidates"] = sum(
            1 for c in candidates if (c.get("price_shadow") or {}).get("over_price")
        )
        info["forecast_rows"] = len(doc.get("forecast_rows") or [])
        slate = doc.get("slate_games") or []
        times = []
        for game in slate:
            iso = game.get("game_time_utc")
            if not iso:
                continue
            try:
                times.append(datetime.fromisoformat(iso.replace("Z", "+00:00")))
            except Exception:
                pass
        if exported:
            try:
                export_dt = datetime.fromisoformat(exported.replace("Z", "+00:00"))
            except Exception:
                export_dt = None
            if times and export_dt:
                info["timing"] = "pre_start" if export_dt < min(times) else "post_start_risk"
            elif export_dt:
                info["timing"] = "morning_pregame" if export_dt.hour < 16 else "evening_post_start_risk"
            else:
                info["timing"] = "unknown"
        else:
            info["timing"] = "unknown"
        delivery = doc.get("discord_delivery") or {}
        info["delivered"] = delivery.get("ok")
    elif stream == "hot_hits":
        info["pool_rows"] = len(doc.get("confidence_research_pool") or [])
        delivery = doc.get("discord_delivery") or {}
        info["delivered"] = delivery.get("status")
    elif stream == "game_markets":
        coverage = doc.get("coverage") or {}
        info["snapshots"] = coverage.get("matched_with_lines")
        info["slate_games"] = coverage.get("slate_games")
    return info


def collect(dry_run: bool) -> list[dict]:
    records: list[dict] = []

    for stream, sources in SOURCE_PRIORITY.items():
        pattern = STREAM_FILES[stream]
        for source_name, source_path, recursive in sources:
            if not source_path.exists():
                continue
            iterator = source_path.rglob(pattern) if recursive else source_path.glob(pattern)
            for path in sorted(iterator):
                if dry_run:
                    records.append(
                        {
                            "file": path.name,
                            "stream": stream,
                            "source": source_name,
                            "screen_date": None,
                        }
                    )
                    continue
                data = path.read_bytes()
                records.append(
                    parse_file(
                        path.name,
                        str(path),
                        data,
                        stream,
                        source_name,
                        "directory",
                    )
                )

    for zip_path in ZIPS:
        if not zip_path.exists():
            continue
        try:
            with zipfile.ZipFile(zip_path) as archive:
                for name in archive.namelist():
                    base = os.path.basename(name)
                    if not base.endswith(".json"):
                        continue
                    for stream, prefix in STREAM_PREFIXES().items():
                        if not base.startswith(prefix):
                            continue
                        if dry_run:
                            records.append(
                                {
                                    "file": base,
                                    "stream": stream,
                                    "source": f"zip:{zip_path.name}",
                                    "screen_date": None,
                                }
                            )
                            continue
                        data = archive.read(name)
                        records.append(
                            parse_file(
                                base,
                                str(zip_path),
                                data,
                                stream,
                                f"zip:{zip_path.name}",
                                "zip",
                                member=name,
                            )
                        )
                        break
        except zipfile.BadZipFile:
            continue

    return records


def STREAM_PREFIXES() -> dict[str, str]:
    return {stream: pattern.split("*")[0] for stream, pattern in STREAM_FILES.items()}


def choose_authoritative(records: list[dict]) -> dict[tuple[str, str], dict]:
    """Score = (tier, rank, -count): lower tier wins (directories beat
    zips), then earliest source priority, then more candidates.

    game_markets is the exception: AM and PM snapshots are distinct
    observations for the same screen_date, so every unique file is kept and
    dedup is by filename (same name = same capture, copied once)."""
    best: dict[tuple[str, str], dict] = {}
    for stream, sources in SOURCE_PRIORITY.items():
        for record in records:
            if record.get("stream") != stream:
                continue
            screen = record.get("screen_date")
            if not screen:
                continue
            key = (stream, record.get("file")) if stream == "game_markets" else (stream, screen)
            count = record.get("candidates") or 0
            if record.get("origin") == "zip":
                tier = 1
                zip_name = record.get("source", "").replace("zip:", "").replace(".zip", "")
                rank = ZIP_PRIORITY_PREFIX_ORDER.index(base) if (base := zip_name) in ZIP_PRIORITY_PREFIX_ORDER else len(ZIP_PRIORITY_PREFIX_ORDER)
            else:
                tier = 0
                rank = next(
                    (i for i, (name, _p, _r) in enumerate(sources) if name == record.get("source")),
                    len(sources),
                )
            score = (tier, rank, -count)
            current = best.get(key)
            if current is None:
                best[key] = record
                continue
            current_tier = 1 if current.get("origin") == "zip" else 0
            current_rank = next(
                (
                    i
                    for i, (name, _p, _r) in enumerate(sources)
                    if name == current.get("source")
                ),
                len(sources),
            ) if current_tier == 0 else (
                ZIP_PRIORITY_PREFIX_ORDER.index(
                    current.get("source", "").replace("zip:", "").replace(".zip", "")
                )
                if current.get("source", "").replace("zip:", "").replace(".zip", "")
                in ZIP_PRIORITY_PREFIX_ORDER
                else len(ZIP_PRIORITY_PREFIX_ORDER)
            )
            current_count = current.get("candidates") or 0
            current_score = (current_tier, current_rank, -current_count)
            if score < current_score:  # lower tier/rank, more candidates wins
                best[key] = record
    return best


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        records = collect(True)
        print(f"Would scan {len(records)} files")
        for stream in STREAM_FILES:
            count = sum(1 for r in records if r["stream"] == stream)
            print(f"- {stream}: {count}")
        return 0

    evidence_root = ROOT / "evidence/history"
    evidence_root.mkdir(parents=True, exist_ok=True)
    records = collect(False)
    chosen = choose_authoritative(records)

    manifest_rows: list[dict] = []
    for key in sorted(chosen.keys()):
        winner = chosen[key]
        stream = winner.get("stream")
        stream_dir = evidence_root / STREAM_DIRNAME[stream]
        stream_dir.mkdir(parents=True, exist_ok=True)
        if winner.get("origin") == "zip":
            parent = stream_dir / "_zip_appendix" / winner["source"].replace("zip:", "")
            parent.mkdir(parents=True, exist_ok=True)
            dest = parent / winner["file"]
        else:
            dest = stream_dir / winner["file"]

        src_path = Path(winner["source_path"])
        if winner.get("origin") == "zip":
            with zipfile.ZipFile(src_path) as archive:
                data = archive.read(winner["member"])
        else:
            data = src_path.read_bytes()
        dest.write_bytes(data)

        row = dict(winner)
        row["selected"] = True
        row["dest_path"] = str(dest.relative_to(ROOT))
        row["dest_sha256"] = sha256_bytes(data)
        row["timing_class"] = row.get("timing") or (
            "gm_snapshot" if stream == "game_markets" else "unknown"
        )
        manifest_rows.append(row)

    winner_keys = {(row.get("stream"), row.get("screen_date")) for row in manifest_rows}
    for record in records:
        key = (record.get("stream"), record.get("screen_date"))
        if not key[1] or key in winner_keys:
            continue
        row = dict(record)
        row["selected"] = False
        row["dup_of"] = chosen.get(key, {}).get("file")
        manifest_rows.append(row)

    (evidence_root / "MANIFEST.json").write_text(json.dumps(manifest_rows, indent=1))

    summary = {"by_stream": {}, "timing": {}, "dates": {}}
    for row in manifest_rows:
        if not row.get("selected"):
            continue
        stream = row.get("stream", "?")
        summary["by_stream"][stream] = summary["by_stream"].get(stream, 0) + 1
        timing = row.get("timing") or "snapshot"
        summary["timing"].setdefault(stream, {})
        summary["timing"][stream][timing] = summary["timing"][stream].get(timing, 0) + 1
        summary["dates"].setdefault(row.get("screen_date"), []).append(row.get("file"))
    (evidence_root / "SUMMARY.json").write_text(json.dumps(summary, indent=1, sort_keys=True))

    total_candidates = {}
    total_priced = 0
    total_pool = 0
    for row in manifest_rows:
        if not row.get("selected"):
            continue
        total_candidates[row["stream"]] = total_candidates.get(row["stream"], 0) + (row.get("candidates") or 0)
        total_priced += row.get("priced_candidates") or 0
        total_pool += row.get("pool_rows") or 0

    print(f"Manifest rows: {len(manifest_rows)} | chosen exports: {len(chosen)}")
    for stream, count in summary["by_stream"].items():
        print(f"- {stream}: {count} dates, {total_candidates.get(stream, 0)} candidates")
    print(f"Priced pitcher candidates: {total_priced}")
    print(f"Hot Hits pool rows: {total_pool}")
    print(f"Evidence root: {evidence_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
