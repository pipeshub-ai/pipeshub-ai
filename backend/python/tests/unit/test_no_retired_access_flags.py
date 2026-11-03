"""The retired access flags must not reappear.

``is_strict`` and ``has_restriction`` collapsed into a single ``access_rule``
field (``OPEN``/``STRICT``/``RESTRICTED``). The risk this guards is specific and
silent: a connector site left unconverted still parses, still passes every
existing test, and simply writes the model default — quietly downgrading a page
from RESTRICTED to OPEN and exposing it to everyone who can see its space. The
absence of a flag is indistinguishable from a node that never carried one, so
nothing else catches it.

The names also lived inside Cypher and AQL f-strings, where a reappearance
raises nothing at import or type-check time and surfaces only as a query
matching no node — the same failure mode ``test_no_legacy_edge_names.py``
exists to prevent for the pre-rename edge names.
"""

import pathlib

import pytest

RETIRED_NAMES = ("is_strict", "has_restriction", "isStrict", "hasRestriction")

# backend/python
_ROOT = pathlib.Path(__file__).resolve().parents[2]

_EXEMPT = {
    # The one module that must name what it replaced: AccessRule's docstring
    # explains the mapping for anyone reading pre-collapse code or data.
    _ROOT / "app" / "config" / "constants" / "arangodb.py",
    # This file carries the names as literals.
    pathlib.Path(__file__).resolve(),
}


def _python_sources() -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for top in ("app", "tests"):
        for path in (_ROOT / top).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if path.resolve() in _EXEMPT:
                continue
            found.append(path)
    return found


@pytest.mark.parametrize("name", RETIRED_NAMES)
def test_retired_access_flag_does_not_reappear(name: str) -> None:
    offenders: list[str] = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        if name not in text:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if name in line:
                offenders.append(
                    f"  {path.relative_to(_ROOT).as_posix()}:{lineno}: {line.strip()}"
                )

    assert not offenders, (
        f"{name!r} is retired — use access_rule / AccessRule "
        f"(OPEN, STRICT, RESTRICTED) instead:\n" + "\n".join(offenders)
    )


def test_the_guard_actually_scans_something() -> None:
    """Guards the guard: an empty file list would make every case above pass."""
    sources = _python_sources()
    assert len(sources) > 100, f"expected the whole tree, found {len(sources)} files"
    assert any(p.name == "entities.py" for p in sources)
