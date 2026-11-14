# Fedora / RHEL / openSUSE spec. Build it with `python3 packaging/build.py rpm`, which puts the
# source tarball in place and points rpmbuild at this file.
%global debug_package %{nil}
%global slug hb-briefing

Name:           hb-briefing
Version:        1.0.0
Release:        1%{?dist}
Summary:        Local-first news and regulatory signal monitor with dossiers and actions
License:        MIT
URL:            https://github.com/James-HoneyBadger/HB_Briefing
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch
Requires:       python3 >= 3.8
Requires:       python3-tkinter
Requires:       python3-dotenv
Recommends:     poppler-utils

%description
HB Briefing monitors RSS and Atom feeds, grades every new item against your own profile,
verifies the flagged ones against the linked article, and keeps dossiers, actions and source
health in a local SQLite file. It works offline with a deterministic heuristic and can call an
OpenAI-compatible model when a key is configured. The desktop app, the terminal interface and
the web app all read the same database.

%prep
%setup -q

%install
rm -rf %{buildroot}
install -d %{buildroot}%{_libdir}/%{slug} %{buildroot}%{_bindir}
install -d %{buildroot}%{_datadir}/applications %{buildroot}%{_docdir}/%{slug}
cp -a *.py profiles *.example LICENSE README.md docs %{buildroot}%{_libdir}/%{slug}/

for pair in "hb-briefing:hbnscan_gui.py" "hb-briefing-cli:hbnscan.py" \
            "hb-briefing-web:hbnweb.py" "hb-briefing-tui:hbntui.py"; do
  name="${pair%%:*}"
  script="${pair##*:}"
  cat > "%{buildroot}%{_bindir}/${name}" <<EOF
#!/bin/sh
set -e
: "\${HB_BRIEFING_HOME:=\${XDG_DATA_HOME:-\$HOME/.local/share}/hb-briefing}"
export HB_BRIEFING_HOME
exec python3 %{_libdir}/%{slug}/${script} "\$@"
EOF
  chmod 0755 "%{buildroot}%{_bindir}/${name}"
done

install -m 0644 %{slug}.desktop %{buildroot}%{_datadir}/applications/
for size in 16 32 48 64 128 256 512; do
  install -d "%{buildroot}%{_datadir}/icons/hicolor/${size}x${size}/apps"
  install -m 0644 "packaging/icons/%{slug}-${size}.png" \
      "%{buildroot}%{_datadir}/icons/hicolor/${size}x${size}/apps/%{slug}.png"
done

%files
%{_bindir}/hb-briefing
%{_bindir}/hb-briefing-cli
%{_bindir}/hb-briefing-web
%{_bindir}/hb-briefing-tui
%{_libdir}/%{slug}
%{_datadir}/applications/%{slug}.desktop
%{_datadir}/icons/hicolor/*/apps/%{slug}.png
%license LICENSE
%doc README.md docs/USER-MANUAL.md docs/TECHNICAL-REFERENCE.md

%changelog
* Fri Nov 14 2026 James HoneyBadger <james-honeybadger@users.noreply.github.com> - 1.0.0-1
- First release: scan pipeline, dossiers, actions, source health, GUI, TUI and web app.
