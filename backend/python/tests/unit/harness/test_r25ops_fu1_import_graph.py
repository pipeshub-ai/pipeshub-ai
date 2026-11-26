"""R25OPS-FU-1: what the idle harness worker and the sandbox broker load, guarded by their import graphs.

The broker needed one constant from ``harness_core.gateway`` and the worker credential from
``app.harness.journal_node``; their packages' eager re-exports loaded the whole tool gateway, the journal client and,
through it, FastAPI and the durable kernel (1,415 modules, 117 MiB in the image; 891 after ``lazy_exports``). The
worker imports the agent stack by design before its first claim (C-17), but none of the machine-learning or other
services' stacks: a new import that pulls one in shows here, not in the next footprint run.

Each import runs in a fresh interpreter, so what another test imported does not hide or add a module.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

# Never in either process at idle: other services' model and parsing stacks.
HEAVY = (
    "torch",
    "transformers",
    "sentence_transformers",
    "docling",
    "litellm",
    "spacy",
    "sklearn",
    "playwright",
    "fastembed",
    "onnxruntime",
    "qdrant_client",
    "aiokafka",
)

# What the broker held only through eager package re-exports (each present in the r25ops image's graph).
BROKER_REMOVED = (
    "fastapi",
    "starlette",
    "httpx",
    "app.harness.journal_node.client",
    "app.services.model_identity",
    "app.services.resource_governor",
    "app.agent_loop_lib.sandbox.coding.e2b",
    "app.agent_loop_lib.sandbox.coding.reflection",
    "harness_core.gateway.endpoint",
    "harness_core.kernel.hooks",
    "harness_core.journal.memory",
    "harness_core.kernel.durable.driver",
    "harness_core.effects.memory",
)

# Never in the broker: it starts containers, it reads no graph, document or model.
BROKER_NEVER = ("pandas", "numpy", "neo4j", "openai", "anthropic", "langchain_core", "app.modules", "app.agents")

_PROBE = """
import importlib, json, sys
for name in sys.argv[1:]:
    importlib.import_module(name)
print(json.dumps(sorted(sys.modules)))
"""


def _loaded(*modules: str) -> set[str]:
    out = subprocess.run(
        [sys.executable, "-c", _PROBE, *modules],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=25,
        check=True,
    )
    return set(json.loads(out.stdout.strip().splitlines()[-1]))


def _found(loaded: set[str], names: tuple[str, ...]) -> list[str]:
    return sorted(n for n in names if any(m == n or m.startswith(f"{n}.") for m in loaded))


def test_fu1_ig1_the_broker_loads_neither_the_gateway_nor_the_journal_client_nor_any_heavy_stack() -> None:
    loaded = _loaded("app.sandbox_broker_main")
    assert _found(loaded, HEAVY + BROKER_NEVER + BROKER_REMOVED) == []
    # What it serves with is there.
    assert {"app.harness.sandbox.docker_broker", "app.harness.journal_node.credentials", "aiohttp.web"} <= loaded


@pytest.mark.timeout(60)
def test_fu1_ig2_the_worker_and_its_warm_up_load_no_other_services_stacks() -> None:
    from app.harness.worker.warmup import ASSEMBLY_MODULES

    loaded = _loaded("app.harness_main", *ASSEMBLY_MODULES)
    assert _found(loaded, HEAVY) == []


def test_fu1_ig4_the_workers_module_loads_no_graph_driver_until_its_warm_up_names_the_store() -> None:
    """``app.edition_services`` re-exported Neo4jProvider eagerly: the worker's module loaded the Neo4j driver, and
    with it pandas and numpy, on every deployment (80 MiB of its 270 at import). The warm-up imports the provider
    ``DATA_STORE`` names, so a Neo4j deployment still pays it before the first claim (C-17), never on a run's loop."""
    from app.harness.worker.warmup import GRAPH_STORE_MODULES, graph_store_modules

    assert _found(_loaded("app.harness_main"), ("neo4j", "pandas", "numpy", "app.services.graph_db.neo4j")) == []
    assert graph_store_modules({"DATA_STORE": "neo4j"}) == ("app.services.graph_db.neo4j.neo4j_provider",)
    assert graph_store_modules({"DATA_STORE": " Postgres "}) == GRAPH_STORE_MODULES["postgres"]
    assert graph_store_modules({"DATA_STORE": "arangodb"}) == graph_store_modules({}) == ()


@pytest.mark.timeout(60)
def test_fu1_ig5_the_edition_services_still_resolve_lazily() -> None:
    import app.edition_services as edition

    for name in ("Neo4jProvider", "TokenRefreshService", "ToolsetTokenRefreshService", "MCPTokenRefreshService"):
        assert isinstance(getattr(edition, name), type), name
    assert callable(edition.get_oauth_config_registry)


LAZY_PACKAGES = (
    "harness_core.gateway",
    "harness_core.journal",
    "harness_core.kernel.durable",
    "harness_core.effects",
    "harness_core.interactions",
    "harness_core.sandbox.egress",
    "harness_engines.acp",
    "app.harness.journal_node",
    "app.agent_loop_lib.sandbox.coding",
)


@pytest.mark.parametrize("package", LAZY_PACKAGES)
def test_fu1_ig3_a_lazy_package_still_exports_every_name_from_the_submodule_it_names(package: str) -> None:
    """The ``TYPE_CHECKING`` imports (what type checkers read), ``_EXPORTS`` (what runs) and ``__all__`` agree, and
    every name resolves to its submodule's object."""
    import importlib

    module = importlib.import_module(package)
    source = Path(module.__file__ or "").read_text()
    typed: dict[str, str] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.If) and getattr(node.test, "id", None) == "TYPE_CHECKING":
            for stmt in node.body:
                if isinstance(stmt, ast.ImportFrom) and stmt.module and stmt.module.startswith(f"{package}."):
                    typed.update({a.name: stmt.module.removeprefix(f"{package}.") for a in stmt.names})
    exports = module._EXPORTS
    assert typed == exports
    assert set(module.__all__) == set(exports)
    for name, submodule in exports.items():
        assert getattr(module, name) is getattr(importlib.import_module(f"{package}.{submodule}"), name)
    assert set(exports) <= set(dir(module))
    with pytest.raises(AttributeError):
        _ = module.not_an_export
