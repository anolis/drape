import io
import json
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from drape import desktop, installer, peek, settings


class CinnamonFilterTest(unittest.TestCase):
    def setUp(self):
        for patch in (
            mock.patch.object(desktop, "current_desktop", return_value="cinnamon"),
            mock.patch.object(desktop, "cinnamon_version", return_value=(6, 4)),
            mock.patch.object(settings, "get", side_effect=lambda key: settings.DEFAULTS[key]),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_filter_default_and_toggle_do_not_affect_other_sessions(self):
        self.assertTrue(desktop.hide_outdated_cinnamon())
        for version in ((5, 2), None):
            with mock.patch.object(desktop, "cinnamon_version", return_value=version):
                self.assertFalse(desktop.hide_outdated_cinnamon())
        with mock.patch.object(desktop, "current_desktop", return_value="mate"):
            self.assertFalse(desktop.hide_outdated_cinnamon())
        with mock.patch.object(settings, "get", return_value=False):
            self.assertFalse(desktop.hide_outdated_cinnamon())

    def test_imported_dialog_styles_are_not_flagged_old(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "cinnamon"
            folder.mkdir()
            (folder / "cinnamon.css").write_text('@import "dialogs.css";\n.modal-dialog {}')
            (folder / "dialogs.css").write_text(".prompt-dialog { background: #222; }")
            self.assertFalse(desktop.cinnamon_theme_outdated(folder.parent))
            (folder / "dialogs.css").write_text("/* .dialog {} */ .modal-dialog {}")
            self.assertTrue(desktop.cinnamon_theme_outdated(folder.parent))

    def test_unknown_or_partial_imports_are_not_rejected(self):
        names = ["Theme/cinnamon/cinnamon.css", "Theme/cinnamon/dialogs.css"]
        styles = {names[0]: '@import "dialogs.css"; .modal-dialog {}'}
        self.assertIn("__drape_cinnamon-unknown__", peek._cinnamon_markers(names, styles, False))
        styles[names[1]] = ".prompt-dialog {}"
        self.assertIn("__drape_cinnamon-modern__", peek._cinnamon_markers(names, styles, True))
        self.assertFalse(
            desktop.archive_compatible({"desktop", "cinnamon-legacy"}, True, "desktop")
        )
        self.assertFalse(
            desktop.archive_compatible({"desktop", "cinnamon-unknown"}, False, "desktop")
        )
        with (
            mock.patch.object(settings, "get", return_value=False),
            mock.patch.object(desktop, "supported", return_value=True),
        ):
            self.assertFalse(
                desktop.archive_compatible({"desktop", "cinnamon-legacy"}, True, "desktop")
            )

    def test_zip_peek_reads_css_and_imports_with_bounded_size(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr(
                "Theme/cinnamon/cinnamon.css", '@import "dialogs.css"; .modal-dialog {}'
            )
            archive.writestr("Theme/cinnamon/dialogs.css", ".dialog { background: black; }")
        with (
            mock.patch.object(peek, "_size", return_value=len(data.getvalue())),
            mock.patch.object(peek, "_RangeFile", return_value=io.BytesIO(data.getvalue())),
        ):
            names, complete = peek.list_archive("url", "theme.zip")
        self.assertTrue(complete)
        self.assertIn("cinnamon-modern", peek.classify_names(names))

    def test_tar_peek_flags_legacy_css(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            css = b".modal-dialog { background: black; }"
            member = tarfile.TarInfo("Theme/cinnamon/cinnamon.css")
            member.size = len(css)
            archive.addfile(member, io.BytesIO(css))
        response = SimpleNamespace(
            iter_content=lambda **_: iter([data.getvalue()]),
            status_code=200,
            raise_for_status=lambda: None,
            close=lambda: None,
        )
        with mock.patch.object(peek.requests, "get", return_value=response):
            names, complete = peek.list_archive("url", "theme.tar.gz")
        self.assertIn("cinnamon-legacy", peek.classify_names(names))

    def test_old_cache_entries_are_reinspected_and_unknown_results_cached(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            mock.patch.object(peek, "CACHE", Path(temp) / "peek.json"),
        ):
            peek.CACHE.write_text(
                json.dumps({"1:theme.zip": {"parts": ["desktop"], "complete": True}})
            )
            self.assertIsNone(peek.cached("1", "theme.zip"))
            with mock.patch.object(
                peek, "list_archive", return_value=(["Theme/cinnamon/cinnamon.css"], False)
            ):
                parts, complete = peek.contents("1", "url", "theme.zip")
            self.assertIn("cinnamon-unknown", parts)
            self.assertEqual(peek.cached("1", "theme.zip"), (parts, complete))

    def test_install_rejects_old_theme_before_copy_and_toggle_allows_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "theme.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("Theme/cinnamon/cinnamon.css", ".modal-dialog {}")
            with (
                mock.patch.object(installer, "MANIFEST", root / "records.json"),
                mock.patch.object(installer, "THEMES_DIR", root / "themes"),
            ):
                with self.assertRaises(installer.IncompatibleError):
                    installer.install_file(archive, "1", "Theme", only_applicable=True)
                self.assertFalse((root / "themes").exists())
                with mock.patch.object(settings, "get", return_value=False):
                    installer.install_file(archive, "1", "Theme", only_applicable=False)
                self.assertIn("1", installer.load_manifest())

    def test_menu_toggle_persists_and_refreshes_installed(self):
        from drape.ui.window import Window

        page = SimpleNamespace(load=mock.Mock())
        window = SimpleNamespace(
            reload_all=mock.Mock(),
            installed=page,
            stack=SimpleNamespace(get_visible_child=lambda: page),
        )
        item = SimpleNamespace(get_active=lambda: False)
        with mock.patch.object(settings, "set") as save:
            Window._toggle_applicable(window, item)
        save.assert_called_once_with("only_applicable", False)
        window.reload_all.assert_called_once()
        page.load.assert_called_once()

    def test_unresolved_imports_are_unknown_not_old(self):
        names = ["Theme/cinnamon/cinnamon.css"]
        styles = {names[0]: '@import "../shared/dialogs.css"; .modal-dialog {}'}
        self.assertIn("__drape_cinnamon-unknown__", peek._cinnamon_markers(names, styles, True))
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "cinnamon"
            folder.mkdir()
            (folder / "cinnamon.css").write_text(styles[names[0]])
            self.assertFalse(desktop.cinnamon_theme_outdated(folder.parent))
