"""Bounded retries and shared rate-limit cooldowns for catalog and file requests."""

from datetime import timezone
from email.utils import parsedate_to_datetime
import time
import threading
from urllib.parse import urlsplit

import requests

RETRY_STATUSES = {500, 502, 503, 504}


_rate_lock = threading.Lock()
_blocked_until = {}


class RateLimited(requests.RequestException):
    """The server deferred a request; this supplies no compatibility evidence."""

    def __init__(self, retry_after=60, checks=None):
        super().__init__("HTTP 429: server rate-limited requests; retry later.")
        self.retry_after = retry_after
        self.checks = checks or {}


def _retry_after(value):
    try:
        return max(1, int(value))
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(1, date.timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return 60


def get(url, **kwargs):
    """Respect rate-limit cooldowns and retry transient server failures twice."""
    host = urlsplit(url).hostname
    with _rate_lock:
        remaining = _blocked_until.get(host, 0) - time.monotonic()
    if remaining > 0:
        raise RateLimited(remaining)
    for attempt in range(3):
        response = requests.get(url, **kwargs)
        if response.status_code == 429:
            delay = _retry_after(response.headers.get("Retry-After"))
            with _rate_lock:
                until = time.monotonic() + delay
                for origin in (host, urlsplit(response.url or url).hostname):
                    _blocked_until[origin] = max(_blocked_until.get(origin, 0), until)
            response.close()
            raise RateLimited(delay)
        if response.status_code not in RETRY_STATUSES or attempt == 2:
            return response
        response.close()
        time.sleep(attempt + 1)
