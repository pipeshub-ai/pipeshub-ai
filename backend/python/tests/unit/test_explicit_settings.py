"""What counts as an explicit pool-size setting (T34, T35).

The shipped defaults must never be mistaken for a choice: every compose file
passes ``LOCAL_DOCLING_PARSE_WORKERS=${...:-1}`` through and the installer
writes ``=1`` into ``.env``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.resource_governor.lanes import docling_auto, explicit_setting

POOL = "PARSE_POOL_WORKERS"
RASTER = "PDF_RASTER_WORKERS"
DOCLING = "LOCAL_DOCLING_PARSE_WORKERS"

REPO = Path(__file__).resolve().parents[4]
COMPOSE = REPO / "deployment" / "docker-compose"


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        (POOL, None, None), (POOL, "", None), (POOL, "   ", None), (POOL, "abc", None),
        (POOL, "2.5", None), (POOL, "-1", None), (POOL, "0", 0), (POOL, "1", 1),
        (POOL, " 3 ", 3), (POOL, "8", 8),
        (RASTER, None, None), (RASTER, "", None), (RASTER, "x", None), (RASTER, "0", None),
        (RASTER, "1", 1), (RASTER, "4", 4),
        (DOCLING, None, None), (DOCLING, "", None), (DOCLING, "1", None), (DOCLING, "x", None),
        (DOCLING, "0", None), (DOCLING, "auto", None), (DOCLING, "2", 2), (DOCLING, "4", 4),
    ],
)
def test_classification_table(name: str, value: str | None, expected: int | None) -> None:
    environ = {} if value is None else {name: value}
    assert explicit_setting(name, environ) == expected


def test_docling_auto_is_its_own_mode() -> None:
    assert docling_auto({DOCLING: "auto"})
    assert docling_auto({DOCLING: " AUTO "})
    assert not docling_auto({DOCLING: "1"})
    assert not docling_auto({})


def test_unknown_setting_is_refused() -> None:
    with pytest.raises(ValueError, match="not a pool size"):
        explicit_setting("SOMETHING_ELSE", {})


def test_the_environment_is_the_default_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DOCLING, "3")
    monkeypatch.delenv(POOL, raising=False)
    assert explicit_setting(DOCLING) == 3
    assert explicit_setting(POOL) is None


_DEFAULT = re.compile(r"LOCAL_DOCLING_PARSE_WORKERS=\$\{LOCAL_DOCLING_PARSE_WORKERS:-([^}]*)\}")


@pytest.mark.skipif(not COMPOSE.is_dir(), reason="deployment files are not in this checkout")
def test_shipped_compose_defaults() -> None:
    defaults: dict[Path, str] = {}
    for path in COMPOSE.rglob("*.yml"):
        for match in _DEFAULT.finditer(path.read_text()):
            defaults[path] = match.group(1)
    assert len(defaults) >= 6, "expected the compose files to pass the setting through"
    hot_reload = {p: v for p, v in defaults.items() if "hot-reload" in p.parts}
    shipped = {p: v for p, v in defaults.items() if "hot-reload" not in p.parts}
    assert hot_reload, "the hot-reload files default to a pool"
    for path, value in shipped.items():
        assert explicit_setting(DOCLING, {DOCLING: value}) is None, path
    for path, value in hot_reload.items():
        assert explicit_setting(DOCLING, {DOCLING: value}) == 4, path


@pytest.mark.skipif(not COMPOSE.is_dir(), reason="deployment files are not in this checkout")
def test_shipped_env_files_and_installer_write_the_default() -> None:
    for name in ("install.sh", "env.template"):
        text = (COMPOSE / name).read_text()
        values = re.findall(r"^LOCAL_DOCLING_PARSE_WORKERS=(.*)$", text, re.MULTILINE)
        assert values, name
        for value in values:
            assert explicit_setting(DOCLING, {DOCLING: value.strip()}) is None, name


@pytest.mark.skipif(not COMPOSE.is_dir(), reason="deployment files are not in this checkout")
def test_shipped_parse_pool_workers_passes_empty() -> None:
    compose = (COMPOSE / "docker-compose.yml").read_text()
    (default,) = re.findall(r"PARSE_POOL_WORKERS=\$\{PARSE_POOL_WORKERS:-([^}]*)\}", compose)
    assert default == ""
    assert explicit_setting(POOL, {POOL: default}) is None
    template = (COMPOSE / "env.template").read_text()
    (value,) = re.findall(r"^PARSE_POOL_WORKERS=(.*)$", template, re.MULTILINE)
    assert explicit_setting(POOL, {POOL: value}) is None
