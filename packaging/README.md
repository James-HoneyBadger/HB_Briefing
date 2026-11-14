# Packaging

Everything needed to turn the checkout into something a user can install. One script drives it:

```bash
python3 packaging/build.py --list
python3 packaging/build.py all        # whatever this machine can build
```

Artefacts land in `dist/`. Builds stage a private copy, so you can package while the app is
running and your own `feeds.txt` and database are never touched.

## Targets

| Target | Produces | Needs |
| --- | --- | --- |
| `icons` | `packaging/icons/*` from `hbicons.py` | Python only |
| `source` | `dist/hb-briefing-<v>.tar.gz`, `dist/hb-briefing.rb` | Python only |
| `deb` | `dist/hb-briefing_<v>_all.deb` | `dpkg-deb` |
| `rpm` | `dist/hb-briefing-<v>-1.*.noarch.rpm` | `rpmbuild` |
| `arch` | `dist/PKGBUILD` with a real checksum | Python only |
| `frozen` | `dist/hb-briefing-<v>-<os>-<arch>/` (self-contained) | PyInstaller |
| `appimage` | `dist/hb-briefing-<v>-linux-x86_64.AppImage` | PyInstaller, FUSE, `appimagetool` |
| `windows` | portable zip, and the installer when Inno Setup is present | Windows, PyInstaller, ISCC |
| `macos` | `.app` + `.dmg` | macOS, PyInstaller |
| `pwa` | `dist/hb-briefing-<v>-*-web.zip` (host for iPadOS/ChromeOS) | PyInstaller |
| `wasm` | `dist/hb-briefing-<v>-wasm/` and `.zip` (browser app, no install) | Python only |

## Layout

```
packaging/
  build.py                  the driver: staging, .deb assembly, AppImage, dmg, zips
  icons/                    generated; committed so a package build needs no image library
  linux/
    hb-briefing.desktop     menu entry (GUI)
    hb-briefing-web.desktop menu entry (browser app)
    hb-briefing.spec        RPM spec (Version must match hbversion.VERSION)
    PKGBUILD                Arch recipe; @VERSION@ and @SHA256@ are filled in by build.py
  windows/
    hb-briefing.iss         Inno Setup: per-user install, no elevation
    build.ps1               PowerShell wrapper for Windows without a bash shell
  macos/
    hb-briefing.rb          Homebrew formula; @VERSION@/@SHA256@ filled in by build.py
  pyinstaller/
    hb-briefing.spec        one tree, four executables (GUI, CLI, web, TUI)
    entry_*.py              the four frozen entry points
    version_info.txt        Windows EXE metadata
wasm/
  index.html                the browser app; loads hbwasm.py + the engine via Pyodide
  README.md                 how to serve it, and what it deliberately cannot do
```

## Rules that keep packages correct

1. **One version.** `hbversion.py` is the single source; `build.py` reads it and the RPM spec
   must agree. Bump it, then retag.
2. **Code, assets and state are three different directories.** The frozen builds and the distro
   packages set `HB_BRIEFING_HOME` (or rely on the default per-user directory); bundled examples
   are read from `ASSET_DIR`, which is the PyInstaller `_MEIPASS` when frozen.
3. **New runtime module?** Add it to `RUNTIME_MODULES` in `build.py`, otherwise the distro
   packages and the source tarball will not ship it. A module that only the browser app needs
   goes in `BROWSER_ONLY` (and `WASM_MODULES`) instead; a test fails until one of them names it.
4. **Icons are generated, never hand-edited.** Change the geometry in `hbicons.py` and run
   `python3 packaging/build.py icons`. The PWA, the desktop entry, the Windows EXE and the
   macOS bundle all read that one set.
5. **The Windows installer and the macOS bundle are built on their own OS.** Cross-building
   either is not supported; the release workflow runs one job per platform.

## CI

`.github/workflows/release.yml` builds the deb, the AppImage, the web bundle, the RPM (inside a
Fedora container), the Windows installer, the macOS dmg and the distro recipes on a `v*` tag,
then publishes them with `SHA256SUMS.txt`. The `tests.yml` workflow stays the gate for the
source itself.

## Trying a package without installing it

```bash
sudo dpkg -i dist/hb-briefing_1.0.0_all.deb        # or: sudo apt install ./dist/...
dpkg-deb -x dist/hb-briefing_1.0.0_all.deb /tmp/root   # inspect only
HB_BRIEFING_HOME=/tmp/home /tmp/root/usr/lib/hb-briefing/hbnscan.py --reclassify --dry-run
```

The last command is the fastest end-to-end check: it proves the payload runs, that first-run
seeding works, and that the database is created in the state directory rather than next to the
code.
