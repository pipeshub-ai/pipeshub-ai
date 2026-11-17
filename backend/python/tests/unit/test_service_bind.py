"""Every service's run() listens where PIPESHUB_BIND_HOST / PIPESHUB_BIND_PORT say, and on its usual address otherwise.

A container keeps binding every interface on the service's fixed port; a process started outside a container (no network of its
own) can be told to listen on loopback, at a port its supervisor picked.
"""
import importlib
import inspect
from unittest.mock import patch

import pytest

# (module, port when nothing is set)
SERVICES = [
    ("app.connectors_main", 8088),
    ("app.indexing_main", 8091),
    ("app.query_main", 8000),
    ("app.embedding_main", 8002),
    ("app.docling_main", 8081),
    ("app.parsing_main", 8092),
    ("app.extraction_main", 8093),
]
PORT_VARS = ("EMBEDDING_SERVER_PORT", "PARSING_SERVICE_PORT", "EXTRACTION_SERVICE_PORT")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch) -> None:
    for var in ("PIPESHUB_BIND_HOST", "PIPESHUB_BIND_PORT", *PORT_VARS):
        monkeypatch.delenv(var, raising=False)
    # query_main publishes its worker count into the environment; keep it at one so run() does not exec uvicorn
    monkeypatch.setenv("QUERY_UVICORN_WORKERS", "1")
    monkeypatch.setenv("INDEXING_UVICORN_WORKERS", "1")


def _bound(module_name: str) -> tuple[str, int]:
    module = importlib.import_module(module_name)
    with patch.object(module.uvicorn, "run") as uvicorn_run:
        module.run(reload=False)
    kwargs = uvicorn_run.call_args.kwargs
    return kwargs["host"], kwargs["port"]


@pytest.mark.parametrize(("module_name", "port"), SERVICES)
def test_defaults_are_every_interface_on_the_fixed_port(module_name, port) -> None:
    assert _bound(module_name) == ("0.0.0.0", port)


@pytest.mark.parametrize(("module_name", "port"), SERVICES)
def test_bind_host_and_port_from_the_environment(monkeypatch, module_name, port) -> None:
    monkeypatch.setenv("PIPESHUB_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("PIPESHUB_BIND_PORT", "45678")
    assert _bound(module_name) == ("127.0.0.1", 45678)


@pytest.mark.parametrize(("module_name", "port"), SERVICES)
def test_a_blank_or_malformed_value_keeps_the_default(monkeypatch, module_name, port) -> None:
    monkeypatch.setenv("PIPESHUB_BIND_HOST", "  ")
    monkeypatch.setenv("PIPESHUB_BIND_PORT", "not-a-port")
    assert _bound(module_name) == ("0.0.0.0", port)


@pytest.mark.parametrize("var", PORT_VARS)
def test_bind_port_wins_over_a_service_port_variable(monkeypatch, var) -> None:
    module_name = {"EMBEDDING_SERVER_PORT": "app.embedding_main", "PARSING_SERVICE_PORT": "app.parsing_main",
                   "EXTRACTION_SERVICE_PORT": "app.extraction_main"}[var]
    monkeypatch.setenv(var, "9100")
    assert _bound(module_name)[1] == 9100
    monkeypatch.setenv("PIPESHUB_BIND_PORT", "9200")
    assert _bound(module_name)[1] == 9200


def test_no_service_hardcodes_its_host() -> None:
    for module_name, _ in SERVICES:
        source = inspect.getsource(importlib.import_module(module_name))
        assert 'host="0.0.0.0"' not in source, module_name


@pytest.mark.parametrize(("module_name", "port"), SERVICES)
def test_an_explicit_port_zero_is_kept(monkeypatch, module_name, port) -> None:
    # uvicorn binds port 0 to a free port; 0 is a choice, not "unset"
    monkeypatch.setenv("PIPESHUB_BIND_PORT", "45678")
    module = importlib.import_module(module_name)
    with patch.object(module.uvicorn, "run") as uvicorn_run:
        module.run(port=0, reload=False)
    assert uvicorn_run.call_args.kwargs["port"] == 0


@pytest.mark.parametrize("var", PORT_VARS)
def test_a_blank_service_port_variable_never_stops_the_service(monkeypatch, var) -> None:
    module_name = {"EMBEDDING_SERVER_PORT": "app.embedding_main", "PARSING_SERVICE_PORT": "app.parsing_main",
                   "EXTRACTION_SERVICE_PORT": "app.extraction_main"}[var]
    default = dict(SERVICES)[module_name]
    for value in ("", "not-a-port"):
        monkeypatch.setenv(var, value)
        monkeypatch.setenv("PIPESHUB_BIND_PORT", "9300")
        assert _bound(module_name)[1] == 9300
        monkeypatch.delenv("PIPESHUB_BIND_PORT")
        assert _bound(module_name)[1] == default
