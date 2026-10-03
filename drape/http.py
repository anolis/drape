"""Bounded retries and shared rate-limit cooldowns for catalog and file requests."""

from datetime import timezone
from email.utils import parsedate_to_datetime
import time
import threading
from urllib.parse import urlsplit

import requests

RETRY_STATUSES = {500, 502, 503, 504}
MAX_RETRY_AFTER = 86400  # an absurd Retry-After must not block a host forever or overflow timers


_rate_lock = threading.Lock()
_blocked_until = {}
_retry_slots = {}


class RateLimited(requests.RequestException):
    """The server deferred a request; this supplies no compatibility evidence."""

    def __init__(self, retry_after=60, checks=None, host=None):
        super().__init__("HTTP 429: server rate-limited requests; retry later.")
        self.retry_after = retry_after
        self.checks = checks or {}
        self.host = host

    def retry_delay(self):
        """Spread background retries for one host without sleeping in scan workers."""
        if self.host is None:
            return self.retry_after
        with _rate_lock:
            now = time.monotonic()
            slot = max(now + self.retry_after, _retry_slots.get(self.host, 0))
            _retry_slots[self.host] = slot + 2
        return slot - now


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


def get(url, *, retry_server_errors=True, **kwargs):
    """Respect rate-limit cooldowns and retry transient server failures twice."""
    host = urlsplit(url).hostname
    with _rate_lock:
        remaining = _blocked_until.get(host, 0) - time.monotonic()
    if remaining > 0:
        raise RateLimited(remaining, host=host)
    attempts = 3 if retry_server_errors else 1
    for attempt in range(attempts):
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
