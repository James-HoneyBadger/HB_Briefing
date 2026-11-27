"""Design tokens: one palette table, a light house style by default, explicit choices kept."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hbnscan
import hbtheme


class PaletteTest(unittest.TestCase):
    def test_the_classic_theme_is_the_design_directive_palette(self):
        classic = hbtheme.palette("classic")
        self.assertEqual(classic["bg_main"], "#F5F3EB")     # workspace
        self.assertEqual(classic["bg_panel"], "#E1E3DE")    # panels
        self.assertEqual(classic["bg_accent"], "#DDDCD4")   # toolbar
        self.assertEqual(classic["bg_input"], "#EAE8E1")    # background
        self.assertEqual(classic["bg_select"], "#C4D1D5")   # selection
        self.assertEqual(classic["fg_text"], "#354650")     # primary text
        self.assertEqual(classic["fg_muted"], "#59656A")    # secondary text
        self.assertEqual(classic["accent"], "#A88B55")      # brand accent
        self.assertEqual(classic["green"], "#66856C")       # success
        self.assertEqual(classic["red"], "#A45D58")         # error
        self.assertEqual(classic["header"], "#536B79")
        self.assertEqual(classic["header_text"], "#F5F4EE")

    def test_every_theme_defines_every_token_the_front_ends_read(self):
        required = {"bg_main", "bg_panel", "bg_accent", "bg_input", "bg_select", "log_fg",
                    "fg_text", "fg_muted", "accent", "accent_hover", "green", "red",
                    "header", "header_text", "border", "line", "line_soft", "hover"}
        for name, theme in hbtheme.THEMES.items():
            self.assertTrue(required <= set(theme), f"{name} is missing tokens")
            for key in required:
                self.assertRegex(theme[key], r"^#[0-9A-Fa-f]{6}$", f"{name}.{key}")

    def test_an_unknown_theme_falls_back_to_the_house_style(self):
        self.assertEqual(hbtheme.palette("no-such-theme"), hbtheme.palette(hbtheme.DEFAULT_THEME))

    def test_the_web_variables_cover_what_the_stylesheet_uses(self):
        css = hbtheme.web_vars("classic")
        for var in ("--bg:", "--panel:", "--line:", "--bg-input:", "--ink:", "--muted:",
                    "--amber:", "--green:", "--red:"):
            self.assertIn(var, css)


class DefaultThemeTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        patch = mock.patch.object(hbnscan, "SETTINGS_PATH", self.root / "settings.json")
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_new_installation_starts_light(self):
        self.assertEqual(hbnscan.load_settings()["theme"], "classic")

    def test_an_explicitly_chosen_theme_is_never_rewritten(self):
        (self.root / "settings.json").write_text(json.dumps({"theme": "dark"}),
                                                 encoding="utf-8")
        self.assertEqual(hbnscan.load_settings()["theme"], "dark")

    def test_a_theme_name_from_the_old_release_is_still_a_valid_choice(self):
        for name in ("dark", "light", "midnight", "solarized", "contrast"):
            self.assertIn(name, hbnscan.setting_choices("theme"))

    def test_a_stale_theme_value_degrades_instead_of_failing_to_load(self):
        (self.root / "settings.json").write_text(json.dumps({"theme": "retired"}),
                                                 encoding="utf-8")
        self.assertEqual(hbnscan.load_settings()["theme"], "classic")


class WebPageThemeTest(unittest.TestCase):
    def test_the_served_page_carries_every_theme_and_starts_classic(self):
        import hbnweb
        page = hbnweb.PAGE.replace("__THEME_CSS__", hbnweb.THEME_CSS)
        self.assertNotIn("__THEME_CSS__", page)
        self.assertIn(":root {", page)
        self.assertIn("--bg:#F5F3EB", page, "the first paint must already be the house style")
        for name in hbtheme.THEME_NAMES:
            self.assertIn(f'html[data-theme="{name}"]', page)
        self.assertIn('<option value="classic">', page)


if __name__ == "__main__":
    unittest.main()
