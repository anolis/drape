"""Browsing consumes index evidence and never starts archive inspection."""

import unittest
from types import SimpleNamespace
from unittest import mock

from drape import pling
from drape.ui import browse


class BrowseIndexTest(unittest.TestCase):
    def item(self):
        return pling.Item(
            "1",
            "Theme",
            "",
            "",
            "",
            "",
            0,
            0,
            "",
            files=[pling.Download(1, "theme.zip", "url", 1, "")],
        )

    def test_batches_publish_from_local_evidence_without_scanning(self):
        item = self.item()
        page = SimpleNamespace(generation=1, _scanned_checks={})
        ready = mock.Mock()
        checks = {"1": {1: ({"icons"}, True)}}
        with (
            mock.patch.object(browse.compatibility.Index, "read_many", return_value=checks),
            mock.patch.object(browse.peek, "inspect_downloads") as scan,
            mock.patch.object(browse.peek, "contents") as contents,
        ):
            browse.BrowsePage._preflight(page, [item], ready, mock.Mock(), 1)
            ready.assert_called_once()
            self.assertEqual(page._scanned_checks, checks)
            scan.assert_not_called()
            contents.assert_not_called()

    def test_stale_batch_never_reads_or_changes_index_results(self):
        page = SimpleNamespace(generation=2, _scanned_checks={})
        ready = mock.Mock()
        with mock.patch.object(browse.compatibility.Index, "read_many") as read:
            browse.BrowsePage._preflight(page, [self.item()], ready, mock.Mock(), 1)
            ready.assert_not_called()
            read.assert_not_called()
            self.assertEqual(page._scanned_checks, {})

    def test_profile_card_uses_only_existing_index(self):
        card = SimpleNamespace(
            _scan_alive=True, _checks=None, item=self.item(), _show_glyphs=mock.Mock()
        )
        with (
            mock.patch.object(
                browse.compatibility.Index, "read_many", return_value={"1": {1: ({"icons"}, True)}}
            ),
            mock.patch.object(browse.peek, "contents") as scan,
        ):
            browse.Card._peek(card, None)
            card._show_glyphs.assert_called_once_with({"icons"}, True, checked=True)
            scan.assert_not_called()
