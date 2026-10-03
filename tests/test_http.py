import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from drape import http, installer


class HttpRetryTest(unittest.TestCase):
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
