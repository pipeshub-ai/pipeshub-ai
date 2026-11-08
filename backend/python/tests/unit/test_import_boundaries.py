"""Import boundaries of the service entry modules.

The connector, query, indexing and docling services each run in their own process, and
each process pays for every library its entry module imports at load. Three of them once
imported the same parser stack, ``docling`` and ``torch`` (about 0.9 GiB each) although
none of the three needed it to start. These tests keep the heavy libraries out:

* ``test_static_*`` walks the module-level import closure of each entry module (no
  imports are executed) and fails with the chain that leads to a forbidden module.
* ``test_walker_*`` and ``test_control_*`` prove the walker finds what it should, including
  when a forbidden import is planted in a real module.
* ``test_runtime_*`` (marker ``import_runtime``) imports each entry module in a fresh
  interpreter and checks ``sys.modules``, which also sees what a library imports for
  itself. It is slow (an import takes 10 to 30 seconds).

What counts as module level: the body of a module and of its classes, ``if``/``try``/
``with`` blocks, but not function bodies, and not ``if TYPE_CHECKING`` blocks. A name
imported from a module that declares ``_LAZY_ATTRS`` (a name to module table read on
first attribute access, see ``app/edition_config.py``) is an edge to the module the table
names, so ``from app.edition_config import chatbot_router`` still counts as a chat import.
"""
from __future__ import annotations

import ast
import functools
import json
import subprocess
import sys
from collections import deque
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]

# The libraries whose import costs hundreds of MiB and tens of seconds. ``docling``
# brings ``torch`` and ``transformers`` with it.
HEAVY_LIBS = frozenset({"docling", "torch", "transformers", "sentence_transformers"})
# Also held out of every entry: it loads onnxruntime and a tokenizer, nothing at start-up needs it.
# The LLM provider packages (langchain_openai, langchain_anthropic, langchain_google_genai,
# langchain_aws, langchain_mistralai) are NOT forbidden: every service that makes a model
# call imports them through app/utils/streaming.py at start-up today, and they do not pull
# torch or transformers. Moving them behind first use is the SDK-lazy-loading follow-up.
HEAVY_LIBS = HEAVY_LIBS | {"fastembed"}

# entry module -> (libraries, app modules) that must not be reachable at import time.
BOUNDARIES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "app.connectors_main": (
        HEAVY_LIBS | {"cv2"},
        frozenset({"app.api.routes.chatbot", "app.events.processor"}),
    ),
    # The attachment path keeps pdfplumber, pypdfium2, numpy, PIL, onnxruntime and cv2.
    "app.query_main": (HEAVY_LIBS, frozenset({"app.events.processor"})),
    # ``docling_core`` (types only) is allowed; ``docling`` is not.
    "app.indexing_main": (HEAVY_LIBS, frozenset()),
    # The service imports ``docling`` when it starts (``ensure_docling``), not at module
    # load, and has no use for the parser set or the shared container helpers. It still
    # reaches ``langchain_openai`` and ``fastembed`` through the block builder's table
    # enrichment (about 50 MiB); that is left for the SDK-lazy-loading follow-up.
    "app.docling_main": (
        HEAVY_LIBS,
        frozenset({
            "app.containers.utils.utils",
            "app.api.routes.chatbot",
            "app.modules.parsers.pptx.pptx_parser",
            "app.modules.parsers.markdown.markdown_parser",
            "app.modules.parsers.html_parser.html_parser",
        }),
    ),
}


_parse = functools.lru_cache(maxsize=None)(ast.parse)  # the control tests re-walk the same tree


class Walker:
    """Module-level import closure over the source tree, by AST (nothing is imported)."""

    def __init__(self, root: Path = BACKEND, overrides: dict[str, str] | None = None) -> None:
        self.root = root
        self.overrides = overrides or {}
        self._edges: dict[str, list[str]] = {}
        self._lazy: dict[str, dict[str, str]] = {}

    def _path(self, mod: str) -> Path | None:
        base = self.root / mod.replace(".", "/")
        if base.with_suffix(".py").is_file():
            return base.with_suffix(".py")
        if (base / "__init__.py").is_file():
            return base / "__init__.py"
        return None

    def exists(self, mod: str) -> bool:
        return mod in self.overrides or self._path(mod) is not None

    def _source(self, mod: str) -> str | None:
        if mod in self.overrides:
            return self.overrides[mod]
        path = self._path(mod)
        return path.read_text(encoding="utf-8") if path else None

    def _tree(self, mod: str) -> ast.Module | None:
        src = self._source(mod)
        return _parse(src) if src is not None else None

    def lazy_table(self, mod: str) -> dict[str, str]:
        if mod not in self._lazy:
            table: dict[str, str] = {}
            tree = self._tree(mod)
            for node in (tree.body if tree else []):
                target = None
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    target, value = node.target.id, node.value
                elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    target, value = node.targets[0].id, node.value
                if target == "_LAZY_ATTRS" and isinstance(value, ast.Dict):
                    for k, v in zip(value.keys, value.values):
                        if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                            table[k.value] = v.value
            self._lazy[mod] = table
        return self._lazy[mod]

    @staticmethod
    def _is_type_checking(test: ast.expr) -> bool:
        return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )

    def edges(self, mod: str) -> list[str]:
        if mod not in self._edges:
            tree = self._tree(mod)
            out: list[str] = []
            if tree is not None:
                path = self._path(mod)
                is_pkg = path is not None and path.name == "__init__.py"
                self._collect(tree.body, mod, is_pkg, out)
            self._edges[mod] = out
        return self._edges[mod]

    def _collect(self, body: list[ast.stmt], mod: str, is_pkg: bool, out: list[str]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(node, ast.If) and self._is_type_checking(node.test):
                self._collect(node.orelse, mod, is_pkg, out)
                continue
            if isinstance(node, ast.Import):
                out.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    pkg = mod.split(".") if is_pkg else mod.split(".")[:-1]
                    pkg = pkg[: len(pkg) - (node.level - 1)]
                    base = ".".join(pkg + ([node.module] if node.module else []))
                out.append(base)
                for alias in node.names:
                    sub = f"{base}.{alias.name}"
                    if self.exists(sub):
                        out.append(sub)
                    lazy = self.lazy_table(base).get(alias.name)
                    if lazy:
                        out.append(lazy)
            else:
                for field in ("body", "orelse", "finalbody"):
                    inner = getattr(node, field, None)
                    if isinstance(inner, list) and inner and isinstance(inner[0], ast.stmt):
                        self._collect(inner, mod, is_pkg, out)
                for handler in getattr(node, "handlers", []):
                    self._collect(handler.body, mod, is_pkg, out)
                for case in getattr(node, "cases", []):
                    self._collect(case.body, mod, is_pkg, out)
                # importlib.import_module("literal") and __import__("literal") at module level
                if not isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While, ast.ClassDef)):
                    for call in ast.walk(node):
                        if isinstance(call, ast.Call) and call.args and isinstance(call.args[0], ast.Constant) \
                                and isinstance(call.args[0].value, str):
                            fn = call.func
                            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                            if name in ("import_module", "__import__"):
                                out.append(call.args[0].value)
                if isinstance(node, ast.ClassDef):
                    self._collect(node.body, mod, is_pkg, out)

    def reach(self, entry: str, libs: frozenset[str], modules: frozenset[str]) -> list[str] | None:
        """The first import chain from ``entry`` to a forbidden library or module, or None."""
        parent: dict[str, str | None] = {entry: None}
        queue = deque([entry])

        def chain(node: str) -> list[str]:
            path = []
            cur: str | None = node
            while cur is not None:
                path.append(cur)
                cur = parent[cur]
            return path[::-1]

        while queue:
            mod = queue.popleft()
            targets = list(self.edges(mod))
            # Importing a.b.c imports the packages a and a.b first.
            for t in list(targets):
                parts = t.split(".")
                targets.extend(".".join(parts[:i]) for i in range(1, len(parts)))
            for t in targets:
                if t in parent:
                    continue
                top = t.split(".")[0]
                if top in libs or any(t == m or t.startswith(m + ".") for m in modules):
                    parent[t] = mod
                    return chain(t)
                if not self.exists(t):
                    continue
                parent[t] = mod
                queue.append(t)
        return None


def _format(entry: str, path: list[str]) -> str:
    return f"{entry} reaches {path[-1]} at import time:\n  " + "\n  -> ".join(path)


# The first entry parses most of app/ (about 25 s on an idle 4-CPU host); the rest reuse the
# parse cache. The suite-wide 30 s limit failed it whenever the host was busy.
@pytest.mark.timeout(180)
@pytest.mark.parametrize("entry", sorted(BOUNDARIES))
def test_static_entry_module_keeps_heavy_imports_out(entry: str) -> None:
    libs, modules = BOUNDARIES[entry]
    path = Walker().reach(entry, libs, modules)
    assert path is None, _format(entry, path or [])


# ---------------------------------------------------------------------------
# The walker itself: what it must see, and what it must not flag.
# ---------------------------------------------------------------------------

def _tree(**sources: str) -> Walker:
    return Walker(root=Path("/nonexistent"), overrides={k.replace("__", "."): v for k, v in sources.items()})


@pytest.mark.parametrize(
    "source",
    [
        "import docling",
        "from docling.document_converter import DocumentConverter",
        "try:\n    import docling\nexcept ImportError:\n    docling = None",
        "class C:\n    import docling",
        "import importlib\nx = importlib.import_module('docling')",
        "if True:\n    import torch",
        "with open('x'):\n    from transformers import AutoModel",
    ],
)
def test_walker_finds_module_level_imports(source: str) -> None:
    w = _tree(app__entry=source)
    assert w.reach("app.entry", frozenset({"docling", "torch", "transformers"}), frozenset()) is not None


@pytest.mark.parametrize(
    "source",
    [
        "def f():\n    import docling",
        "async def f():\n    from docling.document_converter import DocumentConverter",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import docling",
        "import typing\nif typing.TYPE_CHECKING:\n    import docling",
        "class C:\n    def m(self):\n        import docling",
    ],
)
def test_walker_ignores_function_bodies_and_type_checking(source: str) -> None:
    w = _tree(app__entry=source)
    assert w.reach("app.entry", frozenset({"docling"}), frozenset()) is None


def test_walker_follows_the_chain_through_app_modules_and_reports_it() -> None:
    w = _tree(app__a="from app.b import x", app__b="import app.c", app__c="import docling.thing")
    assert w.reach("app.a", frozenset({"docling"}), frozenset()) == ["app.a", "app.b", "app.c", "docling.thing"]


def test_walker_reads_the_lazy_attribute_table() -> None:
    cfg = '_LAZY_ATTRS = {"chat_router": "app.routes.chat"}\n'
    w = _tree(app__cfg=cfg, app__routes__chat="import docling", app__a="from app.cfg import chat_router")
    # a name in the table is an edge to the module it names ...
    assert w.reach("app.a", frozenset({"docling"}), frozenset()) is not None
    # ... and importing the table's module alone is not
    w = _tree(app__cfg=cfg, app__routes__chat="import docling", app__b="import app.cfg")
    assert w.reach("app.b", frozenset({"docling"}), frozenset()) is None


def test_walker_flags_a_forbidden_app_module_and_its_submodules() -> None:
    w = _tree(app__a="from app.events import processor", app__events__processor="x = 1", app__events__init="")
    assert w.reach("app.a", frozenset(), frozenset({"app.events.processor"})) is not None


def test_walker_resolves_relative_imports() -> None:
    w = _tree(app__pkg__a="from . import b", app__pkg__b="import docling")
    assert w.reach("app.pkg.a", frozenset({"docling"}), frozenset()) is not None


# ---------------------------------------------------------------------------
# Control: plant a forbidden import in a real module and require the ratchet to fail.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("entry", "planted_module", "planted_line"),
    [
        ("app.connectors_main", "app.containers.utils.utils", "from app.events.processor import Processor"),
        ("app.connectors_main", "app.edition_config", "from app.api.routes.chatbot import router as chatbot_router"),
        ("app.query_main", "app.api.routes.chatbot", "from app.events.processor import Processor"),
        ("app.indexing_main", "app.utils.converters.docling_doc_to_blocks", "from docling.datamodel.document import DoclingDocument"),
        ("app.docling_main", "app.containers.docling", "from app.containers.utils.utils import ContainerUtils"),
        ("app.connectors_main", "app.containers.utils.utils", "import fastembed"),
        ("app.query_main", "app.containers.utils.utils", "import fastembed"),
        ("app.indexing_main", "app.containers.utils.utils", "import fastembed"),
        ("app.docling_main", "app.docling_main", "import fastembed"),
    ],
)
def test_control_a_planted_import_fails_the_ratchet(entry: str, planted_module: str, planted_line: str) -> None:
    clean = Walker()
    libs, modules = BOUNDARIES[entry]
    assert clean.reach(entry, libs, modules) is None
    source = clean._source(planted_module)
    assert source is not None
    planted = Walker(overrides={planted_module: planted_line + "\n" + source})
    path = planted.reach(entry, libs, modules)
    assert path is not None and planted_module in path, "the ratchet missed a planted import"


# ---------------------------------------------------------------------------
# Runtime: a fresh interpreter, so what a third-party library imports for itself shows.
# ---------------------------------------------------------------------------

_PROBE = """
import importlib, json, sys
importlib.import_module(sys.argv[1])
print("LOADED " + json.dumps(sorted({m.split(".")[0] for m in sys.modules} | {m for m in sys.modules if m.startswith("app.")})))
"""

_RUNTIME_FORBIDDEN_MODULES = {
    "app.connectors_main": {"app.api.routes.chatbot", "app.events.processor"},
    "app.query_main": {"app.events.processor"},
    "app.indexing_main": set(),
    "app.docling_main": {"app.containers.utils.utils"},
}


@pytest.mark.slow
@pytest.mark.import_runtime
@pytest.mark.timeout(300)
@pytest.mark.parametrize("entry", sorted(BOUNDARIES))
def test_runtime_entry_module_does_not_load_heavy_libraries(entry: str) -> None:
    libs, _ = BOUNDARIES[entry]
    # The docling service loads docling when its lifespan starts, not when the module loads.
    proc = subprocess.run(
        [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r)\n%s" % (str(BACKEND), _PROBE), entry],
        cwd=BACKEND, capture_output=True, text=True, timeout=280,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("LOADED "))
    loaded = set(json.loads(line[len("LOADED "):]))
    forbidden = (loaded & libs) | (loaded & _RUNTIME_FORBIDDEN_MODULES[entry])
    assert not forbidden, f"{entry} loaded {sorted(forbidden)} at import time"
