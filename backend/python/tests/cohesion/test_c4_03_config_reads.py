"""C4.03 (AGENTS.md "Conventions"): harness and control-plane code reads configuration through ConfigurationService,
never the KeyValueStore.

An import of a KV store module (``app.config.providers.*``, ``app.config.key_value_store``,
``app.config.key_value_store_factory``) anywhere in the harness, the control plane or their process entry points is
refused, including an import inside a function. ``app.config`` is a namespace package, which import-linter's graph
does not see, so the scan reads the source. ``ALLOWED`` holds the modules that build the store they inject into a
``ConfigurationService`` and nothing else; Node's twin is C-16's FM-9.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Final

from tests.cohesion.support import PYTHON_ROOT, dotted, modules

if TYPE_CHECKING:
    from pathlib import Path

ROOTS: Final = ("app/harness", "app/control_plane", "harness_core", "harness_engines")
ENTRY_POINTS: Final = (
    "app/harness_main.py",
    "app/model_gateway_main.py",
    "app/sandbox_broker_main.py",
    "app/egress_proxy_main.py",
)
STORE_MODULES: Final = ("app.config.providers", "app.config.key_value_store", "app.config.key_value_store_factory")

#: module -> why it may import a store: each builds the store for a ConfigurationService and reads nothing itself.
ALLOWED: Final[dict[str, str]] = {
    "app.harness.assembly.services": "builds the worker's ConfigurationService over the encrypted store",
    "app.harness.credentials.migrate": "the D2 credential-binding migration's ConfigurationService",
    "app.harness.sandbox.egress_rekey": "the SECRET_KEY rotation's egress step (ADR-KEY-01): its ConfigurationService",
    "app.harness.tool_identity.migrate": "the D2 tool-identity migration's ConfigurationService",
}


def _is_store(name: str) -> bool:
    return any(name == module or name.startswith(f"{module}.") for module in STORE_MODULES)


def store_imports(source: str, package: str) -> list[str]:
    """The KV store modules ``source`` imports, wherever the import sits; ``package`` resolves relative imports."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if _is_store(alias.name))
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parent = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*parent, *([base] if base else [])])
            if _is_store(base):
                found.append(base)
            else:
                found.extend(f"{base}.{alias.name}" for alias in node.names if _is_store(f"{base}.{alias.name}"))
    return sorted(set(found))


def _sources() -> list[Path]:
    return [*modules(*ROOTS, include_testing=True), *(PYTHON_ROOT / path for path in ENTRY_POINTS)]


def test_c4_03_only_the_configuration_service_builders_import_a_kv_store() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _sources():
        name = dotted(path)
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        hits = store_imports(path.read_text(encoding="utf-8"), package)
        if hits:
            offenders[name] = hits
    assert set(offenders) - set(ALLOWED) == set(), offenders
    assert set(ALLOWED) <= set(offenders), "an ALLOWED module no longer imports a store: drop it from ALLOWED"


def test_c4_03_the_scan_sees_every_form_of_import() -> None:
    source = (
        "import app.config.key_value_store\n"
        "from app.config.providers.encrypted_store import EncryptedKeyValueStore\n"
        "from app.config import key_value_store_factory\n"
        "from app.config.configuration_service import ConfigurationService\n"
        "def build():\n"
        "    from app.config.providers.redis import redis_store\n"
    )
    assert store_imports(source, "app.harness.x") == [
        "app.config.key_value_store",
        "app.config.key_value_store_factory",
        "app.config.providers.encrypted_store",
        "app.config.providers.redis",
    ]
    assert store_imports("from ...config.providers import etcd\n", "app.harness.assembly") == ["app.config.providers"]


def test_c4_03_every_root_and_entry_point_exists() -> None:
    assert all((PYTHON_ROOT / root).is_dir() for root in ROOTS)
    assert all((PYTHON_ROOT / path).is_file() for path in ENTRY_POINTS)
