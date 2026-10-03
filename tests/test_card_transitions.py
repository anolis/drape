import unittest
from types import MethodType, SimpleNamespace
from unittest import mock

from drape.ui.card_transitions import CardFlow, DURATION_US, FadingCard


class CardTransitionsTest(unittest.TestCase):
    def card(self, mapped=True, animations=True):
        card = SimpleNamespace(_tick=None, _finish=None, opacity=1.0, departing=False)
        card.get_opacity = lambda: card.opacity
        card.set_opacity = lambda value: setattr(card, "opacity", value)
        card.get_mapped = lambda: mapped
        card.get_parent = lambda: None
        card.get_settings = lambda: SimpleNamespace(get_property=lambda _name: animations)
        card.get_frame_clock = lambda: SimpleNamespace(get_frame_time=lambda: 0)
        card.add_tick_callback = mock.Mock(return_value=42)
        card.remove_tick_callback = mock.Mock()
        card.destroy = mock.Mock()
        card.set_sensitive = mock.Mock()
        card._cancel = MethodType(FadingCard._cancel, card)
        card.fade = MethodType(FadingCard.fade, card)
        return card

    def test_removal_waits_for_fade_and_disables_actions(self):
        card = self.card()
        FadingCard.dismiss(card)
        card.destroy.assert_not_called()
        card.set_sensitive.assert_called_once_with(False)
        tick = card.add_tick_callback.call_args.args[0]
        self.assertTrue(tick(card, SimpleNamespace(get_frame_time=lambda: DURATION_US / 2)))
        self.assertAlmostEqual(card.opacity, 0.5)
        self.assertFalse(tick(card, SimpleNamespace(get_frame_time=lambda: DURATION_US)))
        card.destroy.assert_called_once()
        self.assertIsNone(card._tick)

    def test_reversing_fade_cancels_old_completion(self):
        card = self.card()
        hidden = mock.Mock()
        card.fade(0, hidden)
        card.fade(1)
        card.remove_tick_callback.assert_called_once_with(42)
        tick = card.add_tick_callback.call_args.args[0]
        tick(card, SimpleNamespace(get_frame_time=lambda: DURATION_US))
        hidden.assert_not_called()
        self.assertEqual(card.opacity, 1)

    def test_hidden_card_and_disabled_animations_remove_immediately(self):
        for mapped, animations in ((False, True), (True, False)):
            card = self.card(mapped, animations)
            FadingCard.dismiss(card)
            card.destroy.assert_called_once()
            card.add_tick_callback.assert_not_called()

    def test_unmapping_finishes_pending_removal_without_a_timer(self):
        card = self.card()
        FadingCard.dismiss(card)
        FadingCard._unmapped(card)
        card.destroy.assert_called_once()
        self.assertIsNone(card._tick)
        self.assertIsNone(card._finish)

    def test_refresh_retains_unchanged_cards_and_removes_missing_ones(self):
        keep = SimpleNamespace(_identity="keep", _snapshot={"title": "Keep"}, dismiss=mock.Mock())
        gone = SimpleNamespace(_identity="gone", _snapshot={}, dismiss=mock.Mock())
        flow = SimpleNamespace(
            cards=lambda: [keep, gone],
            add=mock.Mock(),
            set_sort_func=mock.Mock(),
            invalidate_sort=mock.Mock(),
            show_all=mock.Mock(),
        )
        create = mock.Mock()
        CardFlow.reconcile(flow, [("keep", {"title": "Keep"}, create)])
        create.assert_not_called()
        flow.add.assert_not_called()
        keep.dismiss.assert_not_called()
        gone.dismiss.assert_called_once()

    def test_offscreen_removal_waits_until_card_reenters_view(self):
        card = mock.Mock()
        remove = mock.Mock()
        flow = SimpleNamespace(_deferred={}, in_view=mock.Mock(return_value=False))
        card.get_parent.return_value = flow
        CardFlow.defer_removal(flow, card, remove)
        remove.assert_not_called()
        self.assertIn(card, flow._deferred)
        flow.in_view.return_value = True
        CardFlow._check_deferred(flow)
        remove.assert_called_once()
        self.assertEqual(flow._deferred, {})

    def test_visible_removal_starts_without_waiting_for_scroll(self):
        card, remove = mock.Mock(), mock.Mock()
        flow = SimpleNamespace(_deferred={}, in_view=lambda _card: True)
        CardFlow.defer_removal(flow, card, remove)
        remove.assert_called_once()
        self.assertEqual(flow._deferred, {})

    def test_scrolling_away_during_a_fade_defers_the_layout_change(self):
        card = mock.Mock(_wanted=False, _included=True)
        flow = SimpleNamespace(
            _deferred={},
            in_view=lambda _card: False,
            invalidate_filter=mock.Mock(),
            _hide_card=mock.Mock(),
        )
        flow.defer_removal = MethodType(CardFlow.defer_removal, flow)
        CardFlow._finish_hide(flow, card)
        self.assertTrue(card._included)
        self.assertIn(card, flow._deferred)
        flow.invalidate_filter.assert_not_called()

    def test_reversing_filter_cancels_offscreen_removal(self):
        card = mock.Mock(departing=False, _wanted=False, _included=True)
        remove = mock.Mock()
        flow = SimpleNamespace(
            _deferred={card: remove},
            _predicate=lambda _card: True,
            cards=lambda: [card],
            invalidate_filter=mock.Mock(),
        )
        CardFlow.refilter(flow)
        self.assertEqual(flow._deferred, {})
        self.assertTrue(card._included)
        remove.assert_not_called()
        card.fade.assert_called_once_with(1)

    def test_clear_discards_offscreen_departures_without_leaving_old_rows(self):
        card = mock.Mock()
        flow = SimpleNamespace(_deferred={card: mock.Mock()}, get_children=lambda: [card])
        CardFlow.clear(flow)
        self.assertEqual(flow._deferred, {})
        card.dismiss.assert_called_once_with(defer=False)
