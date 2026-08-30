"""Guard: the Unraid image must ship every first-party import (stdlib only)."""

from __future__ import annotations

import ast
import fnmatch
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENTRYPOINTS = ("main.py", "worker.py")
REQUIRED_PATHS = (
    "main.py",
    "worker.py",
    "entrypoint.sh",
    "gear_catalog.json",
    "requirements.txt",
    "static",
    "routes",
    "services",
)


def _iter_py(path: Path):
    if path.is_file() and path.suffix == ".py":
        yield path
    elif path.is_dir():
        yield from path.rglob("*.py")


def _top_level_imports(py_file: Path) -> set[str]:
    tree = ast.parse(py_file.read_text(), filename=str(py_file))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _is_first_party(name: str) -> bool:
    return (ROOT / f"{name}.py").is_file() or (ROOT / name / "__init__.py").is_file()


def _module_path(name: str) -> Path:
    package = ROOT / name
    if (package / "__init__.py").is_file():
        return package
    return ROOT / f"{name}.py"


def runtime_modules() -> set[str]:
    seen: set[str] = set()
    queue = [Path(entry).stem for entry in ENTRYPOINTS]
    while queue:
        name = queue.pop()
        if name in seen or not _is_first_party(name):
            continue
        seen.add(name)
        for py in _iter_py(_module_path(name)):
            for imported in _top_level_imports(py):
                if imported not in seen and _is_first_party(imported):
                    queue.append(imported)
    return seen


def root_packages() -> list[str]:
    return sorted(
        path.name
        for path in ROOT.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    )


def dockerignore_patterns() -> list[str]:
    patterns: list[str] = []
    for raw in (ROOT / ".dockerignore").read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        patterns.append(line)
    return patterns


def is_ignored(rel: str, patterns: list[str]) -> bool:
    rel = rel.strip("/")
    name = Path(rel).name
    parts = Path(rel).parts
    for pattern in patterns:
        cleaned = pattern.rstrip("/")
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, cleaned):
            return True
        if fnmatch.fnmatch(name, cleaned):
            return True
        if any(fnmatch.fnmatch(part, cleaned) for part in parts):
            return True
    return False


class DockerImageContentsTests(unittest.TestCase):
    def test_dockerfile_copies_full_app_tree(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertRegex(
            dockerfile,
            r"(?m)^\s*COPY\s+\.\s+\.\s*$",
            "Dockerfile must COPY . . so new packages are not omitted",
        )
        self.assertNotRegex(
            dockerfile,
            r"(?m)^\s*COPY\s+db\.py\s+",
            "Do not revert to a Python file allowlist",
        )

    def test_runtime_import_graph_is_first_party(self):
        modules = runtime_modules()
        self.assertIn("main", modules)
        self.assertIn("services", modules)
        self.assertIn("routes", modules)
        self.assertTrue((ROOT / "services" / "__init__.py").is_file())

    def test_runtime_paths_are_not_dockerignored(self):
        patterns = dockerignore_patterns()
        missing: list[str] = []
        for rel in REQUIRED_PATHS:
            if is_ignored(rel, patterns):
                missing.append(rel)
        for name in (*runtime_modules(), *root_packages()):
            rel = name if (ROOT / name).is_dir() else f"{name}.py"
            if is_ignored(rel, patterns):
                missing.append(rel)
        self.assertEqual(missing, [], f"runtime paths excluded by .dockerignore: {missing}")


if __name__ == "__main__":
    unittest.main()
