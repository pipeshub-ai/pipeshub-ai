"""The tools image's description of itself, for the engine's system prompt (E0b).

    runtime_note.py LOCK [NODE_LOCK]            print RUNTIME.md for the packages installed from LOCK
                                                (and the Node packages installed from NODE_LOCK)
    runtime_note.py --check LOCK [NODE_LOCK]    exit 1 unless the installed distributions are exactly LOCK's
                                                and every top-level package imports; with NODE_LOCK, also
                                                unless the Node packages are exactly its tree and each
                                                top-level one loads with `import` from any folder

opencode's launch config names /usr/share/pxh-engine/RUNTIME.md under ``instructions``; the note is
generated from what is installed, so the model is told exactly what the image ships.
"""

from __future__ import annotations

import importlib
import importlib.metadata as metadata
import json
import platform
import re
import subprocess
import sys
from pathlib import Path

_PIN = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[^\s\\]+)")
_DEPENDENCIES_HEADER = "# Dependencies"
# npm's global prefix. NODE_LOCK's keys are paths under it (`node_modules/a/node_modules/b`).
NODE_PREFIX = Path("/usr/local/lib")


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_lock(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """``(top_level, dependencies)``: name -> version, split at the lock's dependencies header."""
    top: dict[str, str] = {}
    deps: dict[str, str] = {}
    target = top
    for line in path.read_text().splitlines():
        if line.startswith(_DEPENDENCIES_HEADER):
            target = deps
        match = _PIN.match(line)
        if match:
            target[_canonical(match["name"])] = match["version"]
    return top, deps


def import_names() -> dict[str, list[str]]:
    names: dict[str, list[str]] = {}
    for module, dists in metadata.packages_distributions().items():
        if module.startswith("_") or not module.isidentifier():
            continue
        for dist in dists:
            names.setdefault(_canonical(dist), []).append(module)
    return {dist: sorted(set(modules)) for dist, modules in names.items()}


def read_node_lock(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """``(top_level, tree)``: top-level name -> version, and every lock path -> version."""
    packages = json.loads(path.read_text())["packages"]
    tree = {key: entry["version"] for key, entry in packages.items() if key}
    top = {name: tree[f"node_modules/{name}"] for name in packages[""].get("dependencies", {})}
    return top, tree


def installed_node_tree(prefix: Path = NODE_PREFIX) -> dict[str, str]:
    """Every package folder under ``prefix/node_modules``, nested ones included, -> its version."""
    found: dict[str, str] = {}

    def walk(modules: Path) -> None:
        if not modules.is_dir():
            return
        for child in sorted(modules.iterdir()):
            if child.name.startswith("."):
                continue
            folders = sorted(child.iterdir()) if child.name.startswith("@") else [child]
            for folder in folders:
                manifest = folder / "package.json"
                if manifest.is_file():
                    found[folder.relative_to(prefix).as_posix()] = json.loads(manifest.read_text())["version"]
                    walk(folder / "node_modules")

    walk(prefix / "node_modules")
    return found


def _node_version() -> str:
    return subprocess.run(["node", "--version"], capture_output=True, text=True, check=True).stdout.strip().lstrip("v")


def _render_node(node_lock: Path) -> str:
    top, _tree = read_node_lock(node_lock)
    rows = "\n".join(f"| `{name}` | {version} |" for name, version in sorted(top.items()))
    return f"""
Node.js {_node_version()} is installed as `node` (no `npm` or `npx`), with these packages, which `import` and
`require` find from any folder:

| Package | Version |
|---|---|
{rows}

- Node runs TypeScript directly (`node x.ts`, type annotations only), so a skill's `.ts` example runs as written.
- Office files from Node: `docx` writes `.docx`, `exceljs` reads and writes `.xlsx`, `pptxgenjs` writes `.pptx`.
- PDFs from Node: `pdfkit` writes new ones, `pdf-lib` fills forms and merges or splits existing ones.
"""


def render(lock: Path, node_lock: Path | None = None) -> str:
    top, deps = read_lock(lock)
    modules = import_names()
    rows = "\n".join(
        f"| {', '.join(f'`{m}`' for m in modules.get(name, [name]))} | {name} | {version} |"
        for name, version in sorted(top.items())
    )
    also = ", ".join(f"{name} {version}" for name, version in sorted(deps.items()))
    return f"""# Sandbox runtime

This sandbox has Python {platform.python_version()} as `python3`, with these packages preinstalled:

| Import as | Package | Version |
|---|---|---|
{rows}

Also installed, as their dependencies: {also}.

- There is no network access and no package installer in this sandbox: do not run `pip install`,
  `npm install` or any download. Use only the packages listed here and the standard libraries.
- Charts: matplotlib draws without a display; save figures as PNG files with `savefig`.
- Office files: python-docx writes `.docx`, python-pptx writes `.pptx`, openpyxl or XlsxWriter write `.xlsx`.
- PDFs: reportlab writes them (platypus tables paginate), pypdf merges, splits and fills forms, pdfplumber
  extracts text and tables.
""" + (_render_node(node_lock) if node_lock is not None else "")


def check_node(node_lock: Path, prefix: Path = NODE_PREFIX) -> list[str]:
    top, tree = read_node_lock(node_lock)
    installed = installed_node_tree(prefix)
    problems = [f"{key}: installed {installed.get(key)}, lock {v}" for key, v in tree.items() if installed.get(key) != v]
    problems += [f"{key}@{v} is not in the lock" for key, v in installed.items() if key not in tree]
    # An ES module ignores NODE_PATH: it finds a bare specifier only in a node_modules folder above it, so the
    # check imports from a folder outside the tree, as an engine's script in /workspace or /tmp does.
    for name in sorted(top):
        try:
            loaded = subprocess.run(
                ["node", "--input-type=module", "-e", f"await import({json.dumps(name)})"],
                capture_output=True, text=True, cwd="/tmp", check=False,
            )
        except FileNotFoundError:
            return [*problems, "node is not installed"]
        if loaded.returncode != 0:
            problems.append(f"import {name}: {loaded.stderr.strip()[-300:]}")
    return problems


def check(lock: Path) -> list[str]:
    top, deps = read_lock(lock)
    wanted = {**top, **deps}
    installed = {_canonical(d.metadata["Name"]): d.version for d in metadata.distributions()}
    problems = [f"{n}: installed {installed.get(n)}, lock {v}" for n, v in wanted.items() if installed.get(n) != v]
    problems += [f"{n}=={v} is not in the lock" for n, v in installed.items() if n not in wanted]
    modules = import_names()
    for name in top:
        for module in modules.get(name, [name]):
            try:
                importlib.import_module(module)
            except Exception as exc:
                problems.append(f"import {module} ({name}): {type(exc).__name__}: {exc}")
    return problems


def main(argv: list[str]) -> int:
    if argv[:1] == ["--check"]:
        problems = check(Path(argv[1]))
        if len(argv) > 2:
            problems += check_node(Path(argv[2]))
        for problem in problems:
            sys.stderr.write(problem + "\n")
        return 1 if problems else 0
    sys.stdout.write(render(Path(argv[0]), Path(argv[1]) if len(argv) > 1 else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
