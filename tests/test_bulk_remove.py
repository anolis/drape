import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from drape import installer
from drape.ui import theme_actions
from drape.ui.window import Window


class BulkRemoveTest(unittest.TestCase):
    def setUp(self):
        self.a = {"path": "/walls/a.jpg", "name": "a", "provides": ["wallpapers"]}
        self.b = {"path": "/walls/b.jpg", "name": "b", "provides": ["wallpapers"]}
        self.entry = {"title": "Wallpapers", "components": [self.a, self.b],
                      "paths": ["/walls/a.jpg", "/walls/b.jpg"]}
        self.manifest = {"1": self.entry}

    def test_whole_pack_supersedes_duplicate_wallpapers(self):
        plan = installer.removal_plan([("1", "/walls/a.jpg"), ("1", None), ("1", None)], self.manifest)
        self.assertEqual(plan, [("1", None, self.entry)])

    def test_single_wallpaper_plan_keeps_siblings_out_of_system_removal(self):
        plan = installer.removal_plan([("1", "/walls/a.jpg")], self.manifest)
        self.assertEqual(plan[0][2]["components"], [self.a])
        self.assertEqual(self.entry["components"], [self.a, self.b])

    def test_stale_selection_rejected(self):
        for selection in ([('missing', None)], [('1', '/walls/missing.jpg')]):
            with self.assertRaises(installer.InstallError):
                installer.removal_plan(selection, self.manifest)

    def window(self):
        return SimpleNamespace(installed=SimpleNamespace(selected={("1", "/walls/a.jpg"), ("1", "/walls/b.jpg")}),
                               refresh_item=mock.Mock(), notify=mock.Mock(), run_root=mock.Mock())

    def test_cancel_does_not_delete_anything(self):
        window = self.window()
        with mock.patch.object(installer, "load_manifest", return_value=self.manifest), \
                mock.patch.object(theme_actions, "system_copies", return_value=[]), \
                mock.patch.object(theme_actions, "in_use", return_value=True), \
                mock.patch.object(theme_actions.Gtk, "MessageDialog") as dialog, \
                mock.patch.object(installer, "remove_component") as remove:
            dialog.return_value.run.return_value = theme_actions.Gtk.ResponseType.CANCEL
            Window.remove_selected(window, window.installed.selected)
            remove.assert_not_called()
            window.run_root.assert_not_called()

    def test_failure_keeps_failed_selection_and_reports_successes(self):
        window = self.window()
        with mock.patch.object(installer, "load_manifest", return_value=self.manifest), \
                mock.patch.object(theme_actions, "system_copies", return_value=[]), \
                mock.patch.object(theme_actions, "in_use", return_value=False), \
                mock.patch.object(theme_actions.Gtk, "MessageDialog") as dialog, \
                mock.patch.object(theme_actions, "error_dialog") as error, \
                mock.patch.object(installer, "remove_component", side_effect=[None, PermissionError("denied")]):
            dialog.return_value.run.return_value = theme_actions.Gtk.ResponseType.ACCEPT
            Window.remove_selected(window, set(window.installed.selected))
            self.assertEqual(window.installed.selected, {("1", "/walls/b.jpg")})
            error.assert_called_once()
            window.notify.assert_called_once_with("Deleted 1 selected item(s).")

    def test_system_removal_completes_before_user_files_are_removed(self):
        window = self.window()
        with mock.patch.object(installer, "load_manifest", return_value=self.manifest), \
                mock.patch.object(theme_actions, "system_copies", return_value=[("background", "copy", Path('/system'), "copy")]), \
                mock.patch.object(theme_actions, "in_use", return_value=False), \
                mock.patch.object(theme_actions.Gtk, "MessageDialog") as dialog, \
                mock.patch.object(installer, "remove_component") as remove:
            dialog.return_value.run.return_value = theme_actions.Gtk.ResponseType.ACCEPT
            Window.remove_selected(window, set(window.installed.selected))
            remove.assert_not_called()
            self.assertEqual(window.run_root.call_args.args[0], [["uninstall", "background", "copy"]])
            window.run_root.call_args.args[2]()
            self.assertEqual(remove.call_count, 2)

    def test_delete_two_images_preserves_unselected_image_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / 'walls' / 'pack'
            folder.mkdir(parents=True)
            images = [folder / name for name in ('a.jpg', 'b.jpg', 'keep.jpg')]
            for image in images:
                image.touch()
            entry = dict(title='pack', paths=[str(p) for p in images] + [str(folder)],
                         components=[dict(path=str(p), name=p.stem, provides=['wallpapers']) for p in images])
            with mock.patch.object(installer, 'WALLPAPER_DIR', folder.parent), \
                    mock.patch.object(installer, 'MANIFEST', root / 'manifest.json'):
                installer.save_manifest({'1': entry})
                for image in images[:2]:
                    installer.remove_component('1', str(image))
                self.assertTrue(images[2].is_file())
                self.assertFalse(images[0].exists())
                self.assertEqual(len(installer.load_manifest()['1']['components']), 1)

    def test_symlink_parent_cannot_delete_outside_install_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'allowed').mkdir()
            (root / 'outside').mkdir()
            target = root / 'outside' / 'keep'
            target.touch()
            (root / 'allowed' / 'link').symlink_to(root / 'outside')
            with mock.patch.object(installer, 'WALLPAPER_DIR', root / 'allowed'), self.assertRaises(installer.InstallError):
                installer._remove_path(root / 'allowed' / 'link' / 'keep')
            self.assertTrue(target.exists())

    def test_directory_delete_failure_keeps_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            theme = root / 'themes' / 'Example'
            theme.mkdir(parents=True)
            with mock.patch.object(installer, 'THEMES_DIR', theme.parent), \
                    mock.patch.object(installer, 'MANIFEST', root / 'manifest.json'), \
                    mock.patch.object(installer.shutil, 'rmtree', side_effect=PermissionError('denied')):
                installer.save_manifest({'1': dict(title='Example', paths=[str(theme)], components=[])})
                with self.assertRaises(PermissionError):
                    installer.remove('1')
                self.assertIn('1', installer.load_manifest())
