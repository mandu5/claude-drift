"""claude-drift: replay your Claude Code sessions against a new model and see what changed."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("claude-drift")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"
