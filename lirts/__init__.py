"""lirts: a btop-style terminal dashboard for ports, processes, containers and services."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("lirts")
except PackageNotFoundError:  # running from a source checkout without installation
    __version__ = "0.7.1"

__all__ = ["__version__"]
