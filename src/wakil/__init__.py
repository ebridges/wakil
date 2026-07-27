"""wakil: a local-first CLI agent for a personal Markdown knowledge base."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("wakil")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
