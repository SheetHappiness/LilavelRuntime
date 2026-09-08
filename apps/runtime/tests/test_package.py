"""Import and packaging smoke coverage for the runtime package."""

from importlib.metadata import metadata, version

import lilavel_runtime


def test_package_version_matches_installed_metadata() -> None:
    assert lilavel_runtime.__version__ == version("lilavel-runtime")


def test_runtime_has_no_required_distribution_dependencies() -> None:
    assert metadata("lilavel-runtime").get_all("Requires-Dist") is None
