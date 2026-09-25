# Homebrew formula for lirts.  The live copy is in the tap
# github.com/EvilUnicornLabs/homebrew-lirts (Formula/lirts.rb); users install with
#   brew tap evilunicornlabs/lirts && brew install lirts
#
# Per release: set `url` to the new tag, put the sha256 the release workflow printed into
# `sha256`, regenerate the resource blocks from PyPI (the `mcp` extra included, so `lirts mcp`
# works from a brew install) and copy the file to the tap:
#   brew update-python-resources --extra-packages mcp packaging/homebrew/lirts.rb
class Lirts < Formula
  include Language::Python::Virtualenv

  desc "btop-style terminal dashboard for ports, processes, Docker containers and services"
  homepage "https://github.com/EvilUnicornLabs/lirts"
  url "https://github.com/EvilUnicornLabs/lirts/archive/refs/tags/v0.7.0.tar.gz"
  sha256 "REPLACE_WITH_SHA256_FROM_THE_RELEASE"
  license "Apache-2.0"
  head "https://github.com/EvilUnicornLabs/lirts.git", branch: "master"

  depends_on "python@3.12"

  # `brew update-python-resources --extra-packages mcp` fills these in from PyPI.
  # resource "psutil" do ... end

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "lirts", shell_output("#{bin}/lirts --version")
    assert_match "PORT", shell_output("#{bin}/lirts list --no-docker --no-probe")
  end
end
