import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from drape import http, installer


class HttpRetryTest(unittest.TestCase):
    def setUp(self):
        cooldowns = mock.patch.dict(http._blocked_until, {}, clear=True)
        cooldowns.start()
        self.addCleanup(cooldowns.stop)
        slots = mock.patch.dict(http._retry_slots, {}, clear=True)
        slots.start()
        self.addCleanup(slots.stop)

    def test_retry_after_is_bounded_for_numbers_and_dates(self):
        self.assertEqual(http._retry_after("99999999999"), http.MAX_RETRY_AFTER)
        with mock.patch.object(http.time, "time", return_value=0):
            self.assertEqual(
                http._retry_after("Fri, 01 Jan 2100 00:00:00 GMT"), http.MAX_RETRY_AFTER
            )

    def test_background_retries_are_spaced_per_host(self):
        with mock.patch.object(http.time, "monotonic", return_value=100):
            self.assertEqual(http.RateLimited(60, host="files.test").retry_delay(), 60)
            self.assertEqual(http.RateLimited(60, host="files.test").retry_delay(), 62)
            self.assertEqual(http.RateLimited(60, host="other.test").retry_delay(), 60)
            self.assertEqual(http.RateLimited(120, host="files.test").retry_delay(), 120)

    def test_background_server_error_does_not_sleep_or_retry(self):
        response = mock.Mock(status_code=503)
        with (
            mock.patch.object(http.requests, "get", return_value=response) as get,
            mock.patch.object(http.time, "sleep") as sleep,
        ):
            self.assertIs(
                http.get("https://example.test/theme", retry_server_errors=False), response
            )
            get.assert_called_once()
            sleep.assert_not_called()

    def limited_response(self, retry="90"):
        response = requests.Response()
        response.status_code = 429
        response.url = "https://example.test/download?token=secret"
        response.headers["Retry-After"] = retry
        response._content = b"Too many requests"
        response._content_consumed = True
        return response

    def test_rate_limit_closes_response_and_does_not_retry_immediately(self):
        response = self.limited_response()
        with (
            mock.patch.object(http.requests, "get", return_value=response) as get,
            mock.patch.object(response, "close") as close,
            mock.patch.object(http.time, "sleep") as sleep,
        ):
            with self.assertRaises(http.RateLimited) as error:
                http.get(response.url)
            self.assertEqual(error.exception.retry_after, 90)
            self.assertNotIn("secret", str(error.exception))
            get.assert_called_once()
            close.assert_called_once()
            sleep.assert_not_called()

    def test_host_cooldown_blocks_scan_and_download_but_allows_other_hosts(self):
        with (
            mock.patch.object(
                http.requests,
                "get",
                side_effect=[self.limited_response("20"), mock.Mock(status_code=200)],
            ) as get,
            mock.patch.object(http.time, "monotonic", return_value=100),
        ):
            with self.assertRaises(http.RateLimited):
                http.get("https://example.test/first")
            with self.assertRaises(http.RateLimited):
                http.get("https://example.test/second")
            http.get("https://other.test/theme")
            self.assertEqual(get.call_count, 2)

    def test_host_cooldown_expires_and_request_can_succeed(self):
        with (
            mock.patch.object(
                http.requests,
                "get",
                side_effect=[self.limited_response("20"), mock.Mock(status_code=200)],
            ) as get,
            mock.patch.object(http.time, "monotonic", return_value=100),
        ):
            with self.assertRaises(http.RateLimited):
                http.get("https://example.test/first")
        with (
            mock.patch.object(http.requests, "get", return_value=mock.Mock(status_code=200)) as get,
            mock.patch.object(http.time, "monotonic", return_value=121),
        ):
            self.assertEqual(http.get("https://example.test/second").status_code, 200)
            get.assert_called_once()

    def test_retry_after_http_date_and_invalid_header(self):
        with mock.patch.object(http.time, "time", return_value=0):
            self.assertEqual(http._retry_after("Thu, 01 Jan 1970 00:02:00 GMT"), 120)
        for header in (None, "invalid"):
            self.assertEqual(http._retry_after(header), 60)

    def test_install_rate_limit_reports_retry_and_creates_no_file(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(http.requests, "get", return_value=self.limited_response("30")),
        ):
            with self.assertRaises(installer.InstallError) as error:
                installer.download("https://example.test/theme?token=secret", tmp, "theme.zip")
            self.assertNotIsInstance(error.exception, installer.IncompatibleError)
            self.assertIn("rate-limiting", str(error.exception))
            self.assertIn("30 seconds", str(error.exception))
            self.assertNotIn("secret", str(error.exception))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_server_error_recovers_and_closes_failed_response(self):
        failed, good = mock.Mock(status_code=500), mock.Mock(status_code=200)
        with (
            mock.patch.object(http.requests, "get", side_effect=[failed, good]) as get,
            mock.patch.object(http.time, "sleep") as sleep,
        ):
            self.assertIs(http.get("https://example.test/theme", stream=True), good)
            self.assertEqual(get.call_count, 2)
            failed.close.assert_called_once()
            good.close.assert_not_called()
            sleep.assert_called_once_with(1)

    def test_permanent_failure_has_bounded_retries(self):
        responses = [mock.Mock(status_code=503) for _ in range(3)]
        with (
            mock.patch.object(http.requests, "get", side_effect=responses) as get,
            mock.patch.object(http.time, "sleep") as sleep,
        ):
            self.assertIs(http.get("https://example.test/theme"), responses[-1])
            self.assertEqual(get.call_count, 3)
            self.assertEqual(sleep.call_args_list, [mock.call(1), mock.call(2)])

    def test_missing_file_is_not_retried(self):
        response = mock.Mock(status_code=404)
        with (
            mock.patch.object(http.requests, "get", return_value=response) as get,
            mock.patch.object(http.time, "sleep") as sleep,
        ):
            self.assertIs(http.get("https://example.test/missing"), response)
            get.assert_called_once()
            sleep.assert_not_called()

    def test_download_reports_host_without_leaking_signed_url(self):
        url = "https://files06.pling.com/file?token=secret"
        responses = []
        for _ in range(3):
            response = requests.Response()
            response.status_code = 500
            response.url = url
            response._content = b"Server error"
            response._content_consumed = True
            responses.append(response)
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(http.requests, "get", side_effect=responses),
            mock.patch.object(http.time, "sleep"),
        ):
            with self.assertRaises(installer.InstallError) as error:
                installer.download(url, tmp, "Arc-Dark.tar.gz")
            self.assertIn(
                "files06.pling.com returned HTTP 500 after 3 attempts", str(error.exception)
            )
            self.assertNotIn("secret", str(error.exception))
            self.assertEqual(list(Path(tmp).iterdir()), [])
