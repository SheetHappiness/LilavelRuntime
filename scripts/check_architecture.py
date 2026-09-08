"""Small static checks for the LilavelRuntime ownership boundaries."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_SOURCE = ROOT / "apps" / "core" / "src"
DISCORD_SOURCE = ROOT / "apps" / "discord-adapter" / "src"

CORE_FORBIDDEN_IMPORTS = ("discord", "lilavel_discord_edge", "lilavel_discord_adapter")
ADAPTER_FORBIDDEN_MODULES = ("lilavel_core.persistence",)
ADAPTER_FORBIDDEN_NAMES = {
    "CanonicalMessage",
    "ConversationStore",
    "EvidenceRecord",
    "SQLiteConversationStore",
}


def _module_name(node: ast.Import | ast.ImportFrom) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if node.level != 0 or node.module is None:
        return ()
    return (node.module,)


def _matches(module: str, roots: tuple[str, ...]) -> bool:
    return any(module == root or module.startswith(f"{root}.") for root in roots)


def _core_violations() -> list[str]:
    violations: list[str] = []
    for path in sorted(CORE_SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for module in _module_name(node):
                if _matches(module, CORE_FORBIDDEN_IMPORTS):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: Core imports {module}")
    return violations


def _adapter_violations() -> list[str]:
    violations: list[str] = []
    for path in sorted(DISCORD_SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for module in _module_name(node):
                    if _matches(module, ADAPTER_FORBIDDEN_MODULES):
                        violations.append(
                            f"{path.relative_to(ROOT)}:{node.lineno}: adapter imports {module}"
                        )
                if isinstance(node, ast.ImportFrom) and node.module == "lilavel_core":
                    for alias in node.names:
                        if alias.name in ADAPTER_FORBIDDEN_NAMES:
                            violations.append(
                                f"{path.relative_to(ROOT)}:{node.lineno}: adapter imports {alias.name}"
                            )
    return violations


def main() -> int:
    violations = [*_core_violations(), *_adapter_violations()]
    if violations:
        print("ARCHITECTURE_GUARD=FAIL")
        print("\n".join(violations))
        return 1
    print("ARCHITECTURE_GUARD=PASS")
    print("Core has no Discord dependency; Discord adapter has no Core persistence ownership.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
