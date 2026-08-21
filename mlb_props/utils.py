from __future__ import annotations

import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

TEAM_ABBR_ALIASES = {
    "ARI": "AZ",
    "AZ": "AZ",
    "ATL": "ATL",
    "BAL": "BAL",
    "BOS": "BOS",
    "CHC": "CHC",
    "CHW": "CWS",
    "CWS": "CWS",
    "CIN": "CIN",
    "CLE": "CLE",
    "COL": "COL",
    "DET": "DET",
    "HOU": "HOU",
    "KCR": "KC",
    "KC": "KC",
    "LAA": "LAA",
    "LAD": "LAD",
    "MIA": "MIA",
    "MIL": "MIL",
    "MIN": "MIN",
    "NYM": "NYM",
    "NYY": "NYY",
    "OAK": "ATH",
    "ATH": "ATH",
    "PHI": "PHI",
    "PIT": "PIT",
    "SDP": "SD",
    "SD": "SD",
    "SFG": "SF",
    "SF": "SF",
    "SEA": "SEA",
    "STL": "STL",
    "TBR": "TB",
    "TB": "TB",
    "TEX": "TEX",
    "TOR": "TOR",
    "WSN": "WSH",
    "WAS": "WSH",
    "WSH": "WSH",
}


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, HTTPError):
        return 500 <= exc.code < 600 or exc.code == 429
    if isinstance(exc, URLError):
        return True
    return False


def _fetch_text_once(url: str, headers: dict[str, str] | None, timeout: int) -> str:
    request_headers = DEFAULT_HEADERS.copy()
    if headers:
        request_headers.update(headers)
    request = Request(url, headers=request_headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc}") from exc


def fetch_text(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    attempts: int = 3,
    backoff_seconds: float = 1.5,
) -> str:
    """Fetch a URL with bounded retries.

    Retries only on transient failures (network errors, HTTP 5xx, and 429).
    All other HTTP errors and non-transient failures raise immediately.
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _fetch_text_once(url, headers, timeout)
        except RuntimeError as exc:
            cause = exc.__cause__ or exc
            last_error = exc
            if not _is_retryable_error(cause):
                raise
            if attempt < attempts:
                time.sleep(backoff_seconds * (2 ** (attempt - 1)))
    assert last_error is not None
    raise last_error


def fetch_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    attempts: int = 3,
    backoff_seconds: float = 1.5,
) -> Any:
    return json.loads(fetch_text(url, headers=headers, timeout=timeout, attempts=attempts, backoff_seconds=backoff_seconds))


def fetch_json_cached(
    cache,
    cache_key: str,
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    attempts: int = 3,
    backoff_seconds: float = 1.5,
) -> Any:
    """Fetch JSON with retries, returning fresh cache when available and falling
    back to stale cache only if a fresh fetch persistently fails."""
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        data = fetch_json(url, headers=headers, timeout=timeout, attempts=attempts, backoff_seconds=backoff_seconds)
    except Exception:
        stale = cache.get_allow_stale(cache_key)
        if stale is not None:
            return stale
        raise
    cache.set(cache_key, data)
    return data


def normalize_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()
    normalized = normalized.replace("'", "")
    normalized = normalized.replace(".", "")
    normalized = normalized.replace("-", " ")
    normalized = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", normalized)
    normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def normalize_team_abbr(team: str) -> str:
    cleaned = (team or "").strip().upper()
    return TEAM_ABBR_ALIASES.get(cleaned, cleaned)


def parse_iso_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = value.replace("Z", "+00:00")
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
