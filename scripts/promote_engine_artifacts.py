#!/usr/bin/env python3
"""Promote fitted research artifacts into the versioned production bundle."""
from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "evidence/engines"
DEST_DIR = ROOT / "mlb_props/artifacts"
ARTIFACTS = ("pitcher_engine_artifact.json", "game_engine_artifact.json")


def main() -> int:
    missing = [name for name in ARTIFACTS if not (SOURCE_DIR / name).is_file()]
    if missing:
        print(f"Missing fitted artifacts: {', '.join(missing)}")
        return 1

    DEST_DIR.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACTS:
        shutil.copy2(SOURCE_DIR / name, DEST_DIR / name)
        print(f"Promoted {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
