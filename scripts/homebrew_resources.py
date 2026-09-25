"""Regenerate the ``resource`` blocks of the Homebrew formula from PyPI.

``brew update-python-resources`` does the same, but it wants the formula inside a tap and a
pip that accepts ``--uploaded-prior-to``; this script needs neither.  It asks pip for a
dry-run resolution of the formula's source tarball plus the ``mcp`` extra, looks every
package up on PyPI for its sdist URL and sha256, and rewrites the blocks in place.

    venv/bin/python scripts/homebrew_resources.py packaging/homebrew/lirts.rb
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

EXTRAS = ["mcp"]
PYPI = "https://pypi.org/pypi/{name}/{version}/json"
# Homebrew's python of the formula, when it is installed; else whatever runs this script.
BREW_PYTHON = "/opt/homebrew/opt/python@3.12/libexec/bin/python"


def resolve(url: str) -> list[tuple[str, str]]:
    """``(name, version)`` of every package pip would install for ``url`` and the extras."""
    python = BREW_PYTHON if Path(BREW_PYTHON).exists() else sys.executable
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        subprocess.run(
            [
                python,
                "-m",
                "pip",
                "install",
                "-q",
                "--disable-pip-version-check",
                "--dry-run",
                "--ignore-installed",
                f"--report={report}",
                url,
                *EXTRAS,
            ],
            check=True,
        )
        data = json.loads(report.read_text())
    out = []
    for item in data["install"]:
        meta = item["metadata"]
        if meta["name"].lower() != "lirts":
            out.append((meta["name"].replace("_", "-"), meta["version"]))
    return sorted(out, key=lambda p: p[0].lower())


def sdist(name: str, version: str) -> tuple[str, str]:
    """The sdist URL and sha256 of one release on PyPI."""
    with urllib.request.urlopen(PYPI.format(name=name, version=version)) as fh:
        data = json.load(fh)
    for entry in data["urls"]:
        if entry["packagetype"] == "sdist":
            return entry["url"], entry["digests"]["sha256"]
    raise SystemExit(f"{name} {version} has no sdist on PyPI")


def rewrite(formula: Path) -> int:
    text = formula.read_text()
    match = re.search(r'^\s+url "([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise SystemExit("no url line in the formula")
    packages = resolve(match.group(1))
    blocks = "".join(
        f'  resource "{name}" do\n    url "{url}"\n    sha256 "{digest}"\n  end\n\n'
        for name, version in packages
        for url, digest in [sdist(name, version)]
    )
    without = re.sub(r'(  resource "[^"]+" do\n(?:    .*\n)*  end\n\n?)+', "", text)
    without = re.sub(
        r"  # `brew update-python-resources.*\n  # resource \"psutil\" do \.\.\. end\n\n?",
        "",
        without,
    )
    updated = without.replace("  def install\n", blocks + "  def install\n", 1)
    formula.write_text(updated)
    return len(packages)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    count = rewrite(Path(sys.argv[1]))
    print(f"{count} resources written to {sys.argv[1]}")
