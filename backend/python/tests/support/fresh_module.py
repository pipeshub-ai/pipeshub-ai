"""Build a module again, as ``importlib.reload`` would, without touching the module every other test shares."""

import importlib.util
from types import ModuleType


def fresh_copy(module: ModuleType) -> ModuleType:
    """Run ``module``'s source file into a new module object that ``sys.modules`` never holds.

    For testing an import-time choice (such as a backend picked from an env var): ``importlib.reload`` rebinds the
    shared module's globals for every later test in the process, and no ``sys.modules`` check can see it.
    Works on a plain file-backed module, not a package (the copy gets no ``__path__``).
    """
    spec = importlib.util.spec_from_file_location(module.__name__, module.__file__)
    copy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(copy)
    return copy
