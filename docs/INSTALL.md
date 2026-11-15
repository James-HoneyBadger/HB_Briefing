# Installing HB Briefing

One program, seven ways to land it on a machine. The interfaces are identical whatever route
you take: the desktop window, the terminal UI and the browser app all read the same SQLite file
in your own user account.

| Platform | Package | Command |
| --- | --- | --- |
| Debian, Ubuntu, Mint, Pop!_OS, Zorin, Kali | `.deb` | `sudo apt install ./hb-briefing_1.0.0_all.deb` |
| Fedora, RHEL, Rocky, Alma, openSUSE | `.rpm` | `sudo dnf install ./hb-briefing-1.0.0-1.*.noarch.rpm` |
| Any x86_64 Linux | `.AppImage` | `chmod +x` and run; no install |
| Arch, Manjaro, EndeavourOS | `PKGBUILD` | `makepkg -si` |
| Windows 10/11 | `.exe` installer | double-click |
| macOS 10.15+ | `.dmg` | drag to Applications |
| iPadOS, ChromeOS, Android | web app | open the URL, *Add to Home Screen* |
| Anything with Python 3.8+ | source | `./start.sh` or `pip install -r requirements.txt` |
| Nothing at all (no install, any device) | WebAssembly | serve `dist/hb-briefing-1.0.0-wasm/` |

Every package keeps two things apart:

- **Program files** live read-only in `/usr/lib/hb-briefing` (Linux), `%LOCALAPPDATA%\Programs`
  (Windows) or `HB Briefing.app` (macOS). Upgrading or uninstalling them cannot damage data.
- **Your state** lives in a per-user directory — the database, `feeds.txt`, `watchlist.txt`,
  `topics.txt`, `profiles/`, `settings.json` and `.env`:

| Platform | State directory |
| --- | --- |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/hb-briefing` |
| Windows | `%LOCALAPPDATA%\HB Briefing` |
| macOS | `~/Library/Application Support/HB Briefing` |
| Source checkout | the repository itself (nothing moves) |

Override it anywhere with `HB_BRIEFING_HOME=/path/to/state`. That variable is what makes a
USB-stick install, a shared NFS home or a second isolated profile trivial.

---

## Linux

### Debian, Ubuntu, Mint, Pop!_OS, Zorin, Kali

```bash
sudo apt install ./hb-briefing_1.0.0_all.deb
hb-briefing                     # desktop app
hb-briefing-cli                 # one scan from a terminal
hb-briefing-cli --daemon        # keep scanning on the interval in settings
hb-briefing-web                 # browser app on http://127.0.0.1:8765/
hb-briefing-tui                 # terminal interface
```

The package pulls `python3`, `python3-tk` and `python3-dotenv` from your distribution. It is
`Architecture: all` because the program is pure Python: the same file installs on x86_64, arm64
and Raspberry Pi. `poppler-utils` is recommended, not required — it enables reading PDF orders
during deep reads.

`hb-briefing` also appears in the desktop menu under *Office* and *News*.

Remove with `sudo apt remove hb-briefing`. Your data directory is untouched; delete it by hand
if you want a clean slate.

### Fedora, RHEL, Rocky, Alma, openSUSE

```bash
sudo dnf install ./hb-briefing-1.0.0-1.*.noarch.rpm     # Fedora / RHEL family
sudo zypper install ./hb-briefing-1.0.0-1.*.noarch.rpm  # openSUSE
```

On openSUSE the dependency is named `python3-python-dotenv`; if `dnf`/`zypper` refuses to
resolve `python3-dotenv`, install that name instead and rerun. Everything else is identical to
the Debian route.

### Any Linux: AppImage

```bash
chmod +x hb-briefing-1.0.0-linux-x86_64.AppImage
./hb-briefing-1.0.0-linux-x86_64.AppImage
```

The AppImage carries its own Python, Tk and libraries, so it works on an old distribution, a
locked-down work laptop or a live USB with no package manager. Nothing is written outside your
state directory. If your system lacks FUSE, run it with
`APPIMAGE_EXTRACT_AND_RUN=1 ./hb-briefing-1.0.0-linux-x86_64.AppImage`.

### Arch, Manjaro, EndeavourOS

Download `PKGBUILD` from the release, put it in an empty directory next to the matching source
tarball, then:

```bash
makepkg -si
```

Dependencies are `python`, `python-dotenv` and `tk`; `poppler` is optional and enables PDF
reading.

### Autostart after login

Packages install a desktop entry but nothing that runs at login, because the scan interval is
already handled by the app. To have HB Briefing start itself, add a user unit:

```ini
# ~/.config/systemd/user/hb-briefing.service
[Unit]
Description=HB Briefing scanner

[Service]
ExecStart=/usr/bin/hb-briefing-cli --daemon
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now hb-briefing.service
```

---

## Windows 10 and 11

Run `hb-briefing-1.0.0-windows-setup.exe`. It installs per user into
`%LOCALAPPDATA%\Programs\HBBriefing`, so no administrator password is needed, and it adds Start
menu entries for the desktop app, the command line and the web app.

The installer leaves `%LOCALAPPDATA%\HB Briefing` alone on uninstall: your database, feeds and
`.env` survive an upgrade or a reinstall.

Prefer no installer? Use `hb-briefing-1.0.0-windows-portable.zip`: unzip anywhere and run
`hb-briefing.exe`. The folder carries its own Python, so no interpreter is needed on the
machine.

SmartScreen may warn about an unsigned executable the first time; choose *More info* → *Run
anyway*. Signing certificates are the only thing missing, not code review.

---

## macOS 10.15 and newer

Open the `.dmg`, drag **HB Briefing** to *Applications*, then launch it. The bundle is ad-hoc
signed, which is enough for Apple Silicon but not notarised, so the first launch needs
*System Settings → Privacy & Security → Open Anyway* (or `xattr -dr com.apple.quarantine
/Applications/HB\ Briefing.app`).

The published `.dmg` is **arm64** (Apple Silicon), because that is the runner GitHub provides.
On an Intel Mac, run from source (the section below) or build your own bundle on that machine:
`python3 packaging/build.py macos`.

The three console front ends are inside the bundle and can be scripted:

```bash
/Applications/HB\ Briefing.app/Contents/MacOS/hb-briefing-cli --export ~/Desktop/digest.md
/Applications/HB\ Briefing.app/Contents/MacOS/hb-briefing-web --host 0.0.0.0
```

A Homebrew formula is published with each release (`hb-briefing.rb`); point it at this
repository or use it as a template.

---

## iPadOS, iPhone, ChromeOS, Android

There is no way to install a Python desktop program on iPadOS: the App Store forbids shipping
an interpreter, and sideloading is not a general solution. The honest answer is the built-in
web app, which is the same program behind an HTTP server — and it installs to the home screen
as a real app.

1. Run the web front end on a machine that is always on (a laptop, a mini PC, a Raspberry Pi):

   ```bash
   hb-briefing-web --host 0.0.0.0 --port 8765
   ```

   Note the token it prints: reading works without it, but starting scans, dismissing signals
   and changing settings need it.

2. On the device, open `http://<the-machine-ip>:8765/`.

3. Install it:
   - **iPadOS / iOS**: *Share* → *Add to Home Screen*. It opens full screen with its own icon,
     title and status bar.
   - **ChromeOS**: the install icon in the omnibox, or *⋮* → *Install HB Briefing*. It appears
     in the shelf and in the launcher like a native app. ChromeOS can also run the `.deb` in
     the Linux (Crostini) container if you prefer the desktop app.
   - **Android**: *⋮* → *Install app*.

Reading works offline once the service worker has cached the shell and the last responses.
That needs a *secure context*: `http://localhost` or a TLS reverse proxy. Over a plain LAN
address the app still installs and runs — it simply does not cache for offline reading.

The **web bundle** (`hb-briefing-<version>-*-web.zip`) is a self-contained host: unzip, run
`./hb-briefing-web --host 0.0.0.0`, and the phone or Chromebook installs from it. That is the
closest thing to a native iPadOS package that the platform allows.

> Serving on `0.0.0.0` exposes the signals to anyone who can reach the port. Keep it on a
> trusted network, or put a TLS proxy with authentication in front of it.

---

## Nothing installed: the WebAssembly page

A single page that runs the engine itself, compiled to WebAssembly by
[Pyodide](https://pyodide.org). Useful for a locked-down laptop, a machine that will never get
Python, or a tablet that already has a browser but no way to install anything.

```bash
python3 packaging/build.py wasm
cd dist/hb-briefing-1.0.0-wasm
python3 -m http.server 8765
# open http://127.0.0.1:8765/ and drop a file on it
```

Or take `hb-briefing-1.0.0-wasm.zip` from the release, unzip it anywhere, and serve that folder
the same way. A local server is required — a `file://` page cannot fetch its own modules.

It accepts a JSON or CSV export from the desktop app, an RSS/Atom feed, an OPML feed list (it
will offer to fetch the feeds, subject to the publisher's CORS policy), or plain headlines. An
export keeps the verdicts it was exported with; anything else is scored in the tab by the same
`heuristic_verdict()` the desktop app uses, and rewrites are grouped by the same MinHash pass.

What it cannot do is honest to state: there is no database in the tab (nothing survives a
reload), no AI stages (they need a key and a long-lived process), and no feed polling, because
browsers cannot bypass CORS. For those, run `hb-briefing-web` on a machine and use the phone or
Chromebook as the screen.

The first load downloads the Python runtime from the Pyodide CDN — that is the only network
request the page makes. After that it works offline for files and pasted text.

---

## Source

```bash
git clone https://github.com/James-HoneyBadger/HB_Briefing
cd HB_Briefing
./start.sh
```

`start.sh` checks for Python 3.8+, installs `python3-tk` through the system package manager if
Tkinter is missing, builds a project-local `.venv`, installs `requirements.txt` into it, and
seeds `.env`, `feeds.txt`, `watchlist.txt` and `topics.txt` from their `.example` twins. Run it
again as often as you like; it only installs what is missing.

Prefer to do it by hand:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # optional: add LLM_API_KEY for the AI stages
python3 hbnscan_gui.py        # or hbnscan.py / --serve / --tui
```

The source tree keeps its state beside the code, which is why a checkout and an installed copy
never fight over the same database.

---

## First run

Whatever route you took, the first launch creates the state directory and seeds it:

```
feeds.txt         one feed per line (seeded from feeds.txt.example)
watchlist.txt     entities you care about, with categories and priorities
topics.txt        discovery queries turned into feeds
profiles/         scoring profiles (bundled set: default, regulatory, litigation, ...)
.env              optional API key and limits
watchdog_state.db created on the first scan or first open of any front end
```

Then: **Configure** in the desktop app (or `hb-briefing-cli --export`) points at the same files,
and *Settings → Data* shows where the database lives.

## Verifying an install

```bash
hb-briefing-cli --reclassify --dry-run   # reads feeds, profiles and the database, writes nothing
hb-briefing-cli --check                  # validates an API key with one live call
```

Both are safe to run any time. The first one is what the automated tests use to prove a package
is intact.

## Data, backup and removal

- The database is SQLite in WAL mode; back it up with *Settings → Data → Back up now* or by
  copying the whole state directory while the app is closed.
- Upgrades run schema migrations automatically and take a timestamped backup first
  (`watchdog_state-backup-*.db` beside the database).
- Uninstalling removes program files only. Delete the state directory to remove your data.
