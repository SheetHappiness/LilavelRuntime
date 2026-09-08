"""Structural architecture checks for Core package ownership boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

_CORE_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CORE_SOURCE = _CORE_PROJECT_ROOT / "src" / "lilavel_core"
_FORBIDDEN_IMPORT_ROOTS = ("discord", "lilavel_discord_edge")


def _is_forbidden_import(module: str) -> bool:
    return any(module == root or module.startswith(f"{root}.") for root in _FORBIDDEN_IMPORT_ROOTS)


def _forbidden_imports(source: str, source_name: str) -> list[str]:
    tree = ast.parse(source, filename=source_name)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or node.module is None:
                continue
            imported_modules = (node.module,)
        else:
            continue

        for module in imported_modules:
            if _is_forbidden_import(module):
                violations.append(f"{source_name}:{node.lineno}: forbidden import '{module}'")
    return violations


def _core_source_violations() -> list[str]:
    violations: list[str] = []
    for path in sorted(_CORE_SOURCE.rglob("*.py")):
        source_name = str(path.relative_to(_CORE_PROJECT_ROOT))
        violations.extend(_forbidden_imports(path.read_text(encoding="utf-8"), source_name))
    return violations


def test_core_source_has_no_direct_discord_imports() -> None:
    violations = _core_source_violations()
    assert not violations, "Core must remain independent of Discord:\n" + "\n".join(violations)


def test_forbidden_absolute_import_forms_are_rejected() -> None:
    source = """
import discord
from lilavel_discord_edge import DiscordTextEdge
from discord.abc import Messageable
from lilavel_discord_edge.transport import DiscordMessageSink
"""

    violations = _forbidden_imports(source, "<import-boundary-fixture>")

    assert len(violations) == 4
    assert any("import 'discord'" in violation for violation in violations)
    assert any("import 'lilavel_discord_edge'" in violation for violation in violations)
    assert any("import 'discord.abc'" in violation for violation in violations)
    assert any("import 'lilavel_discord_edge.transport'" in violation for violation in violations)


def test_comments_strings_and_core_imports_are_not_violations() -> None:
    source = '''
"""
import discord
from lilavel_discord_edge import not_an_import
"""
from lilavel_core import ContextMessage
# from discord import not_an_import
'''

    assert _forbidden_imports(source, "<non-violation-fixture>") == []
