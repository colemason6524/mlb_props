from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from mlb_props.cache import JsonCache
from mlb_props.notifiers.discord import send_discord_embeds
from mlb_props.utils import fetch_json_cached, fetch_text


class FetchRetryTests(unittest.TestCase):
    def test_transient_network_error_retries_and_succeeds(self) -> None:
        calls = {"count": 0}

        def flaky(url, headers=None, timeout=30, attempts=3, backoff_seconds=1.5):
            calls["count"] += 1
            if calls["count"] < 3:
                raise RuntimeError(f"Network error for {url}: fake timeout") from URLError("timeout")
            return '{"ok": true}'

        with patch("mlb_props.utils._fetch_text_once", side_effect=flaky):
            result = fetch_text("https://example.test/x", attempts=3, backoff_seconds=0.01)
        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(calls["count"], 3)

    def test_persistent_network_error_raises(self) -> None:
        def failing(url, headers=None, timeout=30, attempts=3, backoff_seconds=1.5):
            raise RuntimeError(f"Network error for {url}: fake timeout") from URLError("timeout")

        with patch("mlb_props.utils._fetch_text_once", side_effect=failing):
            with self.assertRaises(RuntimeError):
                fetch_text("https://example.test/x", attempts=3, backoff_seconds=0.01)

    def test_http_4xx_not_retried(self) -> None:
        calls = {"count": 0}

        def bad_request(url, headers=None, timeout=30, attempts=3, backoff_seconds=1.5):
            calls["count"] += 1
            raise RuntimeError("HTTP 404 for https://example.test/x") from HTTPError(
                "https://example.test/x", 404, "Not Found", None, None
            )

        with patch("mlb_props.utils._fetch_text_once", side_effect=bad_request):
            with self.assertRaises(RuntimeError):
                fetch_text("https://example.test/x", attempts=3, backoff_seconds=0.01)
        self.assertEqual(calls["count"], 1)

    def test_timeout_error_retries_and_succeeds(self) -> None:
        calls = {"count": 0}

        class FlakyResponse:
            def __init__(self, succeed: bool) -> None:
                self._succeed = succeed

            def read(self) -> bytes:
                if not self._succeed:
                    raise TimeoutError("The read operation timed out")
                return b'{"ok": true}'

            def __enter__(self) -> "FlakyResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

        def flaky_urlopen(request, timeout=30):
            calls["count"] += 1
            return FlakyResponse(succeed=calls["count"] >= 3)

        with patch("mlb_props.utils.urlopen", side_effect=flaky_urlopen):
            result = fetch_text("https://example.test/x", attempts=3, backoff_seconds=0.01)
        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(calls["count"], 3)

    def test_persistent_timeout_error_raises(self) -> None:
        class TimeoutResponse:
            def read(self) -> bytes:
                raise TimeoutError("The read operation timed out")

            def __enter__(self) -> "TimeoutResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

        def always_timeout(request, timeout=30):
            return TimeoutResponse()

        with patch("mlb_props.utils.urlopen", side_effect=always_timeout):
            with self.assertRaises(RuntimeError):
                fetch_text("https://example.test/x", attempts=3, backoff_seconds=0.01)



class CacheFallbackTests(unittest.TestCase):
    def _cache(self, tmp: Path) -> JsonCache:
        return JsonCache(tmp, ttl_hours=0.0)

    def test_fresh_cache_used_without_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache = self._cache(Path(tmp_dir))
            cache.set("key", {"data": "fresh"})
            with patch("mlb_props.utils.fetch_json", side_effect=AssertionError("should not fetch")):
                result = fetch_json_cached(cache, "key", "https://example.test/x")
            self.assertEqual(result, {"data": "fresh"})

    def test_stale_cache_fallback_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache = self._cache(Path(tmp_dir))
            cache.set("key", {"data": "stale"})
            with patch("mlb_props.utils.fetch_json", side_effect=RuntimeError("network down")):
                result = fetch_json_cached(cache, "key", "https://example.test/x")
            self.assertEqual(result, {"data": "stale"})

    def test_no_cache_and_failure_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache = self._cache(Path(tmp_dir))
            with patch("mlb_props.utils.fetch_json", side_effect=RuntimeError("network down")):
                with self.assertRaises(RuntimeError):
                    fetch_json_cached(cache, "missing", "https://example.test/x")


class DiscordRetryTests(unittest.TestCase):
    def _payload(self) -> dict:
        return {"content": "test", "username": "MLB Props"}

    def test_retry_on_transient_error_then_success(self) -> None:
        responses = [
            URLError("temporary network failure"),
            URLError("temporary network failure"),
        ]

        class FakeResponse:
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout=20):
            if responses:
                raise responses.pop(0)
            return FakeResponse()

        with patch("mlb_props.notifiers.discord.urlopen", side_effect=fake_urlopen), patch(
            "mlb_props.notifiers.discord.time.sleep"
        ) as mock_sleep:
            result = send_discord_embeds("https://webhook.test/x", [], content="hi")
        self.assertTrue(result.ok)
        self.assertEqual(mock_sleep.call_count, 2)

    def test_persistent_transient_error_returns_failure(self) -> None:
        def always_fails(request, timeout=20):
            raise URLError("network is down")

        with patch("mlb_props.notifiers.discord.urlopen", side_effect=always_fails), patch(
            "mlb_props.notifiers.discord.time.sleep"
        ) as mock_sleep:
            result = send_discord_embeds("https://webhook.test/x", [], content="hi")
        self.assertFalse(result.ok)
        self.assertIn("network is down", result.error)
        self.assertEqual(mock_sleep.call_count, 2)

    def test_non_transient_http_error_returns_immediately(self) -> None:
        import io

        def bad_request(request, timeout=20):
            raise HTTPError(
                "https://webhook.test/x", 400, "Bad Request", {}, io.BytesIO(b"bad")
            )

        with patch("mlb_props.notifiers.discord.urlopen", side_effect=bad_request) as mock_urlopen, patch(
            "mlb_props.notifiers.discord.time.sleep"
        ) as mock_sleep:
            result = send_discord_embeds("https://webhook.test/x", [], content="hi")
        self.assertFalse(result.ok)
        self.assertEqual(result.status_code, 400)
        mock_urlopen.assert_called_once()
        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
