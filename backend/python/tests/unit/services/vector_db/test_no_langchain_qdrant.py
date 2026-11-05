"""Guard: production modules must not import langchain_qdrant."""

import importlib
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

_RELOADED = (
    "app.modules.retrieval.retrieval_service",
    "app.modules.transformers.vectorstore",
    "app.modules.indexing.run",
)
_MISSING = object()


def test_no_langchain_qdrant_imports_in_vector_modules():
    # Importing the modules again replaces their entries in sys.modules and the attribute their package keeps, and may load the package
    # itself; a later test that imported one of them at collection would hold the old module while the rest of the process finds the
    # new one. The import state is put back exactly as it was found: entries and attributes that existed, and none that did not.
    state = []
    for name in _RELOADED:
        parent_name, _, child = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        state.append((
            name, sys.modules.get(name, _MISSING), parent_name, parent,
            getattr(parent, child, _MISSING) if parent is not None else _MISSING,
        ))
    had_qdrant = sys.modules.get("langchain_qdrant", _MISSING)
    sys.modules["langchain_qdrant"] = MagicMock()
    try:
        for module_name in _RELOADED:
            sys.modules.pop(module_name, None)
            mod = importlib.import_module(module_name)
            assert mod is not None

        # SparseEmbedder is the supported path
        from app.services.vector_db import sparse_embeddings

        assert sparse_embeddings.SparseEmbedder is not None
    finally:
        for name, entry, parent_name, parent, attribute in state:
            child = name.rpartition(".")[2]
            if entry is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = entry
            if parent is None:
                sys.modules.pop(parent_name, None)  # the package was loaded by the guard
            elif attribute is _MISSING:
                if hasattr(parent, child):
                    delattr(parent, child)
            else:
                setattr(parent, child, attribute)
        if had_qdrant is _MISSING:
            sys.modules.pop("langchain_qdrant", None)
        else:
            sys.modules["langchain_qdrant"] = had_qdrant


_FRESH = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {tests!r})
    from test_no_langchain_qdrant import _RELOADED, test_no_langchain_qdrant_imports_in_vector_modules as guard

    parents = {{name.rpartition(".")[0] for name in _RELOADED}}
    assert not ({{*_RELOADED, *parents}} & sys.modules.keys()), "the modules are already loaded"
    guard()
    left = sorted({{*_RELOADED, *parents}} & sys.modules.keys())
    assert not left, f"the guard left {{left}} in sys.modules"
    """
)


def test_the_guard_leaves_modules_that_were_not_loaded_unloaded() -> None:
    """In a process where none of the three modules or their packages were loaded, the guard leaves none of them behind: nothing in
    sys.modules for a later import to find, and no attribute on a package that stays."""
    here = str(Path(__file__).parent)
    run = subprocess.run(
        [sys.executable, "-c", _FRESH.format(tests=here)], capture_output=True, text=True, cwd=Path(__file__).parents[4], check=False,
    )
    assert run.returncode == 0, run.stderr[-1500:]


def test_the_guard_puts_back_the_modules_it_imports_again() -> None:
    """A test that imported one of them at collection must still be talking to the module the rest of the process finds."""
    importlib.import_module("app.modules.retrieval.retrieval_service")
    before = {name: sys.modules[name] for name in _RELOADED if name in sys.modules}
    test_no_langchain_qdrant_imports_in_vector_modules()
    assert {name: sys.modules[name] for name in before} == before
    parent = sys.modules["app.modules.retrieval"]
    assert parent.retrieval_service is before["app.modules.retrieval.retrieval_service"]
