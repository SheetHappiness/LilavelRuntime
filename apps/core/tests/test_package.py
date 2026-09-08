"""Import and packaging smoke coverage for Lilavel Core."""

from importlib.metadata import version

import lilavel_core


def test_package_version_matches_installed_metadata() -> None:
    """The importable package and distribution metadata expose one version."""
    assert lilavel_core.__version__ == version("lilavel-core")
