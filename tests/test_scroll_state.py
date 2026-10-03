import unittest
from types import SimpleNamespace
from unittest import mock

from drape.ui import scroll_state


class ScrollStateTest(unittest.TestCase):
    def tracker(self, saved=2000):
        scroller = mock.Mock()
        scroller.get_mapped.return_value = True
        adj = scroller.get_vadjustment.return_value
        adj.get_upper.return_value = 1000
        adj.get_page_size.return_value = 500
        adj.get_value.return_value = 500
        with mock.patch.object(scroll_state.settings, "get", return_value={"section": saved}):
            state = scroll_state.ScrollState(scroller, "section")
        return state, adj

    def test_deep_scroll_waits_for_content_height(self):
        state, adj = self.tracker()
        state.restore()
        adj.set_value.assert_called_with(500)
        self.assertEqual(state.pending, 2000)
        with mock.patch.object(scroll_state.settings, "set") as write:
            state.save()
            write.assert_not_called()
        adj.get_upper.return_value = 3000
        state.restore()
        adj.set_value.assert_called_with(2000)
        self.assertIsNone(state.pending)

    def test_saves_each_section_independently(self):
        state, adj = self.tracker(0)
        state.restore()
        adj.get_value.return_value = 750
        with (
            mock.patch.object(scroll_state.settings, "get", return_value={"other": 900}),
            mock.patch.object(scroll_state.settings, "set") as write,
        ):
            state.save()
            write.assert_called_once_with("scroll_positions", {"other": 900, "section": 750})

    def test_shortened_catalog_finishes_restore_at_reachable_position(self):
        state, adj = self.tracker()
        with mock.patch.object(scroll_state.GLib, "timeout_add", return_value=1):
            state.finish()
        self.assertIsNone(state.pending)
        adj.set_value.assert_called_with(500)
