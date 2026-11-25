"""A build without telemetry: delete the plug-in package, write an empty manifest, and everything still imports.

This is what an image build for a customer who wants no telemetry code does. The
copy is made in a temp dir and imported in a fresh interpreter in which
``prometheus_client`` cannot be imported at all, so a leftover import fails here
and not in that customer's container.
"""

import json
import os
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from tests.support import edition_defaults

PY = Path(__file__).resolve().parents[3]
# Every package a build may leave out: the telemetry plug-in and whatever else this build names.
OPTIONAL_PACKAGES = list(edition_defaults.optional_plugin_packages())

ENTRY_MODULES = [
    "app.connectors_main",
    "app.docling_main",
    "app.embedding_main",
    "app.extraction_main",
    "app.indexing_main",
    "app.parsing_main",
    "app.query_main",
    "app.docker_proxy_main",
]

# Every module that imported telemetry before the application announced platform events instead. Three more held
# nothing but instrument definitions and are gone: indexing/rekey_metrics, permissions/metrics and vector_db/org_metrics.
FORMER_IMPORTERS = [
    "app.agents.actions.code_graph.code_graph",
    "app.agents.agent_loop.respond",
    "app.api.routes.agent",
    "app.api.routes.chatbot",
    "app.api.routes.graph_view",
    "app.api.routes.search",
    "app.api.routes.skills",
    "app.config.key_value_store_factory",
    "app.connectors.api.router",
    "app.connectors.core.base.data_processor.data_source_entities_processor",
    "app.connectors.core.registry.connector_registry",
    "app.connectors.core.sync.sync_runner",
    "app.connectors.services.code_graph_event_service",
    "app.connectors.services.trash_purge",
    "app.connectors.sources.web.browser_supervisor",
    "app.containers.connector",
    "app.core.db.capabilities",
    "app.core.db.uow",
    "app.core.envelope",
    "app.core.org_context",
    "app.modules.artifacts.reader",
    "app.modules.artifacts.record_backend",
    "app.modules.artifacts.retention",
    "app.modules.artifacts.sidecars",
    "app.modules.artifacts.stored_retention",
    "app.modules.code_graph.edge_build_reconciler",
    "app.modules.code_graph.edge_build_runner",
    "app.modules.entity_resolution.lookup",
    "app.modules.entity_resolution.resolver",
    "app.modules.indexing.duplicate_reconcile",
    "app.modules.indexing.keep_old_copy",
    "app.modules.indexing.lane_upkeep",
    "app.modules.indexing.named_entity_persist_retry",
    "app.modules.indexing.page_limit",
    "app.modules.indexing.refresh_intent",
    "app.modules.indexing.replay_set",
    "app.modules.named_entities.extractor",
    "app.modules.named_entities.stage",
    "app.modules.named_entities.strategies.single_call",
    "app.modules.named_entities.sweep",
    "app.modules.record_metadata.projection",
    "app.modules.transformers.blob_storage",
    "app.modules.transformers.entity_vectorstore",
    "app.modules.transformers.sink_orchestrator",
    "app.services.graph_db.arango.record_schema_guard",
    "app.services.graph_db.graph_db_provider_factory",
    "app.services.graph_db.neo4j.neo4j_provider",
    "app.services.graph_db.postgres.kg.backfill",
    "app.services.graph_db.postgres.kg.bootstrap",
    "app.services.graph_db.postgres.kg.counts",
    "app.services.graph_db.postgres.kg.drainer",
    "app.services.graph_db.postgres.kg.flush",
    "app.services.graph_db.postgres.kg.guard",
    "app.services.graph_db.postgres.kg.hierarchy",
    "app.services.graph_db.postgres.kg.legacy",
    "app.services.graph_db.postgres.kg.net",
    "app.services.graph_db.postgres.kg.project",
    "app.services.graph_db.postgres.kg.read",
    "app.services.graph_db.postgres.kg.restamp",
    "app.services.graph_db.postgres.kg.worker",
    "app.services.llm_gateway.gateway",
    "app.services.messaging.kafka.consumer.indexing_consumer",
    "app.services.messaging.kafka.handlers.entity",
    "app.services.messaging.kafka.handlers.record",
    "app.services.messaging.lanes.assignment",
    "app.services.messaging.redis_streams.indexing_consumer",
    "app.services.paid_cache.bump",
    "app.services.paid_cache.cache",
    "app.services.paid_cache.orphan",
    "app.services.paid_cache.repurge_window",
    "app.services.paid_cache.retention",
    "app.services.paid_cache.store",
    "app.services.vector_db.pgvector.dense",
    "app.services.vector_db.pgvector.lexical",
    "app.services.vector_db.pgvector.sampler",
    "app.services.vector_db.pgvector.validate",
    "app.services.vector_db.search_retry",
    "app.services.vector_db.vector_db_provider_factory",
    "app.web.tools.fetch_url",
    "app.web.tools.web_search",
]
# A module this tree does not have cannot import anything; the rest are probed.
FORMER_IMPORTERS = [m for m in FORMER_IMPORTERS if (PY / (m.replace(".", "/") + ".py")).is_file()]

# ``grep -ri telemetry`` over the stripped tree may find only these, each a product fact and none of
# them this product's own collection: (file, text on the line) -> why it stays.
ALLOWED_MENTIONS: dict[tuple[str, str], str] = {
    ("app/__init__.py", "disable_vendor_telemetry"): "switches a third party's collection off (onnxruntime's uploader); it sends nothing of ours and must stay in a strict build",
    ("app/utils/process_hardening.py", "disable_telemetry_events()"): "switches a third party's collection off (onnxruntime's uploader); it sends nothing of ours and must stay in a strict build",
    ("app/utils/process_hardening.py", "VENDOR_TELEMETRY_OPT_OUTS"): "switches a third party's collection off (onnxruntime's uploader); it sends nothing of ours and must stay in a strict build",
    ("app/utils/process_hardening.py", "def disable_vendor_telemetry"): "switches a third party's collection off (onnxruntime's uploader); it sends nothing of ours and must stay in a strict build",
    ("app/utils/process_hardening.py", "out of third-party telemetry"): "switches a third party's collection off (onnxruntime's uploader); it sends nothing of ours and must stay in a strict build",
    ("app/services/vector_db/qdrant/qdrant.py", "its telemetry reports host RAM"): "Qdrant's own REST endpoint and client model, read for the memory report; a fact about that product",
    ("app/services/vector_db/qdrant/qdrant.py", "the telemetry model of the pinned client"): "Qdrant's own REST endpoint and client model, read for the memory report; a fact about that product",
    ("app/services/vector_db/qdrant/qdrant.py", 'self._rest_get("/telemetry"'): "Qdrant's own REST endpoint and client model, read for the memory report; a fact about that product",
    ("app/services/vector_db/qdrant/qdrant.py", "telemetry.get(\"app\")"): "Qdrant's own REST endpoint and client model, read for the memory report; a fact about that product",
    ("app/services/vector_db/qdrant/qdrant.py", "Qdrant telemetry unavailable"): "Qdrant's own REST endpoint and client model, read for the memory report; a fact about that product",
    ("app/connectors_main.py", "# telemetry (about 12s on a loaded instance)"): "Qdrant's own REST endpoint and client model, read for the memory report; a fact about that product",
    ("app/connectors/sources/snowflake/data_fetcher.py", "event tables hold telemetry"): (
        "what Snowflake keeps in its event tables, said to explain why the connector does not index them"
    ),
    ("app/sources/external/servicenow/servicenow.py", "post_mid_telemetry_metrics"): (
        "a ServiceNow REST API (MID server metrics) the generated client wraps; called only if an agent tool asks"
    ),
    ("app/sources/external/servicenow/servicenow.py", "/mid_telemetry/metrics"): "the same ServiceNow API's path",
    ("app/services/skills/npm_command_parser.py", "--no-telemetry"): (
        "a flag of other people's npm tools that the skill-install command parser recognises as taking no value"
    ),
    **edition_defaults.allowed_usage_collection_mentions(),
}

PROBE = """
import importlib, importlib.abc, json, sys, traceback

class NoPrometheus(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "prometheus_client" or name.startswith("prometheus_client."):
            raise ModuleNotFoundError("prometheus_client is not in this build", name=name)
        return None

sys.meta_path.insert(0, NoPrometheus())
import app
assert app.__file__.startswith(sys.argv[1]), app.__file__
OPTIONAL = sys.argv[2].split(",")
out = {}
for module in sys.argv[3:]:
    try:
        importlib.import_module(module)
        out[module] = "ok"
    except ModuleNotFoundError as exc:
        out[module] = "missing:" + str(exc.name)
    except BaseException as exc:
        out[module] = "error:" + "".join(traceback.format_exception_only(type(exc), exc)).strip()[-600:]
from app import platform_events, plugins
host = plugins.load_plugins("probe")
out["__plugins__"] = [host.loaded, [f.plugin for f in host.failures], plugins.get_capability(plugins.USAGE_COLLECTION) is None]
out["__listeners__"] = sorted(platform_events._listeners)
out["__loaded__"] = sorted(m for m in sys.modules if m.split(".")[0] == "prometheus_client" or (m.split(".")[0] == "app" and "telemetry" in m.lower()))
out["__third_party__"] = sorted({m.split(".")[0] for m in sys.modules if m.split(".")[0] == "opentelemetry"})
out["__ours__"] = sorted(m for m in sys.modules if any(m == p or m.startswith(p + ".") for p in OPTIONAL))
sys.stdout.write("\\nRESULT " + json.dumps(out))
"""


@pytest.fixture(scope="module")
def stripped(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("stripped")
    shutil.copytree(PY / "app", root / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for package in OPTIONAL_PACKAGES:
        shutil.rmtree(root / package, ignore_errors=True)
    subprocess.run(
        [sys.executable, str(PY / "scripts" / "write_plugins_manifest.py"), "--output", str(root / "app" / "plugins_manifest.py")],
        check=True, timeout=60,
    )
    return root


def _probe(root: Path, modules: list[str]) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "TELEMETRY_COLLECTION_FLAG"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # the tree is searched afterwards: keep it as the build left it
    # The stripped copy first; whatever else the environment adds (never this checkout) after it.
    others = [entry for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep) if entry and Path(entry).resolve() != PY]
    env["PYTHONPATH"] = os.pathsep.join([str(root), *others])
    done = subprocess.run(
        [sys.executable, "-c", PROBE, str(root), ",".join(p.replace("/", ".") for p in OPTIONAL_PACKAGES), *modules], cwd=root, env=env, capture_output=True, text=True, timeout=1500, check=False,
    )
    assert "\nRESULT " in done.stdout, done.stderr[-3000:]
    return json.loads(done.stdout.rsplit("\nRESULT ", 1)[1])


def test_the_stripped_tree_has_no_plugin_and_an_empty_manifest(stripped: Path) -> None:
    assert "app/plugins/telemetry" in OPTIONAL_PACKAGES
    assert not any((stripped / package).exists() for package in OPTIONAL_PACKAGES)
    assert "PLUGINS: list[str] = []" in (stripped / "app" / "plugins_manifest.py").read_text()


@pytest.mark.import_runtime
@pytest.mark.timeout(1800)
def test_every_entry_module_and_former_importer_imports_without_the_plugin(stripped: Path) -> None:
    result = _probe(stripped, ENTRY_MODULES + FORMER_IMPORTERS)
    loaded, failed, nothing_collected = result.pop("__plugins__")
    assert (loaded, failed, nothing_collected) == ([], [], True)
    assert result.pop("__listeners__") == [], "nothing listens in a build with no plug-in"
    assert result.pop("__loaded__") == [], "a telemetry module of ours, or the Prometheus client, was imported"
    assert result.pop("__ours__") == [], "a module of a package this build leaves out was imported"
    # Not ours to remove here: a dependency (a cloud SDK, say) may import the OpenTelemetry API when it is installed.
    if result.pop("__third_party__"):
        warnings.warn("a third-party dependency imported the opentelemetry package in the stripped build", stacklevel=1)
    broken = {m: r for m, r in result.items() if r.startswith("error:")}
    ours = {m: r for m, r in result.items() if r.startswith("missing:") and r.split(":", 1)[1].split(".")[0] in ("app", "prometheus_client")}
    assert broken == {} and ours == {}
    # A third-party package this environment lacks (not one the build removes) fails the same import in the full tree.
    elsewhere = {m: r for m, r in result.items() if r.startswith("missing:")}
    if elsewhere:
        warnings.warn(f"not importable in this environment, with or without the plug-in: {elsewhere}", stacklevel=1)
    assert sum(1 for r in result.values() if r == "ok") >= len(result) - len(elsewhere)
    assert sum(1 for r in result.values() if r == "ok") > len(result) // 2


def test_grep_for_telemetry_in_the_stripped_tree_finds_only_the_listed_product_strings(stripped: Path) -> None:
    hits = []
    for path in sorted((stripped / "app").rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(stripped).as_posix()
        if "telemetry" in relative.lower():
            hits.append((relative, "<the file name>"))
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            assert b"telemetry" not in path.read_bytes().lower(), relative
            continue
        hits += [(relative, line.strip()) for line in text.splitlines() if "telemetry" in line.lower()]
    unexplained = [hit for hit in hits if not any(hit[0] == file and text in hit[1] for file, text in ALLOWED_MENTIONS)]
    assert unexplained == []
    unused = [key for key in ALLOWED_MENTIONS if not any(hit[0] == key[0] and key[1] in hit[1] for hit in hits)]
    assert unused == [], "an allowance nothing needs any more"


def test_prometheus_client_is_not_referenced_in_the_stripped_tree(stripped: Path) -> None:
    found = [p.relative_to(stripped).as_posix() for p in (stripped / "app").rglob("*.py") if "prometheus_client" in p.read_text(encoding="utf-8-sig")]
    assert found == []
