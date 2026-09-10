"""Import and packaging smoke coverage for the runtime package."""

from importlib.metadata import metadata, version
from pathlib import Path

import lilavel_runtime


def test_package_version_matches_installed_metadata() -> None:
    assert lilavel_runtime.__version__ == version("lilavel-runtime")


def test_runtime_dependencies_remain_provider_neutral_and_cli_local() -> None:
    assert metadata("lilavel-runtime").get_all("Requires-Dist") == [
        "lilavel-contracts",
        "lilavel-core",
        "prompt-toolkit==3.0.52",
    ]


def test_presence_semantics_do_not_import_terminal_renderer() -> None:
    package = Path(__file__).parents[1] / "src" / "lilavel_runtime"

    assert "prompt_toolkit" not in (package / "presence.py").read_text(encoding="utf-8")
