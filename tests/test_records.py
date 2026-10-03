import json
import multiprocessing
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from drape import installer
from drape.records import InstallError, ManifestStore


def add_records(path, prefix):
    for i in range(12):
        with ManifestStore(path).edit() as records:
            records[f'{prefix}-{i}'] = {'paths': [], 'components': []}


class RecordsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ManifestStore(self.root / 'installed.json')

    def test_process_updates_preserve_every_record(self):
        workers = [multiprocessing.Process(target=add_records, args=(self.store.path, str(i)))
                   for i in range(3)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
            if worker.is_alive():
                worker.terminate()
                self.fail('record lock did not finish')
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(len(self.store.load()), 36)
        self.assertEqual(len(json.loads(self.store.backup.read_text())), 35)

    def test_invalid_records_are_never_reset_or_overwritten(self):
        for text in ('{broken', '[]', '{"1": {"paths": "unsafe", "components": []}}'):
            self.store.path.write_text(text)
            with self.assertRaises(InstallError):
                self.store.load()
            with self.assertRaises(InstallError):
                self.store.save({})
            self.assertEqual(self.store.path.read_text(), text)
            self.assertFalse(self.store.backup.exists())

    def test_failed_edit_preserves_records_and_backup(self):
        self.store.save({'1': {'paths': [], 'components': []}})
        self.store.save({'2': {'paths': [], 'components': []}})
        with self.assertRaises(RuntimeError):
            with self.store.edit() as records:
                records.clear()
                raise RuntimeError('cancelled')
        self.assertEqual(set(self.store.load()), {'2'})
        self.assertEqual(set(json.loads(self.store.backup.read_text())), {'1'})

    def test_missing_records_with_backup_are_not_treated_as_empty(self):
        self.store.save({})
        self.store.save({})
        self.store.path.unlink()
        with self.assertRaises(InstallError):
            self.store.save({})
        self.assertFalse(self.store.path.exists())

    def test_failed_atomic_write_keeps_last_records(self):
        self.store.save({'1': {'paths': [], 'components': []}})
        with mock.patch('drape.records.os.fsync', side_effect=OSError('disk error')):
            with self.assertRaises(InstallError):
                self.store.save({})
        self.assertEqual(set(self.store.load()), {'1'})
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_overlapping_installs_and_metadata_updates_are_preserved(self):
        archives = []
        for name in ('A', 'B'):
            archive = self.root / f'{name}.zip'
            with zipfile.ZipFile(archive, 'w') as output:
                output.writestr(f'{name}/index.theme', '[Icon Theme]\nName=' + name + '\nDirectories=48x48/apps\n')
                output.writestr(f'{name}/48x48/apps/test.svg', '<svg/>')
            archives.append(archive)
        copying = threading.Event()
        release = threading.Event()
        queued = threading.Event()
        real_copy = installer.shutil.copytree

        def copy(src, dest, *args, **kwargs):
            if Path(dest).name == 'A':
                copying.set()
                if not release.wait(5):
                    raise RuntimeError('copy timed out')
            return real_copy(src, dest, *args, **kwargs)

        def status(text, fraction=None):
            if text == 'Waiting to install…':
                queued.set()

        with mock.patch.object(installer, 'MANIFEST', self.store.path), \
                mock.patch.object(installer, 'ICONS_DIR', self.root / 'icons'), \
                mock.patch.object(installer.shutil, 'copytree', side_effect=copy), \
                mock.patch.object(installer.shutil, 'which', return_value=None):
            self.store.save({'A': {'paths': [], 'components': []}})
            with ThreadPoolExecutor(2) as pool:
                first = pool.submit(installer.install_file, archives[0], 'A', 'A', only_applicable=False)
                try:
                    self.assertTrue(copying.wait(5))
                    second = pool.submit(installer.install_file, archives[1], 'B', 'B',
                                         only_applicable=False, status=status)
                    self.assertTrue(queued.wait(5))
                    installer.set_preview('A', 'preview-url')
                    installer.set_chosen('A', 'icons', 'A')
                    with self.store.edit() as records:
                        records['unrelated'] = {'paths': [], 'components': []}
                finally:
                    release.set()
                first.result(timeout=10)
                second.result(timeout=10)
            records = self.store.load()
            self.assertEqual(set(records), {'A', 'B', 'unrelated'})
            self.assertEqual(records['A']['preview'], 'preview-url')
            self.assertEqual(records['A']['chosen'], {'icons': 'A'})
            for key in ('A', 'B'):
                self.assertTrue(Path(records[key]['paths'][0]).exists())

    def test_concurrent_installs_preserve_all_theme_categories(self):
        packs = {
            'controls': {'Controls/gtk-3.0/gtk.css': '/* theme */',
                         'Controls/cinnamon/cinnamon.css': '/* theme */',
                         'Controls/metacity-1/metacity-theme-3.xml': '<metacity_theme/>'},
            'cursor': {'Cursor/cursors/left_ptr': b'Xcur' + bytes(12)},
            'wallpaper': {'background.jpg': b'image'},
            'colors': {'Scheme.colors': '[General]\nName=Scheme\n[Colors:Window]\nBackgroundNormal=0,0,0'},
        }
        archives = {}
        for key, files in packs.items():
            archives[key] = self.root / (key + '.zip')
            with zipfile.ZipFile(archives[key], 'w') as output:
                for name, content in files.items():
                    output.writestr(name, content)
        starting = threading.Barrier(len(packs))

        def install(key):
            starting.wait(timeout=5)
            return installer.install_file(archives[key], key, key, only_applicable=False)

        with mock.patch.object(installer, 'MANIFEST', self.store.path), \
                mock.patch.object(installer, 'THEMES_DIR', self.root / 'themes'), \
                mock.patch.object(installer, 'CURSORS_DIR', self.root / 'cursors'), \
                mock.patch.object(installer, 'WALLPAPER_DIR', self.root / 'backgrounds'), \
                mock.patch.object(installer.kde, 'DATA_HOME', self.root / 'kde'), \
                mock.patch('drape.desktop.current_desktop', return_value='mate'):
            with ThreadPoolExecutor(len(packs)) as pool:
                list(pool.map(install, packs))
        records = self.store.load()
        self.assertEqual(set(records), set(packs))
        kinds = {kind for entry in records.values() for component in entry['components']
                 for kind in component['provides']}
        self.assertEqual(kinds, {'gtk', 'desktop', 'wm', 'cursors', 'wallpapers', 'colors'})
        for entry in records.values():
            self.assertTrue(all(Path(path).exists() for path in entry['paths']))


class RecordErrorUITest(unittest.TestCase):
    def test_startup_reports_record_error_before_creating_window(self):
        from types import SimpleNamespace
        from drape.ui import application
        app = SimpleNamespace(get_active_window=lambda: None)
        with mock.patch.object(installer, 'load_manifest', side_effect=InstallError('unreadable')), \
                mock.patch.object(application, 'Window') as window, \
                mock.patch.object(application, 'error_dialog') as dialog:
            self.assertIsNone(application.App._window(app))
            window.assert_not_called()
            dialog.assert_called_once()

    def test_installed_refresh_reports_error_without_clearing_cards(self):
        from types import SimpleNamespace
        from drape.ui import installed
        page = SimpleNamespace(_sync_category_visibility=mock.Mock(), win=mock.Mock(),
                               selected={('1', None)}, grids=mock.Mock())
        with mock.patch.object(installer, 'load_manifest', side_effect=InstallError('unreadable')), \
                mock.patch.object(installed, 'error_dialog') as dialog:
            installed.InstalledPage.load(page)
            dialog.assert_called_once()
            self.assertEqual(page.selected, {('1', None)})
            self.assertEqual(page.grids.mock_calls, [])
