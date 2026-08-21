from __future__ import annotations

import json
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DISCORD_MESSAGE_LIMIT = 2000
DISCORD_EMBED_LIMIT = 10
DISCORD_ATTEMPTS = 3
DISCORD_BACKOFF_SECONDS = 2.0


@dataclass(frozen=True)
class DiscordResult:
    ok: bool
    status_code: int | None = None
    error: str | None = None


def _send_payload(
    webhook_url: str,
    payload: dict,
    attempts: int = DISCORD_ATTEMPTS,
    backoff_seconds: float = DISCORD_BACKOFF_SECONDS,
) -> DiscordResult:
    request = Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "mlb-props/discord-notifier"},
        method="POST",
    )
    last_result: DiscordResult | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=20) as response:
                status_code = getattr(response, "status", None)
                return DiscordResult(ok=200 <= int(status_code or 0) < 300, status_code=status_code)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_result = DiscordResult(ok=False, status_code=exc.code, error=body[:500])
            if exc.code < 500 and exc.code != 429:
                return last_result
        except URLError as exc:
            last_result = DiscordResult(ok=False, error=str(exc))
        if attempt < attempts:
            time.sleep(backoff_seconds * (2 ** (attempt - 1)))
    assert last_result is not None
    return last_result


def send_discord_message(webhook_url: str, content: str, username: str = "MLB Props") -> DiscordResult:
    if not webhook_url:
        return DiscordResult(ok=False, error="Missing Discord webhook URL")
    if not content.strip():
        return DiscordResult(ok=False, error="Discord message content is empty")

    payload = {
        "content": _trim_message(content),
        "username": username,
        "allowed_mentions": {"parse": []},
    }
    return _send_payload(webhook_url, payload)


def send_discord_embeds(
    webhook_url: str,
    embeds: list[dict],
    content: str = "",
    username: str = "MLB Props",
) -> DiscordResult:
    if not webhook_url:
        return DiscordResult(ok=False, error="Missing Discord webhook URL")
    if not embeds and not content.strip():
        return DiscordResult(ok=False, error="Discord payload is empty")

    payload = {
        "content": _trim_message(content) if content else "",
        "username": username,
        "embeds": embeds[:DISCORD_EMBED_LIMIT],
        "allowed_mentions": {"parse": []},
    }
    return _send_payload(webhook_url, payload)


def _trim_message(content: str) -> str:
    if len(content) <= DISCORD_MESSAGE_LIMIT:
        return content
    suffix = "\n... trimmed for Discord"
    return content[: DISCORD_MESSAGE_LIMIT - len(suffix)].rstrip() + suffix
