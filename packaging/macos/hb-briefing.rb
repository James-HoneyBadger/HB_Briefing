# Homebrew formula. `python3 packaging/build.py source` writes the filled-in copy to
# dist/hb-briefing.rb, with the version and the real checksum of the release tarball.
#
#   brew tap James-HoneyBadger/hb-briefing https://github.com/James-HoneyBadger/HB_Briefing
#   brew install hb-briefing
class HbBriefing < Formula
  include Language::Python::Virtualenv

  desc "Local-first news and regulatory signal monitor with dossiers and actions"
  homepage "https://github.com/James-HoneyBadger/HB_Briefing"
  url "https://github.com/James-HoneyBadger/HB_Briefing/archive/refs/tags/v@VERSION@.tar.gz"
  sha256 "@SHA256@"
  license "MIT"

  depends_on "python@3.12"
  depends_on "python-tk@3.12"

  # The only third-party runtime dependency is a pure-Python .env reader.
  resource "python-dotenv" do
    url "https://files.pythonhosted.org/packages/source/p/python-dotenv/python_dotenv-1.0.1.tar.gz"
    sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  end

  def install
    venv = virtualenv_create(libexec, "python3.12")
    venv.pip_install resource("python-dotenv")
    libexec.install Dir["*.py"], "profiles", "docs", *Dir["*.example"], "README.md", "LICENSE"

    %w[hb-briefing:hbnscan_gui.py hb-briefing-cli:hbnscan.py
       hb-briefing-web:hbnweb.py hb-briefing-tui:hbntui.py].each do |pair|
      name, script = pair.split(":")
      (bin/name).write <<~SH
        #!/bin/sh
        set -e
        : "${HB_BRIEFING_HOME:=${XDG_DATA_HOME:-$HOME/.local/share}/hb-briefing}"
        export HB_BRIEFING_HOME
        exec "#{libexec}/bin/python" "#{libexec}/#{script}" "$@"
      SH
      (bin/name).chmod 0755
    end
  end

  test do
    assert_match "Reclassify", shell_output("#{bin}/hb-briefing-cli --reclassify --dry-run 2>&1")
  end
end
