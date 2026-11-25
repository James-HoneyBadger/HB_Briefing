# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: one build tree, four executables, every desktop platform.

The GUI starts without a console window; the three console front ends share the same
interpreter and libraries through MERGE, so the folder stays close to the size of a single app.

Build with `python3 packaging/build.py frozen` (Linux/macOS) or
`packaging\\windows\\build.ps1` (Windows), which pass --distpath/--workpath.
"""
import sys
from pathlib import Path

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parents[1]
ICON_DIR = ROOT / "packaging" / "icons"

SRC = ROOT / "src"
ASSETS = ROOT / "assets"
sys.path.insert(0, str(SRC))
from hbversion import APP_NAME, VERSION  # noqa: E402

DATAS = [
    (str(ASSETS / "feeds.txt.example"), "."),
    (str(ASSETS / "watchlist.txt.example"), "."),
    (str(ASSETS / "topics.txt.example"), "."),
    (str(ASSETS / ".env.example"), "."),
    (str(ASSETS / "profiles"), "profiles"),
    (str(ROOT / "docs"), "docs"),
    (str(ROOT / "LICENSE"), "."),
]
# Imported lazily by the CLI dispatch, so they are named explicitly.
HIDDEN = ["hbworkspace", "hbnweb", "hbntui", "hbconfig", "hbnconfig_gui", "hbsimilar",
          "hbicons", "hbversion"]

common = {
    "pathex": [str(SRC)],
    "datas": DATAS,
    "hiddenimports": HIDDEN,
    "excludes": ["pytest", "IPython", "matplotlib", "numpy", "PIL", "pandas"],
    "noarchive": False,
    "optimize": 0,
}

gui = Analysis([str(SPEC_DIR / "entry_gui.py")], **common)
cli = Analysis([str(SPEC_DIR / "entry_cli.py")], **common)
web = Analysis([str(SPEC_DIR / "entry_web.py")], **common)
tui = Analysis([str(SPEC_DIR / "entry_tui.py")], **common)

# Fold the console builds into the GUI's bundle so shared libraries are stored once.
MERGE((cli, "hb-briefing-cli", "hb-briefing-cli"),
      (web, "hb-briefing-web", "hb-briefing-web"),
      (tui, "hb-briefing-tui", "hb-briefing-tui"))

icon = None
if sys.platform == "win32":
    icon = str(ICON_DIR / "hb-briefing.ico")
elif sys.platform == "darwin":
    icon = str(ICON_DIR / "hb-briefing.icns")
if icon and not Path(icon).is_file():
    icon = None


def build_exe(analysis, name, console):
    return EXE(
        PYZ(analysis.pure),
        analysis.scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        strip=False,
        upx=False,
        console=console,
        icon=icon,
        version=str(SPEC_DIR / "version_info.txt") if sys.platform == "win32" else None,
    )


exe_gui = build_exe(gui, "hb-briefing", console=False)
exe_cli = build_exe(cli, "hb-briefing-cli", console=True)
exe_web = build_exe(web, "hb-briefing-web", console=True)
exe_tui = build_exe(tui, "hb-briefing-tui", console=True)

coll = COLLECT(
    exe_gui,
    exe_cli,
    exe_web,
    exe_tui,
    gui.binaries,
    gui.datas,
    gui.zipfiles,
    strip=False,
    upx=False,
    name=APP_NAME.replace(" ", "-").lower(),
)

if sys.platform == "darwin":
    # A real .app so Finder, Launchpad and `open -a` all behave; the console binaries stay
    # reachable inside Contents/MacOS for scripting and cron.
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=icon,
        bundle_identifier="io.github.jameshoneybadger.hb-briefing",
        version=VERSION,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "10.15",
            "NSRequiresAquaSystemAppearance": False,
            "LSApplicationCategoryType": "public.app-category.news",
            "NSHumanReadableCopyright": "MIT licensed",
        },
    )
