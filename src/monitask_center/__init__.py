"""Monitask Center: workday Monitask timer checks driven by launchd."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("monitask-center")  # pyproject.toml stays the single source of truth
except PackageNotFoundError:  # unbuilt checkout
    __version__ = "0+unknown"
