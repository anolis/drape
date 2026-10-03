import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from drape.ui import packs, theme_actions


class ComponentChoiceTest(unittest.TestCase):
    def test_companion_reference_is_separate_only_when_its_files_are_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "index.theme").write_text("[X-GNOME-Metatheme]\nIconTheme=Companion Icons\n")
            entry = {"components": [{"path": str(root), "provides": ["gtk"]}]}
            self.assertIn("separate download", packs.component_notes(entry, {"gtk": []}))
            entry["components"].append({"path": str(root), "provides": ["icons"]})
            # Selecting only Controls must not mislabel bundled icons as external.
            self.assertNotIn("separate download", packs.component_notes(entry, {"gtk": []}))

    def test_multi_component_prompt_routes_all_current_and_later(self):
        window = SimpleNamespace(notify=mock.Mock())
        entry = {"title": "Bundle", "components": []}
        for response, expected in ((2, None), (1, "gtk"), (-6, "later")):
            with (
                self.subTest(response=response),
                mock.patch.object(
                    packs,
                    "choices",
                    return_value={"gtk": [], "desktop": [], "icons": [], "wallpapers": []},
                ),
                mock.patch.object(theme_actions.desktop, "theme_part", return_value="gtk"),
                mock.patch.object(theme_actions.Gtk, "MessageDialog") as dialog,
                mock.patch.object(packs, "apply_pack") as apply,
            ):
                dialog.return_value.run.return_value = response
                self.assertTrue(
                    theme_actions.ThemeActions.choose_installed_components(
                        window, "1", entry, "gtk"
                    )
                )
                if expected == "later":
                    apply.assert_not_called()
                elif expected:
                    apply.assert_called_once_with(window, "1", only_kind=expected)
                else:
                    apply.assert_called_once_with(window, "1")

    def test_single_part_variants_do_not_prompt_as_multiple_components(self):
        with (
            mock.patch.object(packs, "choices", return_value={"gtk": [1, 2, 3]}),
            mock.patch.object(theme_actions.Gtk, "MessageDialog") as dialog,
        ):
            self.assertFalse(
                theme_actions.ThemeActions.choose_installed_components(
                    mock.Mock(), "1", {"components": []}, "gtk"
                )
            )
            dialog.assert_not_called()
