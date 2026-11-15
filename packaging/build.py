#!/usr/bin/env python3
"""Build installable HB Briefing packages for every desktop platform.

    python3 packaging/build.py deb          # Debian/Ubuntu/Mint/Pop!_OS and derivatives
    python3 packaging/build.py rpm          # Fedora/RHEL/openSUSE (needs rpmbuild)
    python3 packaging/build.py arch         # Arch/Manjaro (writes a ready PKGBUILD)
    python3 packaging/build.py appimage     # universal Linux bundle (needs PyInstaller)
    python3 packaging/build.py windows      # installer on Windows (needs PyInstaller + Inno Setup)
    python3 packaging/build.py macos        # .app + .dmg on macOS (needs PyInstaller)
    python3 packaging/build.py pwa          # web-app host bundle for iPadOS/ChromeOS
    python3 packaging/build.py wasm         # single-page WebAssembly app (Pyodide)
    python3 packaging/build.py source       # tarball for distro packagers and Homebrew
    python3 packaging/build.py frozen       # the self-contained tree, no installer
    python3 packaging/build.py icons        # regenerate the icon set from hbicons.py
    python3 packaging/build.py all          # everything this machine can build

Every artefact lands in dist/. Builds never touch the repository's own configuration or
database: they stage a copy, so a package can be built while the app is running.
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import hbicons  # noqa: E402
from hbversion import APP_NAME, VERSION  # noqa: E402

SLUG = "hb-briefing"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
PACKAGING = ROOT / "packaging"
ICONS = PACKAGING / "icons"

# Everything the application needs at run time; the tests, packaging and git metadata are not
# installed. Adding a module here is the one place a new file has to be declared.
RUNTIME_MODULES = ("hbnscan.py", "hbworkspace.py", "hbconfig.py", "hbnconfig_gui.py",
                   "hbnscan_gui.py", "hbntui.py", "hbnweb.py", "hbsimilar.py", "hbicons.py",
                   "hbversion.py")
RUNTIME_ASSETS = ("feeds.txt.example", "watchlist.txt.example", "topics.txt.example",
                  ".env.example", "LICENSE", "README.md", "requirements.txt")
# Only what hbwasm.py needs: the engine, the similarity pass and the version. The browser app
# runs the same scoring code, so it ships the same files.
WASM_MODULES = ("hbwasm.py", "hbnscan.py", "hbsimilar.py", "hbversion.py")
# Deliberately absent from the desktop payloads: the browser adapter is only useful in the tab.
BROWSER_ONLY = ("hbwasm.py",)
DOC_FILES = {"README.md": "README.md", "USER-MANUAL.md": "USER-MANUAL.md",
             "TECHNICAL-REFERENCE.md": "TECHNICAL-REFERENCE.md", "WEB-APP.md": "WEB-APP.md",
             "INSTALL.md": "INSTALL.md"}

MAINTAINER = "James HoneyBadger <james-honeybadger@users.noreply.github.com>"
HOMEPAGE = "https://github.com/James-HoneyBadger/HB_Briefing"
SUMMARY = "Local-first news and regulatory signal monitor with dossiers and actions"
DESCRIPTION = """\
HB Briefing monitors RSS and Atom feeds, grades every new item against your own profile,
verifies the flagged ones against the linked article, and keeps dossiers, actions and source
health in a local SQLite file. It works offline with a deterministic heuristic and can call an
OpenAI-compatible model when a key is configured. The desktop app, the terminal interface and
the web app all read the same database.
"""


# --------------------------------------------------------------------- small helpers


def log(message):
    print(f"[build] {message}", flush=True)


def run(command, cwd=None, env=None, check=True, capture=False):
    printable = " ".join(str(part) for part in command)
    log(printable)
    result = subprocess.run([str(part) for part in command], cwd=str(cwd or ROOT),
                            env=env, text=True, check=False,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.STDOUT if capture else None)
    if check and result.returncode != 0:
        if capture and result.stdout:
            print(result.stdout)
        raise SystemExit(f"[build] command failed ({result.returncode}): {printable}")
    return result


def require(tool, hint):
    found = shutil.which(tool)
    if not found:
        raise SystemExit(f"[build] {tool} is required: {hint}")
    return found


def dist_path(name):
    DIST.mkdir(parents=True, exist_ok=True)
    path = DIST / name
    if path.exists():
        path.unlink()
    return path


def clean(directory):
    if Path(directory).exists():
        shutil.rmtree(directory)
    Path(directory).mkdir(parents=True, exist_ok=True)


def stage_runtime(destination, with_docs=True):
    """Copy the runtime tree into `destination` (a private copy, never a symlink farm)."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name in RUNTIME_MODULES:
        shutil.copy2(ROOT / name, destination / name)
    for name in RUNTIME_ASSETS:
        source = ROOT / name
        if source.is_file():
            shutil.copy2(source, destination / name)
    if (ROOT / "profiles").is_dir():
        shutil.copytree(ROOT / "profiles", destination / "profiles", dirs_exist_ok=True)
    if with_docs:
        docs = destination / "docs"
        docs.mkdir(exist_ok=True)
        for source_name, target_name in DOC_FILES.items():
            source = ROOT / "docs" / source_name
            if source.is_file():
                shutil.copy2(source, docs / target_name)
    return destination


def normalise_modes(root, executables=()):
    """Package payloads must not inherit the checkout's umask or stray +x bits."""
    executables = {Path(path) for path in executables}
    for path in Path(root).rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
            continue
        path.chmod(0o755 if path in executables else 0o644)


def launcher(kind, install_dir):
    """A POSIX launcher that keeps user state out of the install directory."""
    commands = {
        "gui": ("hbnscan_gui.py", "python3"),
        "cli": ("hbnscan.py", "python3"),
        "web": ("hbnweb.py", "python3"),
        "tui": ("hbntui.py", "python3"),
    }
    script, interpreter = commands[kind]
    guard = ""
    if kind in ("gui", "tui"):
        guard = (f'command -v {interpreter} >/dev/null 2>&1 || '
                 f'{{ echo "HB Briefing needs {interpreter} on PATH." >&2; exit 1; }}\n')
    if kind == "gui":
        guard += (f'{interpreter} -c "import tkinter" 2>/dev/null || {{ '
                  'echo "HB Briefing needs tkinter: install python3-tk (Debian/Ubuntu), '
                  'python3-tkinter (Fedora) or tk (Arch)." >&2; exit 1; }\n')
    return (f'#!/bin/sh\n'
            f'# HB Briefing launcher. User state lives in HB_BRIEFING_HOME, never in '
            f'{install_dir}.\n'
            f'set -e\n'
            f'{guard}'
            f': "${{HB_BRIEFING_HOME:=${{XDG_DATA_HOME:-$HOME/.local/share}}/hb-briefing}}"\n'
            f'export HB_BRIEFING_HOME\n'
            f'exec {interpreter} {install_dir}/{script} "$@"\n')


# --------------------------------------------------------------------- icons


def build_icons(force=True):
    """Render the icon set that the desktop entries, installers and PWA all share."""
    ICONS.mkdir(parents=True, exist_ok=True)
    written = hbicons.write_icons(ICONS, prefix=SLUG, sizes=hbicons.PNG_SIZES)
    (ICONS / f"{SLUG}.ico").write_bytes(hbicons.ico_bytes())
    (ICONS / f"{SLUG}.icns").write_bytes(hbicons.icns_bytes())
    log(f"{len(written) + 2} icon files in {ICONS.relative_to(ROOT)}")
    return ICONS


def ensure_icons():
    if not (ICONS / f"{SLUG}.ico").is_file():
        build_icons()


# --------------------------------------------------------------------- tarballs


def _tar_filter(info):
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = 1700000000          # reproducible: no build timestamp in the archive
    if info.isdir():
        info.mode = 0o755
    else:
        info.mode = 0o755 if info.mode & 0o111 else 0o644
    return info


def tar_gz_bytes(source_dir, arc_root):
    """A .tar.gz of `source_dir`, stored under `arc_root/` with reproducible metadata."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.GNU_FORMAT) as archive:
        archive.add(str(source_dir), arcname=arc_root, filter=_tar_filter,
                    recursive=True)
    return buffer.getvalue()


def build_source():
    """The tarball distro packagers and the Homebrew formula build from."""
    name = f"{SLUG}-{VERSION}.tar.gz"
    target = dist_path(name)
    staging = BUILD / "source" / f"{SLUG}-{VERSION}"
    clean(BUILD / "source")
    stage_runtime(staging)
    shutil.copy2(ROOT / "packaging" / "linux" / f"{SLUG}.desktop",
                 staging / f"{SLUG}.desktop")
    if (ROOT / "start.sh").is_file():
        shutil.copy2(ROOT / "start.sh", staging / "start.sh")
    # The distro package recipes read the icon set out of the tarball.
    ensure_icons()
    shutil.copytree(ICONS, staging / "packaging" / "icons", dirs_exist_ok=True)
    target.write_bytes(tar_gz_bytes(staging, f"{SLUG}-{VERSION}"))
    log(f"{target.relative_to(ROOT)} ({target.stat().st_size // 1024} KiB)")
    write_homebrew_formula(target)
    return target


def write_homebrew_formula(tarball):
    """Fill the formula in with the version and the checksum of the tarball just built."""
    import hashlib

    digest = hashlib.sha256(Path(tarball).read_bytes()).hexdigest()
    template = (PACKAGING / "macos" / f"{SLUG}.rb").read_text(encoding="utf-8")
    target = DIST / f"{SLUG}.rb"
    target.write_text(template.replace("@VERSION@", VERSION).replace("@SHA256@", digest),
                      encoding="utf-8")
    log(f"{target.relative_to(ROOT)} (sha256 {digest[:16]}…)")
    return target


# --------------------------------------------------------------------- .deb


def _tar_of_dir(directory, exclude=()):
    """A gzipped tar of `directory`, with `exclude`d top-level names left out."""
    excluded = set(exclude)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.GNU_FORMAT) as archive:
        for path in sorted(Path(directory).rglob("*")):
            relative = path.relative_to(directory)
            if relative.parts and relative.parts[0] in excluded:
                continue
            archive.add(str(path), arcname=str(relative), filter=_tar_filter, recursive=False)
        archive.add(str(directory), arcname=".", filter=_tar_filter, recursive=False)
    return buffer.getvalue()


def _ar_archive(members):
    """A .deb is an `ar` archive of three members; writing it needs no external tool."""
    out = bytearray(b"!<arch>\n")
    for name, payload in members:
        header = (f"{name + '/':<16}{int(time.time()):<12}{0:<6}{0:<6}{'100644':<8}"
                  f"{len(payload):<10}`\n").encode("ascii")
        out += header + payload
        if len(payload) % 2:
            out += b"\n"          # ar members are 2-byte aligned
    return bytes(out)


def build_deb(pure=False):
    """Assemble the .deb, through dpkg-deb when it is available and directly when it is not."""
    dpkg_deb = None if pure else shutil.which("dpkg-deb")
    if not dpkg_deb:
        log("dpkg-deb unavailable or not wanted: writing the archive directly")
    ensure_icons()
    work = BUILD / "deb"
    clean(work)
    data = work / "data"
    install_dir = Path("/usr/lib") / SLUG
    stage_runtime(data / install_dir.relative_to("/"))

    for kind, binary in (("gui", SLUG), ("cli", f"{SLUG}-cli"), ("web", f"{SLUG}-web"),
                         ("tui", f"{SLUG}-tui")):
        path = data / "usr" / "bin" / binary
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(launcher(kind, install_dir), encoding="utf-8")
        path.chmod(0o755)

    applications = data / "usr" / "share" / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    for desktop in (f"{SLUG}.desktop", f"{SLUG}-web.desktop"):
        shutil.copy2(PACKAGING / "linux" / desktop, applications / desktop)
    for size in (16, 32, 48, 64, 128, 256, 512):
        target = (data / "usr" / "share" / "icons" / "hicolor" / f"{size}x{size}" / "apps"
                  / f"{SLUG}.png")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ICONS / f"{SLUG}-{size}.png", target)

    doc = data / "usr" / "share" / "doc" / SLUG
    doc.mkdir(parents=True, exist_ok=True)
    for name in ("README.md", "USER-MANUAL.md", "TECHNICAL-REFERENCE.md", "INSTALL.md"):
        source = ROOT / "docs" / name
        if source.is_file():
            shutil.copy2(source, doc / name)
    (doc / "copyright").write_text(
        f"HB Briefing {VERSION}\n{SUMMARY}\n\n{HOMEPAGE}\n\nMIT licensed.\n",
        encoding="utf-8")

    size_kib = sum(path.stat().st_size for path in (data / "usr").rglob("*")
                   if path.is_file()) // 1024
    normalise_modes(data, executables={data / "usr" / "bin" / binary for binary in
                                      (SLUG, f"{SLUG}-cli", f"{SLUG}-web", f"{SLUG}-tui")})
    control_dir = data / "DEBIAN"
    control_dir.mkdir(parents=True, exist_ok=True)
    (control_dir / "control").write_text(
        f"Package: {SLUG}\n"
        f"Version: {VERSION}\n"
        "Section: news\n"
        "Priority: optional\n"
        "Architecture: all\n"
        "Depends: python3 (>= 3.8), python3-tk, python3-dotenv\n"
        "Recommends: poppler-utils, python3-venv, cron\n"
        f"Installed-Size: {size_kib}\n"
        f"Maintainer: {MAINTAINER}\n"
        f"Homepage: {HOMEPAGE}\n"
        f"Description: {SUMMARY}\n"
        + "".join(f" {line}\n" if line else " .\n" for line in DESCRIPTION.splitlines()),
        encoding="utf-8")
    (control_dir / "postinst").write_text(
        "#!/bin/sh\nset -e\n"
        "if command -v update-desktop-database >/dev/null 2>&1; then\n"
        "    update-desktop-database -q /usr/share/applications || true\n"
        "fi\n"
        "exit 0\n", encoding="utf-8")
    (control_dir / "postinst").chmod(0o755)
    target = dist_path(f"{SLUG}_{VERSION}_all.deb")
    if dpkg_deb:
        run([dpkg_deb, "--root-owner-group", "--build", data, target])
    else:
        # The same three members dpkg-deb writes. DEBIAN belongs to control.tar.gz only, so
        # it is excluded from data.tar.gz.
        target.write_bytes(_ar_archive([
            ("debian-binary", b"2.0\n"),
            ("control.tar.gz", _tar_of_dir(control_dir)),
            ("data.tar.gz", _tar_of_dir(data, exclude=("DEBIAN",))),
        ]))
    log(f"{target.relative_to(ROOT)} ({target.stat().st_size // 1024} KiB)")
    return target


# --------------------------------------------------------------------- .rpm


def build_rpm():
    rpmbuild = shutil.which("rpmbuild")
    if not rpmbuild:
        raise SystemExit("[build] rpmbuild is required: on Fedora 'sudo dnf install rpm-build', "
                         "on Debian 'sudo apt install rpm'")
    tarball = build_source()
    ensure_icons()
    top = BUILD / "rpm"
    for sub in ("SOURCES", "SPECS", "BUILD", "RPMS", "SRPMS"):
        (top / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy2(tarball, top / "SOURCES" / tarball.name)
    run([rpmbuild, "-ba", PACKAGING / "linux" / f"{SLUG}.spec",
         "--define", f"_topdir {top}",
         "--define", f"_sourcedir {top / 'SOURCES'}",
         "--define", f"version {VERSION}"])
    built = sorted((top / "RPMS").rglob("*.rpm"))
    if not built:
        raise SystemExit("[build] rpmbuild produced no package")
    target = dist_path(built[0].name)
    shutil.copy2(built[0], target)
    log(f"{target.relative_to(ROOT)} ({target.stat().st_size // 1024} KiB)")
    return target


def build_arch():
    """Write a PKGBUILD carrying the real checksum of the source tarball just built."""
    import hashlib

    tarball = build_source()
    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    template = (PACKAGING / "linux" / "PKGBUILD").read_text(encoding="utf-8")
    rendered = (template
                .replace("@VERSION@", VERSION)
                .replace("@SHA256@", digest))
    target = DIST / "PKGBUILD"
    target.write_text(rendered, encoding="utf-8")
    log(f"{target.relative_to(ROOT)} (sha256 {digest[:16]}…)")
    log("on Arch: cd dist && makepkg -si")
    return target


# --------------------------------------------------------------------- frozen tree


def pyinstaller_command():
    """PyInstaller may be installed as a module rather than a script."""
    candidate = shutil.which("pyinstaller")
    if candidate:
        return [candidate]
    probe = run([sys.executable, "-m", "PyInstaller", "--version"], check=False, capture=True)
    if probe.returncode == 0:
        return [sys.executable, "-m", "PyInstaller"]
    raise SystemExit("[build] PyInstaller is required: "
                     "python3 -m pip install --user pyinstaller")


def build_frozen():
    """A self-contained tree with its own interpreter: no system Python needed."""
    ensure_icons()
    work = BUILD / "pyinstaller"
    clean(work)
    name = f"{SLUG}-{VERSION}-{platform_tag()}"
    out = work / "dist"
    # Keep any PYTHONPATH the caller set (that is where PyInstaller itself may live).
    pythonpath = os.pathsep.join(
        part for part in (str(ROOT), os.environ.get("PYTHONPATH", "")) if part)
    run(pyinstaller_command() + [
        PACKAGING / "pyinstaller" / f"{SLUG}.spec",
        "--noconfirm", "--clean", "--log-level", "WARN",
        "--distpath", out, "--workpath", work / "work",
    ], env={**os.environ, "PYTHONPATH": pythonpath})
    produced = sorted(out.iterdir())
    if not produced:
        raise SystemExit("[build] PyInstaller produced nothing")
    target = DIST / name
    if target.exists():
        shutil.rmtree(target)
    DIST.mkdir(parents=True, exist_ok=True)
    # macOS produces two items (the .app and the console tree); keep them together.
    if len(produced) == 1:
        shutil.move(str(produced[0]), target)
    else:
        target.mkdir()
        for item in produced:
            shutil.move(str(item), target / item.name)
    log(f"{target.relative_to(ROOT)}")
    return target


def platform_tag():
    machine = os.uname().machine if hasattr(os, "uname") else "amd64"
    machine = {"x86_64": "x86_64", "AMD64": "x86_64", "arm64": "arm64",
               "aarch64": "arm64"}.get(machine, machine)
    if os.name == "nt":
        return f"windows-{machine}"
    if sys.platform == "darwin":
        return f"macos-{machine}"
    return f"linux-{machine}"


def zip_tree(directory, target):
    """Zip a tree with POSIX permissions preserved (Python's zipfile is enough)."""
    target = Path(target)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(Path(directory).rglob("*")):
            relative = path.relative_to(Path(directory).parent)
            if path.is_dir():
                continue
            info = zipfile.ZipInfo(str(relative))
            info.date_time = (2025, 1, 1, 0, 0, 0)
            info.external_attr = (path.stat().st_mode & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    log(f"{target.relative_to(ROOT)} ({target.stat().st_size // 1024} KiB)")
    return target


# --------------------------------------------------------------------- AppImage


APPIMAGE_URL = ("https://github.com/AppImage/AppImageKit/releases/download/continuous/"
                "appimagetool-x86_64.AppImage")


def _tool(name, url, work):
    found = shutil.which(name)
    if found:
        return found
    cached = BUILD / "tools" / name
    if not cached.is_file():
        cached.parent.mkdir(parents=True, exist_ok=True)
        log(f"downloading {name}")
        import urllib.request
        try:
            urllib.request.urlretrieve(url, cached)
        except Exception as exc:                      # noqa: BLE001 - explain, do not trace
            raise SystemExit(f"[build] could not download {name} ({exc}); install it manually "
                             f"and put it on PATH") from None
        cached.chmod(0o755)
    return str(cached)


def build_appimage():
    if platform_tag() != "linux-x86_64":
        raise SystemExit("[build] AppImage tooling is only distributed for x86_64 hosts; "
                         "use the .deb/.rpm here, or build the AppImage on an x86_64 machine")
    tree = build_frozen()
    appdir = BUILD / "appimage" / f"{APP_NAME}.AppDir"
    clean(BUILD / "appimage")
    (appdir / "usr" / "bin").mkdir(parents=True)
    shutil.copytree(tree, appdir / "usr" / "bin", dirs_exist_ok=True)
    shutil.copy2(ICONS / f"{SLUG}-512.png", appdir / f"{SLUG}.png")
    desktop = (PACKAGING / "linux" / f"{SLUG}.desktop").read_text(encoding="utf-8")
    (appdir / f"{SLUG}.desktop").write_text(desktop, encoding="utf-8")
    (appdir / "AppRun").write_text(
        "#!/bin/sh\n"
        'HERE=$(dirname "$(readlink -f "$0")")\n'
        'export HB_BRIEFING_HOME="${HB_BRIEFING_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}'
        '/hb-briefing}"\n'
        'exec "$HERE/usr/bin/hb-briefing" "$@"\n', encoding="utf-8")
    (appdir / "AppRun").chmod(0o755)
    tool = _tool("appimagetool", APPIMAGE_URL, BUILD)
    target = dist_path(f"{SLUG}-{VERSION}-{platform_tag()}.AppImage")
    env = {**os.environ, "ARCH": "x86_64", "APPIMAGE_EXTRACT_AND_RUN": "1"}
    run([tool, "--no-appstream", appdir, target], env=env)
    target.chmod(0o755)
    log(f"{target.relative_to(ROOT)} ({target.stat().st_size // 1024} KiB)")
    return target


# --------------------------------------------------------------------- Windows


def build_windows():
    if os.name != "nt":
        raise SystemExit("[build] the Windows installer has to be built on Windows "
                         "(or in the release workflow's windows-latest job)")
    tree = build_frozen()
    iss = PACKAGING / "windows" / f"{SLUG}.iss"
    iscc_candidates = [shutil.which("ISCC"), shutil.which("iscc"),
                       r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                       r"C:\Program Files\Inno Setup 6\ISCC.exe"]
    iscc = next((path for path in iscc_candidates if path and Path(path).is_file()), None)
    archive = zip_tree(tree, DIST / f"{SLUG}-{VERSION}-windows-portable.zip")
    if not iscc:
        log("Inno Setup (ISCC.exe) not found: the portable zip is the installable artefact")
        return archive
    run([iscc, f"/DMyAppVersion={VERSION}", f"/DSourceDir={tree}", iss])
    produced = sorted(DIST.glob("*.exe"))
    log(f"{produced[-1].name} ({produced[-1].stat().st_size // 1024} KiB)")
    return produced[-1]


# --------------------------------------------------------------------- macOS


def build_macos():
    if sys.platform != "darwin":
        raise SystemExit("[build] the .dmg has to be built on macOS "
                         "(or in the release workflow's macos-latest job)")
    frozen = build_frozen()
    # PyInstaller may leave the bundle beside the console tree or move it into dist/.
    candidates = [path for path in Path(frozen).rglob("*.app")]
    candidates += [path for path in (BUILD / "pyinstaller" / "dist").rglob("*.app")]
    bundle = next((path for path in candidates if path.is_dir()), None)
    if bundle is None:
        raise SystemExit("[build] the .app bundle was not produced by PyInstaller")
    if shutil.which("codesign"):
        # Ad-hoc signing: unsigned arm64 binaries are killed by Gatekeeper on first launch.
        run(["codesign", "--force", "--deep", "--sign", "-", bundle], check=False)
    target = dist_path(f"{SLUG}-{VERSION}-{platform_tag()}.dmg")
    staging = BUILD / "dmg"
    clean(staging)
    shutil.copytree(bundle, staging / bundle.name, symlinks=True)
    (staging / "Applications").symlink_to("/Applications")
    run(["hdiutil", "create", "-quiet", "-volname", APP_NAME, "-srcfolder", staging,
         "-ov", "-format", "UDZO", target])
    log(f"{target.relative_to(ROOT)} ({target.stat().st_size // 1024} KiB)")
    return target


# --------------------------------------------------------------------- PWA host


def build_pwa():
    """The web-app bundle that a phone or Chromebook installs to its home screen."""
    tree = build_frozen()
    if not list(Path(tree).rglob("hb-briefing-web*")):
        raise SystemExit("[build] the frozen tree has no web front end")
    (Path(tree) / "INSTALL-PWA.md").write_text(PWA_NOTE, encoding="utf-8")
    target = DIST / f"{SLUG}-{VERSION}-{platform_tag()}-web.zip"
    return zip_tree(tree, target)


PWA_NOTE = """\
# HB Briefing web app: iPadOS, ChromeOS, Android

The web app is the same program as the desktop build; it serves its own manifest, service
worker and icons, so it installs to a home screen or shelf and keeps reading offline.

    ./hb-briefing-web --host 0.0.0.0 --port 8765

Then, on the device: open `http://<computer-ip>:8765/` and use *Add to Home Screen*
(iPadOS Safari) or *Install app* (ChromeOS, Android, desktop Chrome and Edge).

The token printed at start is only needed for actions that change data (scan, dismiss,
settings); reading works without it. Offline caching needs a secure context, which plain HTTP
over a LAN is not: use `localhost`, an SSH tunnel or a TLS reverse proxy when you want the
installed app to open with no network at all.
"""


# --------------------------------------------------------------------- WebAssembly


def build_wasm():
    """A single-page app that runs the engine in the browser, via Pyodide."""
    out = DIST / f"{SLUG}-{VERSION}-wasm"
    clean(out)
    for name in WASM_MODULES:
        shutil.copy2(ROOT / name, out / name)
    shutil.copy2(ROOT / "wasm" / "index.html", out / "index.html")
    (out / "icon-192.png").write_bytes(hbicons.png_bytes(192))
    (out / "runtime.json").write_text(json.dumps({
        "version": VERSION,
        "modules": list(WASM_MODULES),
        "runtime": "pyodide 0.29.5",
    }, indent=2) + "\n", encoding="utf-8")
    guide = (ROOT / "wasm" / "README.md")
    if guide.is_file():
        shutil.copy2(guide, out / "README.md")
    target = DIST / f"{SLUG}-{VERSION}-wasm.zip"
    zip_tree(out, target)
    log(f"{out.relative_to(ROOT)}: serve it with 'python3 -m http.server' inside that folder")
    return target


# --------------------------------------------------------------------- entry point


def targets():
    return {
        "icons": build_icons,
        "source": build_source,
        "deb": build_deb,
        "rpm": build_rpm,
        "arch": build_arch,
        "frozen": build_frozen,
        "appimage": build_appimage,
        "windows": build_windows,
        "macos": build_macos,
        "pwa": build_pwa,
        "wasm": build_wasm,
    }


def build_all():
    available = targets()
    order = ["icons", "source"]
    if os.name == "nt":
        order.append("windows")
    elif sys.platform == "darwin":
        order.append("macos")
    else:
        order += ["deb", "appimage"]
        if shutil.which("rpmbuild"):
            order.append("rpm")
        if shutil.which("makepkg"):
            order.append("arch")
    order += ["pwa", "wasm"]
    for name in order:
        log(f"=== {name} ===")
        available[name]()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="packaging/build.py",
        description="Build installable HB Briefing packages.")
    parser.add_argument("target", nargs="?", default="all", choices=sorted(targets()) + ["all"])
    parser.add_argument("--list", action="store_true", help="list the targets and exit")
    parser.add_argument("--pure", action="store_true",
                        help="write the .deb without dpkg-deb (works on any host)")
    args = parser.parse_args(argv)
    if args.list:
        for name in sorted(targets()):
            print(name)
        return 0
    if not (ROOT / "hbnscan.py").is_file():
        raise SystemExit("[build] run this from a checkout of the repository")
    log(f"{APP_NAME} {VERSION} on {platform_tag()}")
    if args.target == "all":
        return build_all()
    if args.target == "deb":
        build_deb(pure=args.pure)
        return 0
    targets()[args.target]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
