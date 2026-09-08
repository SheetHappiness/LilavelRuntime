"""Import and packaging smoke coverage for the runtime package."""

from importlib.metadata import metadata, version

import lilavel_runtime


def test_package_version_matches_installed_metadata() -> None:
    assert lilavel_runtime.__version__ == version("lilavel-runtime")


def test_runtime_depends_only_on_provider_neutral_core() -> None:
    assert metadata("lilavel-runtime").get_all("Requires-Dist") == ["lilavel-core"]
