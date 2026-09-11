#!/usr/bin/env bash
set -u

TASK=${1:-}
PROJECT_DIR=${PROJECT_DIR:-"$HOME/mlb_props"}
ENV_FILE=${MLB_PROPS_ENV_FILE:-"$HOME/.config/mlb_props/env"}
PYTHON_EXE=${MLB_PROPS_PYTHON_EXE:-"$PROJECT_DIR/.venv/bin/python"}
LOG_DIR="$PROJECT_DIR/logs"
LOCK_FILE=${MLB_PROPS_LOCK_FILE:-"$HOME/.local/state/mlb_props/run.lock"}

mkdir -p "$LOG_DIR" "$(dirname "$LOCK_FILE")"

if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

export TZ=${TZ:-America/Detroit}
export PYTHONUNBUFFERED=1

if [[ ! -x "$PYTHON_EXE" ]]; then
    printf 'Project Python interpreter is unavailable: %s\n' "$PYTHON_EXE" >&2
    exit 1
fi

case "$TASK" in
    hot-hits)
        LOG_FILE="$LOG_DIR/hot_hits_task.log"
        TIMEOUT=45m
        # Collection only. The forecast board is the sole Discord publisher.
        export DATA_MODE=live SEND_DISCORD=false EXPORT_HISTORY=true DISPLAY_LIMIT=8
        export HOT_HITS_DISCORD_MIN_SCORE=${HOT_HITS_DISCORD_MIN_SCORE:-10}
        export HOT_HITS_CARD_POLICY=${HOT_HITS_CARD_POLICY:-core-first-v1}
        export HOT_HITS_CORE_LIMIT=${HOT_HITS_CORE_LIMIT:-4}
        export HOT_HITS_VALUE_LIMIT=${HOT_HITS_VALUE_LIMIT:-2}
        COMMAND=("$PYTHON_EXE" run_hot_hits.py)
        REQUIRED_SECRET=
        ;;
    pitcher-props)
        LOG_FILE="$LOG_DIR/pitcher_props_task.log"
        TIMEOUT=45m
        # Collection only. The forecast board is the sole Discord publisher.
        export DATA_MODE=live SEND_DISCORD=false EXPORT_HISTORY=true DISPLAY_LIMIT=30
        export PITCHER_PROPS_DISCORD_CORE_LIMIT=${PITCHER_PROPS_DISCORD_CORE_LIMIT:-5}
        export PITCHER_PROPS_DISCORD_WATCH_LIMIT=${PITCHER_PROPS_DISCORD_WATCH_LIMIT:-5}
        export RUN_NOTE="scheduled full pregame run"
        COMMAND=("$PYTHON_EXE" run_nightly.py)
        REQUIRED_SECRET=
        ;;
    game-markets-morning)
        LOG_FILE="$LOG_DIR/game_markets_task.log"
        TIMEOUT=20m
        export DATA_MODE=live EXPORT_HISTORY=true REFRESH_LINES=true
        export RUN_NOTE="scheduled morning pregame shadow run"
        COMMAND=("$PYTHON_EXE" run_game_markets.py)
        REQUIRED_SECRET=
        ;;
    game-markets-evening)
        LOG_FILE="$LOG_DIR/game_markets_task.log"
        TIMEOUT=20m
        export DATA_MODE=live EXPORT_HISTORY=true REFRESH_LINES=true
        export RUN_NOTE="scheduled evening lineup-confirmation refresh"
        COMMAND=("$PYTHON_EXE" run_game_markets.py)
        REQUIRED_SECRET=
        ;;
    forecast-board)
        LOG_FILE="$LOG_DIR/forecast_board_task.log"
        TIMEOUT=30m
        export DATA_MODE=live EXPORT_HISTORY=true
        if [[ "${FORECAST_BOARD_SEND_DISCORD:-true}" == "true" ]]; then
            COMMAND=("$PYTHON_EXE" run_forecast_board.py --date "$(date '+%F')" --send-discord)
        else
            COMMAND=("$PYTHON_EXE" run_forecast_board.py --date "$(date '+%F')")
        fi
        REQUIRED_SECRET=FORECAST_BOARD_DISCORD_WEBHOOK_URL
        ;;
    forecast-pipeline-noon|forecast-pipeline-afternoon)
        SLOT=${TASK#forecast-pipeline-}
        LOG_FILE="$LOG_DIR/forecast_pipeline_${SLOT}_task.log"
        TIMEOUT=60m
        export DATA_MODE=live EXPORT_HISTORY=true
        PIPELINE_ARGS=(--slot "$SLOT" --date "$(date '+%F')")
        REQUIRED_SECRET=
        if [[ "${FORECAST_BOARD_SEND_DISCORD:-true}" == "true" ]]; then
            PIPELINE_ARGS+=(--send-discord)
            REQUIRED_SECRET=FORECAST_BOARD_DISCORD_WEBHOOK_URL
        fi
        COMMAND=("$PYTHON_EXE" run_forecast_pipeline.py "${PIPELINE_ARGS[@]}")
        ;;
    *)
        printf 'Unknown task: %s\n' "$TASK" >&2
        exit 2
        ;;
esac

MAX_LOG_BYTES=${MLB_PROPS_MAX_LOG_BYTES:-5242880}
if [[ -f "$LOG_FILE" ]] && (( $(stat -c %s "$LOG_FILE") >= MAX_LOG_BYTES )); then
    rm -f "$LOG_FILE.2"
    [[ ! -f "$LOG_FILE.1" ]] || mv "$LOG_FILE.1" "$LOG_FILE.2"
    mv "$LOG_FILE" "$LOG_FILE.1"
fi

exec >>"$LOG_FILE" 2>&1
printf '%s  Starting %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$TASK"

if [[ -n "$REQUIRED_SECRET" && -z "${!REQUIRED_SECRET:-}" ]]; then
    printf '%s  FAILED: %s is not configured in %s\n' \
        "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$REQUIRED_SECRET" "$ENV_FILE"
    exit 1
fi

cd "$PROJECT_DIR" || exit 1
exec 9>"$LOCK_FILE"
printf '%s  Waiting for the shared task lock\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')"
flock 9
printf '%s  Running: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "${COMMAND[*]}"

timeout --signal=TERM --kill-after=2m "$TIMEOUT" "${COMMAND[@]}"
EXIT_CODE=$?
printf '%s  Finished %s with exit code %s\n' \
    "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$TASK" "$EXIT_CODE"
exit "$EXIT_CODE"
