"""Small static checks for the LilavelRuntime ownership boundaries."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_SOURCE = ROOT / "apps" / "core" / "src"
RUNTIME_SOURCE = ROOT / "apps" / "runtime" / "src"
DISCORD_SOURCE = ROOT / "apps" / "discord-adapter" / "src"

CORE_FORBIDDEN_IMPORTS = (
    "discord",
    "lilavel_discord_edge",
    "lilavel_discord_adapter",
    "lilavel_runtime",
)
RUNTIME_FORBIDDEN_IMPORTS = (
    "discord",
    "lilavel_discord_edge",
    "lilavel_discord_adapter",
    "neuro",
    "neuro_api",
    "neuro_sdk",
)
ADAPTER_FORBIDDEN_MODULES = ("lilavel_core.persistence",)
ADAPTER_FORBIDDEN_NAMES = {
    "CanonicalMessage",
    "ConversationStore",
    "EvidenceRecord",
    "SQLiteConversationStore",
}
ADAPTER_FORBIDDEN_CORE_OPERATIONS = {"start_turn", "generate", "cancel"}


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


def _discord_environment_violations() -> list[str]:
    path = DISCORD_SOURCE / "lilavel_discord_edge" / "edge.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    environment = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "_DiscordEnvironment"
        ),
        None,
    )
    if environment is None:
        return ["apps/discord-adapter: Discord environment boundary is missing"]

    violations: list[str] = []
    names = {node.id for node in ast.walk(environment) if isinstance(node, ast.Name)}
    if "WorldEvent" not in names:
        violations.append("apps/discord-adapter: Discord observations bypass WorldEvent")
    if "ToolResult" not in names:
        violations.append("apps/discord-adapter: Discord actions bypass ToolResult")
    for name in ("ConversationCore", "ModelRuntime"):
        if name in names:
            violations.append(f"apps/discord-adapter: Discord environment owns {name}")
    for node in ast.walk(environment):
        if isinstance(node, ast.Attribute) and node.attr in ADAPTER_FORBIDDEN_CORE_OPERATIONS:
            violations.append(
                f"{path.relative_to(ROOT)}:{node.lineno}: Discord environment owns Core operation "
                f"{node.attr}"
            )
    return violations


def _runtime_violations() -> list[str]:
    violations: list[str] = []
    for path in sorted(RUNTIME_SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for module in _module_name(node):
                if _matches(module, RUNTIME_FORBIDDEN_IMPORTS):
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: runtime imports {module}"
                    )
    return violations


def main() -> int:
    violations = [
        *_core_violations(),
        *_runtime_violations(),
        *_adapter_violations(),
        *_discord_environment_violations(),
    ]
    if violations:
        print("ARCHITECTURE_GUARD=FAIL")
        print("\n".join(violations))
        return 1
    print("ARCHITECTURE_GUARD=PASS")
    print(
        "Core has no Discord/runtime dependency; runtime has no Discord/Neuro dependency; "
        "Discord adapter has no Core persistence/lifecycle ownership and uses typed "
        "WorldEvent/action boundaries."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
