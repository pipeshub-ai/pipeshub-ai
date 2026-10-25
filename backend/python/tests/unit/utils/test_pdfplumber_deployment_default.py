"""Every deployment that passes ENABLE_PDFPLUMBER_PROCESSOR defaults it to on.

The code reads unset as on (`pdfplumber_enabled`), but Compose files pass the
variable explicitly; one left at `${ENABLE_PDFPLUMBER_PROCESSOR:-false}` would
quietly keep that deployment on Docling.
"""
import pathlib
import re

import pytest

from app.utils.env_utils import _OFF_VALUES, PDFPLUMBER_PROCESSOR_ENV_VAR

ROOT = pathlib.Path(__file__).resolve().parents[5]
_ALL_FILES = [
    p
    for pattern in ("deployment/docker-compose/**/*.yml", "deployment/helm/**/*.yaml")
    for p in ROOT.glob(pattern)
]
# Only files that pass the variable are checked.
DEPLOYMENT_FILES = sorted(p for p in _ALL_FILES if PDFPLUMBER_PROCESSOR_ENV_VAR in p.read_text())
_ASSIGNMENT = re.compile(rf"{PDFPLUMBER_PROCESSOR_ENV_VAR}\s*[=:]\s*[\"']?([^\s\"'#]*)")
_COMPOSE_DEFAULT = re.compile(rf"^\$\{{{PDFPLUMBER_PROCESSOR_ENV_VAR}:?-([^}}]*)\}}$")


def _default_of(value: str) -> str:
    """The value a deployment gets when the operator sets nothing."""
    match = _COMPOSE_DEFAULT.match(value)
    return (match.group(1) if match else value).strip().lower()


def test_the_compose_files_are_found() -> None:
    assert any(p.name == "docker-compose.yml" for p in DEPLOYMENT_FILES)
    assert len(_ALL_FILES) > len(DEPLOYMENT_FILES)


@pytest.mark.parametrize("path", DEPLOYMENT_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_deployment_defaults_pdfplumber_off(path: pathlib.Path) -> None:
    defaults = [_default_of(v) for v in _ASSIGNMENT.findall(path.read_text())]
    assert [d for d in defaults if d in _OFF_VALUES] == []
