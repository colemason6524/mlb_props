#!/usr/bin/env python3
"""Generate user systemd services and timers for the sports VM."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


TIMEZONE = "America/Detroit"
PREFIX = "sports-"


@dataclass(frozen=True)
class ScheduledTask:
    name: str
    description: str
    runner: str
    task: str
    calendars: tuple[str, ...] = ()
    interval_minutes: int | None = None


def daily(time: str) -> str:
    return f"*-*-* {time}:00 {TIMEZONE}"


def weekly(days: str, time: str) -> str:
    return f"{days} *-*-* {time}:00 {TIMEZONE}"


def horse_times(start_hour: int, start_minute: int, count: int, step: int = 20) -> tuple[str, ...]:
    total = start_hour * 60 + start_minute
    return tuple(daily(f"{(total + i * step) // 60:02d}:{(total + i * step) % 60:02d}") for i in range(count))


TASKS = (
    ScheduledTask("cfb-board", "CFB daily board", "cfb_props/scripts/run_cfb_linux_task.sh", "board", (daily("09:56"),)),
    ScheduledTask("cfb-grade", "CFB Tuesday and Sunday grade", "cfb_props/scripts/run_cfb_linux_task.sh", "grade", (weekly("Tue,Sun", "09:17"),)),
    ScheduledTask("golf-forecast", "Golf forecast every two hours", "golf_props/scripts/linux/run_linux_task.sh", "weekly-forecast", tuple(daily(f"{hour:02d}:17") for hour in range(0, 24, 2))),
    ScheduledTask("golf-bovada", "Golf Bovada collection", "golf_props/scripts/linux/run_linux_task.sh", "bovada-collect", (weekly("Mon,Wed,Thu", "08:56"),)),
    ScheduledTask("horses-pre-race", "Horses BBC pre-race collection", "horses/scripts/linux/run_horses_task.sh", "pre-race", tuple(daily(t) for t in ("04:27", "06:07", "07:27", "09:07", "10:27", "12:07", "13:27", "15:07"))),
    ScheduledTask("horses-odds", "Horses external odds collection", "horses/scripts/linux/run_horses_task.sh", "odds-source", horse_times(4, 32, 33)),
    ScheduledTask("horses-pre-race-score", "Horses pre-race scoring", "horses/scripts/linux/run_horses_task.sh", "pre-race-score", horse_times(4, 37, 33)),
    ScheduledTask("horses-performance", "Horses performance scoring", "horses/scripts/linux/run_horses_task.sh", "performance-score", horse_times(4, 42, 33)),
    ScheduledTask("horses-coverage", "Horses odds coverage report", "horses/scripts/linux/run_horses_task.sh", "odds-source-coverage", (daily("15:29"),)),
    ScheduledTask("horses-archive", "Horses report archive", "horses/scripts/linux/run_horses_task.sh", "report-archive", (daily("15:36"),)),
    ScheduledTask("horses-nightly", "Horses nightly finalization", "horses/scripts/linux/run_horses_task.sh", "nightly", (daily("20:54"),)),
    ScheduledTask("mlb-pipeline-noon", "MLB noon forecast pipeline", "mlb_props/scripts/run_linux_task.sh", "forecast-pipeline-noon", (daily("12:15"),)),
    ScheduledTask("mlb-pipeline-afternoon", "MLB afternoon forecast pipeline", "mlb_props/scripts/run_linux_task.sh", "forecast-pipeline-afternoon", (daily("16:45"),)),
    ScheduledTask("nba-grade", "NBA grading", "nba_stats/scripts/run_linux_task.sh", "grade", (daily("11:45"),)),
    ScheduledTask("nba-board", "NBA board", "nba_stats/scripts/run_linux_task.sh", "board", (daily("15:42"),)),
    ScheduledTask("nfl-board", "NFL board", "nfl_props/scripts/run_nfl_linux_task.sh", "board", (daily("10:56"),)),
    ScheduledTask("nfl-grade", "NFL Tuesday grade", "nfl_props/scripts/run_nfl_linux_task.sh", "grade", (weekly("Tue", "08:47"),)),
    ScheduledTask("nhl-grade", "NHL grading", "nhl_props/scripts/run_linux_task.sh", "grade", (daily("09:26"),)),
    ScheduledTask("nhl-board-am", "NHL morning board", "nhl_props/scripts/run_linux_task.sh", "board-am", (daily("09:56"),)),
    ScheduledTask("nhl-board-pm", "NHL evening board", "nhl_props/scripts/run_linux_task.sh", "board-pm", (daily("16:53"),)),
    ScheduledTask("soccer-board-am", "Soccer morning board", "soccer_props/scripts/run_linux_task.sh", "board-am", (daily("06:07"),)),
    ScheduledTask("soccer-grade", "Soccer grading", "soccer_props/scripts/run_linux_task.sh", "grade", (daily("08:13"),)),
    ScheduledTask("soccer-board-mid", "Soccer midday board", "soccer_props/scripts/run_linux_task.sh", "board-mid", (daily("09:26"),)),
    ScheduledTask("tennis-grade", "Tennis grading", "tennis_props/scripts/run_linux_task.sh", "grade", (daily("08:13"),)),
    ScheduledTask("tennis-stats-shadow", "Tennis stats shadow", "tennis_props/scripts/run_linux_task.sh", "stats-shadow", (daily("09:27"),)),
    ScheduledTask("tennis-daily", "Tennis morning card", "tennis_props/scripts/run_linux_task.sh", "daily", (daily("10:02"),)),
    ScheduledTask("tennis-midday", "Tennis midday card", "tennis_props/scripts/run_linux_task.sh", "midday", (daily("15:21"),)),
    ScheduledTask("ufc-odds", "UFC scheduled odds collection", "ufc_props/scripts/run_linux_task.sh", "collect-odds", tuple(daily(t) for t in ("00:17", "06:17", "12:17", "18:17"))),
    ScheduledTask("ufc-odds-window", "UFC dense odds window", "ufc_props/scripts/run_linux_task.sh", "collect-odds-window", interval_minutes=17),
    ScheduledTask("ufc-board", "UFC Friday and Saturday boards", "ufc_props/scripts/run_linux_task.sh", "board", (weekly("Fri", "18:07"), weekly("Sat", "09:56"))),
    ScheduledTask("ufc-grade", "UFC Sunday grade", "ufc_props/scripts/run_linux_task.sh", "grade", (weekly("Sun", "09:43"),)),
    ScheduledTask("wnba-daily", "WNBA daily board", "wnba_props/scripts/run_linux_task.sh", "daily", (daily("10:56"),)),
    ScheduledTask("wnba-shadow-capture", "WNBA hourly shadow capture", "wnba_props/scripts/run_linux_task.sh", "shadow-capture", tuple(daily(f"{hour:02d}:13") for hour in range(9, 24))),
    ScheduledTask("wnba-shadow-grade", "WNBA shadow grading", "wnba_props/scripts/run_linux_task.sh", "shadow-grade", (daily("06:17"),)),
)


def service_text(task: ScheduledTask) -> str:
    return f"""[Unit]
Description={task.description}
After=network-online.target
Wants=network-online.target
ConditionPathExists=%h/{task.runner}

[Service]
Type=oneshot
ExecStart=%h/{task.runner} {task.task}
TimeoutStartSec=infinity
"""


def timer_text(task: ScheduledTask) -> str:
    triggers = "\n".join(f"OnCalendar={value}" for value in task.calendars)
    if task.interval_minutes is not None:
        triggers = f"OnActiveSec={task.interval_minutes}m\nOnUnitInactiveSec={task.interval_minutes}m"
    return f"""[Unit]
Description=Schedule {task.description}

[Timer]
{triggers}
AccuracySec=1s
RandomizedDelaySec=0
Persistent=false
Unit={PREFIX}{task.name}.service

[Install]
WantedBy=timers.target
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    expected = set()
    for task in TASKS:
        service_name = f"{PREFIX}{task.name}.service"
        timer_name = f"{PREFIX}{task.name}.timer"
        (args.output_dir / service_name).write_text(service_text(task))
        (args.output_dir / timer_name).write_text(timer_text(task))
        expected.update((service_name, timer_name))

    tennis_retry = "tennis-setup-retry.service"
    (args.output_dir / tennis_retry).write_text(
        """[Unit]
Description=One-time Tennis data source recovery
After=network-online.target
Wants=network-online.target
ConditionPathExists=%h/tennis_props/scripts/setup_retry.sh

[Service]
Type=simple
WorkingDirectory=%h/tennis_props
ExecStart=%h/tennis_props/scripts/setup_retry.sh
TimeoutStartSec=infinity
"""
    )

    for path in args.output_dir.glob(f"{PREFIX}*"):
        if path.name not in expected and path.is_file():
            path.unlink()
    print(f"Generated {len(TASKS)} services and {len(TASKS)} timers in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
