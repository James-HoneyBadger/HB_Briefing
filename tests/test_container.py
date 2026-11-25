"""The container entry point, exercised without Docker.

Every mode is a thin exec of an existing front end, so the dispatch can be tested as a shell
script: point it at a copy of the source, give it a state directory, and see what it runs. The
image itself is built in CI; this covers the part that is easy to get wrong.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "packaging" / "docker" / "entrypoint.sh"
MODULES = ("hbnscan.py", "hbworkspace.py", "hbconfig.py", "hbnconfig_gui.py", "hbnscan_gui.py",
           "hbntui.py", "hbnweb.py", "hbsimilar.py", "hbicons.py", "hbversion.py")


class EntrypointTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.app = self.root / "app"
        self.data = self.root / "data"
        self.app.mkdir()
        self.data.mkdir()
        for name in MODULES:
            shutil.copy2(ROOT / name, self.app / name)
        shutil.copytree(ROOT / "profiles", self.app / "profiles")
        (self.app / "feeds.txt.example").write_text("", encoding="utf-8")
        self.env = {"HB_APP_DIR": str(self.app), "HB_BRIEFING_HOME": str(self.data),
                    "PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONPATH": str(self.app),
                    "HB_PYTHON": sys.executable}

    def run_entrypoint(self, *args, timeout=120):
        return subprocess.run([str(ENTRYPOINT), *args], capture_output=True, text=True,
                              env=self.env, timeout=timeout, check=False)

    def test_help_lists_the_modes(self):
        result = self.run_entrypoint("help")
        self.assertEqual(result.returncode, 0)
        for mode in ("web", "daemon", "scan", "cli", "tui", "dossier", "shell"):
            self.assertIn(mode, result.stdout)

    def test_an_unknown_mode_is_a_usage_error(self):
        result = self.run_entrypoint("nonsense")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown mode", result.stderr)

    def test_a_scan_uses_the_shared_state_directory(self):
        (self.data / "feeds.txt").write_text("# no feeds\n", encoding="utf-8")
        result = self.run_entrypoint("scan", timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertTrue((self.data / "watchdog_state.db").is_file(),
                        "state must land in HB_BRIEFING_HOME, not in the image")

    def test_the_cli_mode_passes_arguments_through(self):
        (self.data / "feeds.txt").write_text("# no feeds\n", encoding="utf-8")
        target = self.data / "digest.md"
        result = self.run_entrypoint("cli", "--export", str(target), timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertTrue(target.is_file())
        self.assertIn("HB Briefing", target.read_text(encoding="utf-8"))

    def test_a_workspace_command_reaches_the_workspace(self):
        result = self.run_entrypoint("dossier", "list", timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertIn("dossier", result.stdout.lower())

    def test_the_dockerfile_keeps_state_out_of_the_image(self):
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("HB_BRIEFING_HOME=/data", text)
        self.assertIn("VOLUME", text)
        self.assertIn("USER briefing", text, "the container must not run as root")
        self.assertIn("HEALTHCHECK", text)

    def test_the_compose_file_does_not_publish_on_every_interface(self):
        text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("127.0.0.1:", text)
        self.assertIn("hb-data", text)


if __name__ == "__main__":
    unittest.main()
