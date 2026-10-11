"""Enforce ARCHITECTURE §10 within src/daybook, including relative imports."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

RESTRICTED = {
    "sqlite3": {"journal", "skills._ledger"},
    "httpx": {"model", "shell"},
    "PySide6": {"shell"},
    "Xlib": {"sense"},
    "mss": {"sense"},
}


def violations(source: str, module: str) -> list[str]:
    errors: list[str] = []
    parts = module.split(".")
    owner = parts[1] if len(parts) > 1 else ""
    package = parts[:-1]
    for node in ast.walk(ast.parse(source)):
        targets: list[str] = []
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                prefix = package[: len(package) - node.level + 1]
                base = ".".join(prefix + ([base] if base else []))
            targets = [base, *[f"{base}.{alias.name}" for alias in node.names]]
        else:
            continue
        for target in targets:
            target_parts = target.split(".")
            if target_parts[0] in RESTRICTED:
                allowed = RESTRICTED[target_parts[0]]
                relative = ".".join(parts[1:])
                if not any(
                    relative == x or relative.startswith(x + ".") for x in allowed
                ):
                    errors.append(
                        f"{module}:{node.lineno}: {target_parts[0]} is restricted"
                    )
            if (
                len(target_parts) >= 3
                and target_parts[0] == "daybook"
                and target_parts[1] != owner
                and any(p.startswith("_") for p in target_parts[2:])
            ):
                errors.append(
                    f"{module}:{node.lineno}: cross-package internal import {target}"
                )
    return list(dict.fromkeys(errors))


def check(root: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(root.rglob("*.py")):
        module = ".".join(("daybook", *path.relative_to(root).with_suffix("").parts))
        errors.extend(violations(path.read_text(), module))
    return errors


if __name__ == "__main__":
    found = check(Path(__file__).resolve().parents[1] / "src/daybook")
    print("\n".join(found) if found else "Import boundaries: OK")
    sys.exit(bool(found))
