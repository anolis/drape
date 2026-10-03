import unittest
from types import SimpleNamespace
from unittest import mock

from drape import desktop, installer
from drape.ui.installed import InstalledPage
from drape.ui.widgets import ApplyControl


class InstalledLoadingTest(unittest.TestCase):
    def test_only_selected_category_is_reconciled_and_restored(self):
        icons, cursors = mock.Mock(), mock.Mock()
        icon_scroll, cursor_scroll = mock.Mock(), mock.Mock()
        page = SimpleNamespace(
            _entries={"icons": ["icons"], "cursors": ["cursors"]},
            grids={
                "icons": (None, icon_scroll, icons, None),
                "cursors": (None, cursor_scroll, cursors, None),
            },
        )
        InstalledPage._show_category(page, "icons")
        icons.reconcile.assert_called_once_with(["icons"])
        icon_scroll._scroll_state.finish.assert_not_called()
        cursors.reconcile.assert_not_called()
        InstalledPage._show_category(page, "icons")
        self.assertEqual(icons.reconcile.call_count, 1)
        InstalledPage._show_category(page, "cursors")
        cursors.reconcile.assert_called_once_with(["cursors"])

    def test_apply_control_uses_snapshot_for_display_and_fresh_records_for_actions(self):
        old = dict(name="Old", path="/themes/old", provides=["icons"])
        new = dict(name="New", path="/themes/new", provides=["icons"])
        control = SimpleNamespace(key="1", kind="icons")
        with (
            mock.patch.object(
                installer, "load_manifest", return_value={"1": {"components": [new]}}
            ) as records,
            mock.patch.object(desktop, "compatible_parts", side_effect=lambda c: c["provides"]),
        ):
            self.assertEqual(ApplyControl._components(control, {"components": [old]}), [old])
            records.assert_not_called()
            self.assertEqual(ApplyControl._components(control), [new])
            records.assert_called_once()
