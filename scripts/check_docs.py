"""Small integrity checks for repository Markdown documentation."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRECTORIES = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "venv",
}

LINK_PATTERN = re.compile(
    r"(?<!!\])\[[^\]]+\]\(\s*(?:<(?P<bracketed>[^>]+)>|(?P<plain>[^\s)]+))"
)
HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
HTML_ANCHOR_PATTERN = re.compile(
    r"<a\b[^>]+(?:id|name)=[\"']([^\"']+)[\"']", re.IGNORECASE
)
COMMAND_PATH_PATTERN = re.compile(
    r"^\s*(?:Set-Location|cd)\s+(?:-LiteralPath\s+)?"
    r"(?:(?:\"(?P<double>[^\"]+)\")|(?:'(?P<single>[^']+)')|(?P<bare>[^\s;|&]+))",
    re.IGNORECASE,
)


def _markdown_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.md")
        if not any(part in SKIP_DIRECTORIES for part in path.relative_to(ROOT).parts)
    )


def _github_anchor(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = text.strip().rstrip("#").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s+", "-", text).strip("-")


def _anchors(content: str) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    for line in content.splitlines():
        match = HEADING_PATTERN.match(line)
        if not match:
            continue
        anchor = _github_anchor(match.group(1))
        if not anchor:
            continue
        count = counts.get(anchor, 0)
        anchors.add(anchor if count == 0 else f"{anchor}-{count}")
        counts[anchor] = count + 1
    anchors.update(HTML_ANCHOR_PATTERN.findall(content))
    return anchors


def _inside_repository(path: Path) -> bool:
    try:
        path.relative_to(ROOT)
    except ValueError:
        return False
    return True


def _resolve_local_target(source: Path, target: str) -> Path | None:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return None
    if not parsed.path:
        return source
    candidate = (source.parent / unquote(parsed.path)).resolve()
    return candidate if _inside_repository(candidate) else candidate


def _command_path(match: re.Match[str]) -> str:
    return next(value for value in match.group("double", "single", "bare") if value is not None)


def _check_file(path: Path, violations: list[str]) -> tuple[int, int, int]:
    lines = path.read_text(encoding="utf-8").splitlines()
    content = "\n".join(lines)
    checked_links = 0
    checked_anchors = 0
    checked_commands = 0
    in_fence = False
    fence_marker = ""

    for line_number, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
            continue

        if in_fence:
            command_match = COMMAND_PATH_PATTERN.match(line)
            if command_match:
                checked_commands += 1
                command_path = _command_path(command_match)
                if command_path.startswith(("$", "%")):
                    continue
                resolved = (ROOT / command_path.replace("\\", "/")).resolve()
                if not _inside_repository(resolved) or not resolved.exists():
                    violations.append(
                        f"{path.relative_to(ROOT)}:{line_number}: command path does not exist: {command_path}"
                    )
            continue

        for match in LINK_PATTERN.finditer(line):
            target = match.group("bracketed") or match.group("plain")
            if target.startswith(("#", "http:", "https:", "mailto:")):
                if target.startswith("#"):
                    checked_links += 1
                    anchor = target[1:]
                    checked_anchors += 1
                    if anchor and anchor not in _anchors(content):
                        violations.append(
                            f"{path.relative_to(ROOT)}:{line_number}: missing Markdown anchor: #{anchor}"
                        )
                continue

            checked_links += 1
            parsed = urlsplit(target)
            resolved = _resolve_local_target(path, target)
            if resolved is None:
                continue
            if not _inside_repository(resolved) or not resolved.exists():
                violations.append(
                    f"{path.relative_to(ROOT)}:{line_number}: local link target does not exist: {target}"
                )
                continue
            if parsed.fragment:
                checked_anchors += 1
                if resolved.is_file() and parsed.fragment not in _anchors(
                    resolved.read_text(encoding="utf-8")
                ):
                    violations.append(
                        f"{path.relative_to(ROOT)}:{line_number}: missing Markdown anchor: {target}"
                    )

    return checked_links, checked_anchors, checked_commands


def main() -> int:
    violations: list[str] = []
    markdown_files = _markdown_files()
    link_count = anchor_count = command_count = 0
    for path in markdown_files:
        links, anchors, commands = _check_file(path, violations)
        link_count += links
        anchor_count += anchors
        command_count += commands

    if violations:
        print("DOCS_INTEGRITY=FAIL")
        print("\n".join(violations))
        return 1

    print("DOCS_INTEGRITY=PASS")
    print(
        f"Markdown files: {len(markdown_files)}; local links: {link_count}; "
        f"anchors: {anchor_count}; command paths: {command_count}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
