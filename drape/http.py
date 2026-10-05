"""Bounded retries and shared rate-limit cooldowns for catalog and file requests."""

import threading
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests

RETRY_STATUSES = {500, 502, 503, 504}
MAX_RETRY_AFTER = 86400  # an absurd Retry-After must not block a host forever or overflow timers


_rate_lock = threading.Lock()
_blocked_until = {}
# Only isolated inspection workers enable pacing; normal downloads stay unchanged.
request_interval = 0
_last_request = None


class RateLimited(requests.RequestException):
    """The server deferred a request; this supplies no compatibility evidence.

    `host` is the cooldown it belongs to; `item` is set when download links were refreshed
    before the limit hit, so the retry can start from the fresh links."""

    def __init__(self, retry_after=60, checks=None, host=None, item=None):
        super().__init__("HTTP 429: server rate-limited requests; retry later.")
        self.retry_after = retry_after
        self.checks = checks or {}
        self.host = host
        self.item = item


def _retry_after(value):
    try:
        return min(MAX_RETRY_AFTER, max(1, int(value)))
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return min(MAX_RETRY_AFTER, max(1, date.timestamp() - time.time()))
        except (ValueError, TypeError, OverflowError):
            return 60


def defer(host, delay):
    """Share a worker's host cooldown with catalog/download requests in the app."""
    if host:
        with _rate_lock:
            _blocked_until[host] = max(
                _blocked_until.get(host, 0), time.monotonic() + min(MAX_RETRY_AFTER, max(1, delay))
            )


def get(url, *, retry_server_errors=True, **kwargs):
    """Respect rate-limit cooldowns and retry transient server failures twice."""
    host = urlsplit(url).hostname
    with _rate_lock:
        remaining = _blocked_until.get(host, 0) - time.monotonic()
    if remaining > 0:
        raise RateLimited(remaining, host=host)
    attempts = 3 if retry_server_errors else 1
    for attempt in range(attempts):
        global _last_request
        if request_interval and _last_request is not None:
            time.sleep(max(0, request_interval - (time.monotonic() - _last_request)))
        if request_interval:
            _last_request = time.monotonic()
        response = requests.get(url, **kwargs)
        if response.status_code == 429:
            delay = _retry_after(response.headers.get("Retry-After"))
            with _rate_lock:
                until = time.monotonic() + delay
                for origin in (host, urlsplit(response.url or url).hostname):
                    _blocked_until[origin] = max(_blocked_until.get(origin, 0), until)
            response.close()
            raise RateLimited(delay, host=host)
        if response.status_code not in RETRY_STATUSES or attempt == attempts - 1:
            return response
        response.close()
        time.sleep(attempt + 1)
