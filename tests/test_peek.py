import unittest
import json
import tempfile
from pathlib import Path
from unittest import mock

from drape import peek
import requests
from drape.peek import classify_names


class ClassifyTest(unittest.TestCase):
    def test_empty_partial_inspection_is_reused_until_expiry(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peek, "CACHE", Path(tmp) / "peek.json"),
            mock.patch.object(peek, "list_archive", return_value=(["preview.png"], False)) as read,
            mock.patch.object(peek.time, "time", return_value=1000),
        ):
            self.assertEqual(peek.contents("1", "url", "theme.tar.gz"), (set(), False))
            count = read.call_count
            self.assertEqual(peek.contents("1", "url", "theme.tar.gz"), (set(), False))
            self.assertEqual(read.call_count, count)
            with mock.patch.object(peek.time, "time", return_value=1601):
                self.assertIsNone(peek.cached("1", "theme.tar.gz"))

    def test_legacy_empty_cache_is_ignored_but_positive_cache_is_reused(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peek, "CACHE", Path(tmp) / "peek.json"),
        ):
            peek.CACHE.write_text(
                json.dumps(
                    {
                        "1:empty.zip": {"parts": [], "complete": False},
                        "1:good.zip": {"parts": ["icons"], "complete": True},
                    }
                )
            )
            self.assertIsNone(peek.cached("1", "empty.zip"))
            self.assertEqual(peek.cached("1", "good.zip"), ({"icons"}, True))

    def test_transport_failure_does_not_write_cache(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peek, "CACHE", Path(tmp) / "peek.json"),
            mock.patch.object(peek, "list_archive", side_effect=requests.ConnectionError("failed")),
        ):
            with self.assertRaises(peek.InspectionFailed):
                peek.contents("1", "url", "theme.zip")
            self.assertFalse(peek.CACHE.exists())

    def test_catalog_rate_limit_survives_link_refresh(self):
        from drape import pling

        cause = peek.RateLimited(30, host="api.test")
        error = pling.PlingError("Catalog failed")
        error.__cause__ = cause
        with mock.patch.object(pling, "get", side_effect=error):
            with self.assertRaises(peek.RateLimited) as caught:
                peek.fresh_item(mock.Mock(id="1"))
            self.assertIs(caught.exception, cause)

    def test_zip_transport_failure_wrapped_as_bad_zip_is_not_evidence(self):
        size = mock.Mock(
            status_code=200,
            headers={"content-range": "bytes 0-0/1000"},
            raise_for_status=lambda: None,
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peek, "CACHE", Path(tmp) / "peek.json"),
            mock.patch.object(
                peek.http, "get", side_effect=[size, requests.ConnectionError("failed")]
            ),
        ):
            with self.assertRaises(peek.InspectionFailed):
                peek.contents("1", "url", "theme.zip")
            self.assertFalse(peek.CACHE.exists())

    def test_rate_limited_inspection_does_not_write_archive_cache(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peek, "CACHE", Path(tmp) / "peek.json"),
            mock.patch.object(peek, "list_archive", side_effect=peek.RateLimited(120)),
        ):
            with self.assertRaises(peek.RateLimited):
                peek.contents("1", "https://example.test/theme.zip", "theme.zip")
            self.assertFalse(peek.CACHE.exists())

    def test_rate_limit_inside_zip_end_record_is_not_a_bad_zip(self):
        size = mock.Mock(
            status_code=200,
            headers={"content-range": "bytes 0-0/1000"},
            raise_for_status=lambda: None,
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peek, "CACHE", Path(tmp) / "peek.json"),
            mock.patch.object(peek.http, "get", side_effect=[size, peek.RateLimited(30)]),
        ):
            with self.assertRaises(peek.RateLimited) as error:
                peek.contents("1", "https://example.test/theme.zip", "theme.zip")
            self.assertEqual(error.exception.retry_after, 30)
            self.assertFalse(peek.CACHE.exists())

    def test_parts_from_paths(self):
        self.assertEqual(
            classify_names(
                [
                    "T/gtk-3.0/gtk.css",
                    "T/metacity-1/metacity-theme-3.xml",
                    "T/cinnamon/cinnamon.css",
                ]
            ),
            {"gtk", "gtk-3.0", "wm", "desktop"},
        )
        self.assertEqual(classify_names(["C/index.theme", "C/cursors/left_ptr"]), {"cursors"})
        self.assertEqual(classify_names(["pack/Normal.cur", "pack/Busy.ani"]), {"cursors"})
        self.assertEqual(classify_names(["s/s.plymouth", "s/s.script"]), {"plymouth"})
        self.assertEqual(classify_names(["x/metadata.desktop", "x/Main.qml"]), {"login", "sddm"})

    def test_icon_theme_layouts(self):
        std = [f"Frost/32x32/places/f{i}.png" for i in range(12)]
        self.assertEqual(classify_names(std), {"icons"})
        reversed_layout = [f"Slot/status/scalable/s{i}.svg" for i in range(12)]
        self.assertEqual(classify_names(reversed_layout), {"icons"})
        hidpi = ["P/index.theme", "P/24x24@2x/apps/a.svg"]
        self.assertEqual(classify_names(hidpi), {"icons"})

    def test_loose_pictures_are_wallpapers_not_icons(self):
        self.assertEqual(
            classify_names(["pack/red-folder.png", "pack/blue-folder.png", "pack/x.png"]),
            {"wallpapers"},
        )

    def test_theme_assets_and_previews_are_not_wallpapers(self):
        names = ["T/gtk-3.0/assets/check.png", "T/preview.png", "T/gtk-3.0/gtk.css"]
        self.assertEqual(classify_names(names), {"gtk", "gtk-3.0"})

    def test_window_manager_formats_are_distinct(self):
        self.assertEqual(classify_names(["T/xfwm4/themerc"]), {"xfwm"})
        self.assertEqual(classify_names(["T/metacity-1/metacity-theme-1.xml"]), {"wm"})
        self.assertEqual(
            classify_names(["T/gnome-shell/gnome-shell.css", "T/gnome-shell/a.png"]),
            {"gnome-shell"},
        )

    def test_nested_archive_is_not_proof_of_incompatibility(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peek, "CACHE", Path(tmp) / "peek.json"),
            mock.patch.object(
                peek, "list_archive", return_value=(["theme.tar.gz", "preview.png"], True)
            ),
        ):
            _parts, complete = peek.contents("1", "https://example.invalid/theme.zip", "theme.zip")
            self.assertFalse(complete)


if __name__ == "__main__":
    unittest.main()
