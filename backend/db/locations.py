"""Where revision files live: every module directory under versions/, plus the extra locations."""
import importlib.util
import os
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent
BACKEND_DIR = DB_DIR.parent


def extra_locations() -> list[str]:
    """``EXTRA_MIGRATION_LOCATIONS`` from ``app/edition.py``, read by file path so the app package
    (and its imports) is never loaded. Entries are module directories relative to ``backend/``
    (a leading ``backend/`` is accepted)."""
    edition = Path(os.environ.get("PIPESHUB_EDITION_FILE", BACKEND_DIR / "python" / "app" / "edition.py"))
    if not edition.is_file():
        return []
    spec = importlib.util.spec_from_file_location("_pipeshub_edition", edition)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    found = []
    for loc in getattr(module, "EXTRA_MIGRATION_LOCATIONS", []):
        path = BACKEND_DIR / loc.removeprefix("backend/")
        if path.is_dir():
            found.append(str(path))
    return found


def version_locations(versions: Path = DB_DIR / "versions") -> list[str]:
    core = sorted(str(p) for p in versions.iterdir() if p.is_dir() and not p.name.startswith("__"))
    return core + extra_locations()
