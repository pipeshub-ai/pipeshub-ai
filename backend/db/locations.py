"""Where revision files live: every module directory under versions/, plus the extra locations."""
import importlib.util
import os
from pathlib import Path
from types import ModuleType

DB_DIR = Path(__file__).resolve().parent
BACKEND_DIR = DB_DIR.parent


def _edition_module() -> ModuleType | None:
    """The edition file loaded by path (``PIPESHUB_EDITION_FILE`` overrides it), or ``None`` when it does not exist."""
    edition = Path(os.environ.get("PIPESHUB_EDITION_FILE", BACKEND_DIR / "python" / "app" / "edition.py"))
    if not edition.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_pipeshub_edition", edition)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extra_locations() -> list[str]:
    """``EXTRA_MIGRATION_LOCATIONS`` from ``app/edition.py``, read by file path so the app package
    (and its imports) is never loaded. Entries are module directories relative to ``backend/``
    (a leading ``backend/`` is accepted)."""
    module = _edition_module()
    found = []
    for loc in getattr(module, "EXTRA_MIGRATION_LOCATIONS", []):
        path = BACKEND_DIR / loc.removeprefix("backend/")
        if path.is_dir():
            found.append(str(path))
    return found


def extra_vetted_schemas() -> list[str]:
    """``EXTRA_VETTED_SCHEMAS`` from ``app/edition.py``: schemas of extra migration modules that own SECURITY DEFINER functions.
    The role bootstrap creates ``pipeshub_vetted_<schema>`` for each, so a module listed in ``EXTRA_MIGRATION_LOCATIONS`` migrates
    after the ordinary bootstrap with no extra flag. Empty when the edition file is missing or does not define it."""
    found = getattr(_edition_module(), "EXTRA_VETTED_SCHEMAS", [])
    if not isinstance(found, (list, tuple)):
        raise TypeError(f"EXTRA_VETTED_SCHEMAS must be a list of schema names, not {type(found).__name__}")
    return list(found)


def version_locations(versions: Path = DB_DIR / "versions") -> list[str]:
    core = sorted(str(p) for p in versions.iterdir() if p.is_dir() and not p.name.startswith("__"))
    return core + extra_locations()
