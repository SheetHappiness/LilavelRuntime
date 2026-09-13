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
SEMANTIC_GENERATION_CALLS = {"generate", "generate_for_run"}
LEGACY_SEMANTIC_COMPATIBILITY_CLASSES = {
    "MindAppraiser",
    "AutonomousCognitionRunner",
}
SCOPED_COGNITION_ENGINE_CLASSES = {
    "_ScopedModelGenerationMixin",
    "DispositionPlanner",
    "LocalCognitionEngine",
}
CANONICAL_COMPOSITION_FILES = {"cli.py", "kernel.py"}
E1_INTERVENTION_FORBIDDEN_MODULES = {
    "cognition_model",
    "conversation_adapter",
    "conversation_router",
    "kernel",
    "presence",
    "proposal_application",
    "tool_registry",
    "tool_session",
}
E1_INTERVENTION_FORBIDDEN_NAMES = {
    "ActionProposal",
    "ModelRuntime",
    "PresentationAction",
    "ProposalApplicationCoordinator",
    "ToolCall",
}
E1_INTERVENTION_FORBIDDEN_CALLS = {
    "apply",
    "execute",
    "generate",
    "generate_for_run",
    "route",
    "submit_cognition",
}
E2_COGNITION_FILES = {
    "cognition_model.py",
    "cognition_episode.py",
    "intervention.py",
}
E2_COGNITION_FORBIDDEN_IMPORTS = (
    "discord",
    "lilavel_discord_edge",
    "lilavel_discord_adapter",
    "aiohttp",
    "httpx",
    "requests",
    "socket",
)
E2_COGNITION_FORBIDDEN_CALLS = {
    "send",
    "send_message",
    "dispatch",
    "execute_batch",
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
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: Core imports {module}"
                    )
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
                                f"{path.relative_to(ROOT)}:{node.lineno}: adapter imports "
                                f"{alias.name}"
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


class _SemanticEntrypointVisitor(ast.NodeVisitor):
    """Find model generation calls outside the two documented ownership lanes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.class_stack: list[ast.ClassDef] = []
        self.violations: list[str] = []

    @property
    def current_class(self) -> ast.ClassDef | None:
        return self.class_stack[-1] if self.class_stack else None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        class_node = self.current_class
        class_name = class_node.name if class_node is not None else None
        function_name: str | None = None
        if isinstance(node.func, ast.Attribute):
            function_name = node.func.attr
        elif (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in SEMANTIC_GENERATION_CALLS
        ):
            function_name = str(node.args[1].value)

        if function_name in SEMANTIC_GENERATION_CALLS:
            if class_name == "PersistentPresenceRuntime":
                self._violate(node, "PersistentPresenceRuntime owns direct semantic generation")
            elif class_name in SCOPED_COGNITION_ENGINE_CLASSES:
                if function_name != "generate_for_run":
                    self._violate(node, f"{class_name} must use generate_for_run")
            elif class_name in LEGACY_SEMANTIC_COMPATIBILITY_CLASSES:
                class_docstring = (
                    ast.get_docstring(class_node, clean=False) if class_node is not None else None
                )
                if (
                    self.path.name != "presence.py"
                    or class_docstring is None
                    or "Deprecated standalone" not in class_docstring
                ):
                    self._violate(
                        node,
                        f"{class_name} generation is not explicitly deprecated compatibility",
                    )
            else:
                self._violate(node, "semantic generation call has no actor-owned route")

        self.generic_visit(node)

    def _violate(self, node: ast.Call, message: str) -> None:
        self.violations.append(f"{self.path}:{node.lineno}: {message}")


def _semantic_entrypoint_violations() -> list[str]:
    violations: list[str] = []
    for path in sorted(RUNTIME_SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _SemanticEntrypointVisitor(path)
        visitor.visit(tree)
        violations.extend(visitor.violations)

        if path.name not in CANONICAL_COMPOSITION_FILES:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else None
            )
            if called_name in LEGACY_SEMANTIC_COMPATIBILITY_CLASSES:
                violations.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: canonical composition constructs "
                    f"deprecated {called_name}"
                )

    cognition_model = RUNTIME_SOURCE / "lilavel_runtime" / "cognition_model.py"
    kernel = RUNTIME_SOURCE / "lilavel_runtime" / "kernel.py"
    cli = RUNTIME_SOURCE / "lilavel_runtime" / "cli.py"
    if cognition_model.exists():
        tree = ast.parse(cognition_model.read_text(encoding="utf-8"), filename=str(cognition_model))
        if not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "generate_for_run"
            for node in ast.walk(tree)
        ):
            violations.append("apps/runtime: LocalCognitionEngine has no scoped model route")
    if kernel.exists():
        tree = ast.parse(kernel.read_text(encoding="utf-8"), filename=str(kernel))
        if not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CognitionEpisodeRunner"
            for node in ast.walk(tree)
        ):
            violations.append(
                "apps/runtime: CognitionEpisodeRunner is missing from runtime composition"
            )
    if cli.exists():
        tree = ast.parse(cli.read_text(encoding="utf-8"), filename=str(cli))
        if not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "LocalCognitionEngine"
            for node in ast.walk(tree)
        ):
            violations.append("apps/runtime: canonical CLI composition lacks LocalCognitionEngine")
    return violations


def _e1_intervention_violations() -> list[str]:
    """Keep the inert E1 policy from acquiring a model or effect capability."""

    path = RUNTIME_SOURCE / "lilavel_runtime" / "intervention.py"
    if not path.exists():
        return []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level and module in E1_INTERVENTION_FORBIDDEN_MODULES:
                violations.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: E1 intervention imports .{module}"
                )
            for alias in node.names:
                if alias.name in E1_INTERVENTION_FORBIDDEN_NAMES:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: E1 intervention imports "
                        f"{alias.name}"
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in E1_INTERVENTION_FORBIDDEN_MODULES:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: E1 intervention imports "
                        f"{alias.name}"
                    )
        elif isinstance(node, ast.Name) and node.id in E1_INTERVENTION_FORBIDDEN_NAMES:
            violations.append(
                f"{path.relative_to(ROOT)}:{node.lineno}: E1 intervention references {node.id}"
            )
        elif isinstance(node, ast.Call):
            called_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else None
            )
            if called_name in E1_INTERVENTION_FORBIDDEN_CALLS:
                violations.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: E1 intervention calls "
                    f"{called_name}"
                )
    return violations


def _e2_ambient_violations() -> list[str]:
    """Keep ambient cognition advisory and route SPEAK through application."""

    violations: list[str] = []
    cognition_paths = sorted(
        path
        for path in (RUNTIME_SOURCE / "lilavel_runtime").glob("*.py")
        if path.name in E2_COGNITION_FILES
    )
    for path in cognition_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for module in _module_name(node):
                    if _matches(module, E2_COGNITION_FORBIDDEN_IMPORTS):
                        violations.append(
                            f"{path.relative_to(ROOT)}:{node.lineno}: cognition imports {module}"
                        )
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in E2_COGNITION_FORBIDDEN_CALLS:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: cognition calls "
                        f"effect/transport method {node.func.attr}"
                    )

    application_path = RUNTIME_SOURCE / "lilavel_runtime" / "proposal_application.py"
    if not application_path.exists():
        return ["apps/runtime: proposal application boundary is missing"]
    tree = ast.parse(application_path.read_text(encoding="utf-8"), filename=str(application_path))
    coordinator = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "ProposalApplicationCoordinator"
        ),
        None,
    )
    if coordinator is None:
        return ["apps/runtime: ProposalApplicationCoordinator is missing"]
    methods = {
        node.name: node
        for node in coordinator.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    execute_actions = methods.get("_execute_actions")
    revalidate_speak = methods.get("_revalidate_speak")
    if execute_actions is None or not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_revalidate_speak"
        for node in ast.walk(execute_actions)
    ):
        violations.append(
            "apps/runtime: ProposalApplicationCoordinator does not guard SPEAK before P4"
        )
    if revalidate_speak is None or not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "revalidate"
        for node in ast.walk(revalidate_speak)
    ):
        violations.append(
            "apps/runtime: SPEAK guard does not invoke current social revalidation"
        )
    if not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute_batch"
        for node in ast.walk(tree)
    ):
        violations.append("apps/runtime: SPEAK has no existing P4 batch application route")
    return violations


def main() -> int:
    violations = [
        *_core_violations(),
        *_runtime_violations(),
        *_semantic_entrypoint_violations(),
        *_e1_intervention_violations(),
        *_e2_ambient_violations(),
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
        "Discord adapter has no Core persistence/lifecycle ownership, and semantic model "
        "generation remains on scoped ConversationCore/LocalCognitionEngine/DispositionPlanner "
        "engine seams."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
