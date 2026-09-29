"""Bounded retries for read-only catalog and file requests."""

import time

import requests

RETRY_STATUSES = {500, 502, 503, 504}


def get(url, **kwargs):
    """Retry transient server failures twice, closing failed responses before retrying."""
    for attempt in range(3):
        response = requests.get(url, **kwargs)
        if response.status_code not in RETRY_STATUSES or attempt == 2:
            return response
        response.close()
        time.sleep(attempt + 1)
