#!/usr/bin/env sh
# One-line installer:  curl -fsSL https://raw.githubusercontent.com/EvilUnicornLabs/lirts/master/install.sh | sh
# Installs pipx if needed, then lirts from GitHub (or from a local checkout when run inside one).
set -eu

REPO="${LIRTS_REPO:-https://github.com/EvilUnicornLabs/lirts}"

have() { command -v "$1" >/dev/null 2>&1; }

if ! have pipx; then
  echo "pipx not found; installing it..."
  if have brew; then
    brew install pipx
  elif have python3; then
    python3 -m pip install --user pipx
    python3 -m pipx ensurepath
  else
    echo "Need python3 or Homebrew to install pipx." >&2
    exit 1
  fi
fi

if [ -f "pyproject.toml" ] && grep -q '^name = "lirts"' pyproject.toml; then
  echo "Installing lirts from this checkout..."
  pipx install --force .
else
  echo "Installing lirts from $REPO ..."
  pipx install --force "git+${REPO}.git"
fi

echo
echo "Done. Run: lirts        (dashboard)"
echo "     lirts config init  (write ~/.config/lirts/config.yaml)"
echo "     lirts --install-completion  (shell completion)"
