"""The packaging files must not drift from the code they describe.

These checks fail on the mistakes that are invisible until a user installs the result: a module
missing from the payload, a version that disagrees with the tag, a template placeholder left
unfilled.
"""
import re
import sys
import unittest
from pathlib import Path

import hbversion

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))

import build  # noqa: E402  (the packaging driver)


class VersionTest(unittest.TestCase):
    def test_every_package_agrees_on_the_version(self):
        self.assertRegex(hbversion.VERSION, r"^\d+\.\d+\.\d+$")
        spec = (ROOT / "packaging" / "linux" / "hb-briefing.spec").read_text(encoding="utf-8")
        match = re.search(r"^Version:\s*(\S+)$", spec, re.MULTILINE)
        self.assertIsNotNone(match, "the spec has no Version line")
        self.assertEqual(match.group(1), hbversion.VERSION,
                         "packaging/linux/hb-briefing.spec disagrees with hbversion.VERSION")
        info = (ROOT / "packaging" / "pyinstaller" / "version_info.txt").read_text(
            encoding="utf-8")
        self.assertIn(f"({hbversion.VERSION.replace('.', ', ')}, 0)", info,
                      "version_info.txt disagrees with hbversion.VERSION")
        self.assertIn(f"u'{hbversion.VERSION}'", info)

    def test_the_engine_exposes_the_same_version(self):
        import hbnscan
        self.assertEqual(hbnscan.__version__, hbversion.VERSION)


class PayloadTest(unittest.TestCase):
    def test_every_runtime_module_is_declared_for_packaging(self):
        modules = {path.name for path in ROOT.glob("hb*.py")}
        self.assertTrue(modules, "no runtime modules found")
        missing = modules - set(build.RUNTIME_MODULES)
        self.assertFalse(missing, f"add {sorted(missing)} to RUNTIME_MODULES in build.py")

    def test_declared_modules_exist(self):
        for name in build.RUNTIME_MODULES:
            self.assertTrue((ROOT / name).is_file(), f"{name} is declared but missing")

    def test_the_source_tarball_carries_what_the_distro_recipes_read(self):
        self.assertIn("feeds.txt.example", build.RUNTIME_ASSETS)
        self.assertIn(".env.example", build.RUNTIME_ASSETS)
        for recipe in ("linux/hb-briefing.spec", "linux/PKGBUILD"):
            text = (ROOT / "packaging" / recipe).read_text(encoding="utf-8")
            self.assertIn("packaging/icons", text,
                          f"{recipe} reads the icon set out of the tarball")


class RecipeTest(unittest.TestCase):
    def test_templates_keep_their_placeholders(self):
        pkgbuild = (ROOT / "packaging" / "linux" / "PKGBUILD").read_text(encoding="utf-8")
        for placeholder in ("@VERSION@", "@SHA256@"):
            self.assertIn(placeholder, pkgbuild)
        formula = (ROOT / "packaging" / "macos" / "hb-briefing.rb").read_text(encoding="utf-8")
        for placeholder in ("@VERSION@", "@SHA256@"):
            self.assertIn(placeholder, formula)

    def test_desktop_entries_point_at_installed_commands(self):
        for name in ("hb-briefing.desktop", "hb-briefing-web.desktop"):
            text = (ROOT / "packaging" / "linux" / name).read_text(encoding="utf-8")
            self.assertIn("Icon=hb-briefing", text)
            executable = re.search(r"^Exec=(\S+)$", text, re.MULTILINE).group(1)
            self.assertIn(executable, ("hb-briefing", "hb-briefing-web"))

    def test_the_windows_installer_installs_per_user(self):
        iss = (ROOT / "packaging" / "windows" / "hb-briefing.iss").read_text(encoding="utf-8")
        self.assertIn("PrivilegesRequired=lowest", iss)
        self.assertIn("{localappdata}", iss)
        self.assertNotIn("{pf}", iss, "Program Files would need elevation")

    def test_the_pwa_note_explains_the_home_screen_route(self):
        self.assertIn("Add to Home Screen", build.PWA_NOTE)
        self.assertIn("hb-briefing-web", build.PWA_NOTE)

    def test_launchers_keep_state_out_of_the_install_directory(self):
        script = build.launcher("gui", "/usr/lib/hb-briefing")
        self.assertIn("HB_BRIEFING_HOME", script)
        self.assertIn("/usr/lib/hb-briefing/hbnscan_gui.py", script)
        self.assertIn("tkinter", script, "the launcher must explain a missing tkinter")
        self.assertTrue(script.startswith("#!/bin/sh"))


if __name__ == "__main__":
    unittest.main()
