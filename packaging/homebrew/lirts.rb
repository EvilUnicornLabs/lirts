# Homebrew formula for lirts.
#
# Publish a release tag (e.g. v0.6.0), then fill `url` / `sha256` and run
#   brew update-python-resources packaging/homebrew/lirts.rb
# to regenerate the `resource` blocks from PyPI.  Put the file in a tap
# (github.com/<you>/homebrew-lirts/Formula/lirts.rb) and users install with
#   brew tap <you>/lirts && brew install lirts
class Lirts < Formula
  include Language::Python::Virtualenv

  desc "btop-style terminal dashboard for ports, processes, Docker containers and services"
  homepage "https://github.com/EvilUnicornLabs/lirts"
  url "https://github.com/EvilUnicornLabs/lirts/archive/refs/tags/v0.6.0.tar.gz"
  sha256 "REPLACE_WITH_SHA256_OF_THE_RELEASE_TARBALL"
  license "MIT"
  head "https://github.com/EvilUnicornLabs/lirts.git", branch: "master"

  depends_on "python@3.12"

  # `brew update-python-resources` fills these in from the lock of pyproject.toml.
  # resource "psutil" do ... end

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "lirts", shell_output("#{bin}/lirts --version")
    assert_match "PORT", shell_output("#{bin}/lirts list --no-docker --no-probe")
  end
end
