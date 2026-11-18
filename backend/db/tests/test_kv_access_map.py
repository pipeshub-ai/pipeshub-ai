"""The key-value keys each service role may read, checked against the code in both directions.

How the map is built: for each service's entry module, the import closure inside ``backend/python/app`` (every ``import app...`` /
``from app... import`` / relative import, lazy ones included), and in it every key the code names: the ``config_node_constants``
members used, and string or f-string literals starting with ``/services/``, ``/migrations/`` or ``state:`` (placeholders become ``x``).
The closure of every entry module is nearly the whole package, so the scan over-approximates what a service reads. For the two roles
whose reads are allow-lists (indexing, kv) every reachable key must therefore be classified by hand: in ``READS`` with the function that
reads it at run time (the grant), or in ``NOT_READ`` with why that service never reads it. The tests then check
- under-grant: every reachable key is classified, and every key in ``READS`` is readable by the role in enforce;
- over-grant: on every key the code names (and the keys under it, and a few made-up ones), the role's matrix answers exactly what
  ``READS`` says, so a new readable key needs a ``READS`` entry and a reader;
- the citations: each ``READS`` reader function (or module constant) still names its key.
connectors and query read every key but the Node API's own (a deny-list): for them the check is that every reachable key is readable
unless it is one of those. Keys built at run time from other values are not seen by the scan.
"""
import ast
import functools
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from db import bootstrap_roles

from .conftest import EXTENSIONS, VETTED, _drop_roles, run_migrate

PYTHON = Path(__file__).resolve().parents[2] / "python"
KEY = re.compile(r"^(/services/|/migrations/|state:)")
# REST paths of the Salesforce API, not key-value keys
NOT_KEYS = ("app/sources/external/salesforce/", "app/sources/client/salesforce/", "app/connectors/sources/salesforce/")

# the role each service connects as (process_monitor.sh: its own login when set; the configuration-only services use the kv login)
SERVICES = {
    "connectors": ["app.connectors_main"],
    "query": ["app.query_main"],
    "indexing": ["app.indexing_main"],
    "kv": ["app.embedding_main", "app.docling_main", "app.parsing_main", "app.extraction_main"],
}

# connectors and query: everything but these
NODE_ONLY = ("/services/smtp", "/services/slackBot", "/services/auth/", "/services/mcp/accessPolicy/", "crawl:")

# The grants: key (exact), "prefix*", or "re:<regex>" -> where it is read at run time ("path::Qualified.name" below backend/python: a
# function, class or module constant, never a line number, so an edit elsewhere in the file moves nothing) and a text one line in it
# holds. Reads only; writes are the revision's own allow-lists.
_META = "re:^/services/connectors/[^/]+/metadataSchemas/"
READS = {
    "kv": {
        "/services/aiModels": ("app/embedding_main.py::_local_model_in_use", "AI_MODELS"),         # also utils/llm.py::is_local_cpu_embedding_configured (docling, parsing), extraction/client.py::ExtractionClient._context_length_ceiling
        "/services/secretKeys": ("app/api/middlewares/auth.py::isJwtTokenValid", "SECRET_KEYS"),  # parsing and extraction routes; policy_store.py::IdentityPolicyStore.pseudonym_key (pseudonyms)
        "/services/aiGateway/identityPolicy": ("app/services/model_identity/policy_store.py::IdentityPolicyStore._load", "AI_GATEWAY_IDENTITY_POLICY"),  # routes/parsing.py::_parse_file, routes/extraction.py::classify
        "/services/metricsCollection": ("app/telemetry/pusher.py::METRICS_CONFIG_KEY", "/services/metricsCollection"),  # embedding, docling (pusher bound at start)
        "/migrations/etcd_to_redis": ("app/config/configuration_service.py::ConfigurationService._start_watch", "/migrations/etcd_to_redis"),  # every ConfigurationService
        # parsing and extraction bind the fleet breaker at start (fleet_redis.bind_service_fleet): created if absent, then read
        "/services/indexing/spendGuard/hmacKey": ("app/services/llm_gateway/fleet_breaker.py::load_install_secret", "INSTALL_SECRET_KEY"),
        "/services/redis": ("app/config/configuration_service.py::ConfigurationService.get_redis_config", "REDIS"),  # the same: fleet_redis.py::fleet_redis_config
    },
    "indexing": {
        "state:*": ("app/services/pg_state.py::LOCK_PREFIX", "state:lock:"),
        "/services/scopeFields/*": ("app/services/permissions/generation.py::ENABLED_KEY", "/services/scopeFields/"),
        "/services/vectordb/*": ("app/services/vector_db/strategy_resolver.py::STRATEGY_CONFIG_KEY", "/services/vectordb/"),
        "/services/platform/artifactRollout/membership/*": ("app/services/messaging/membership.py::MEMBERSHIP_DIRECTORY", "/services/platform/artifactRollout/membership/"),
        "/services/entityCleanup/*": ("app/connectors/services/entity_cleanup_intents.py::PENDING_DIRECTORY", "/services/entityCleanup/"),  # entity_index_rebuild.py::EntityIndexRebuilder._reconcile_entity_cleanup
        "/migrations/class_units_backfill/*": ("app/modules/indexing/class_units_backfill.py::FLAG_PREFIX", "/migrations/class_units_backfill/"),
        "/services/secretKeys": ("app/core/envelope.py::configure_from_config.load", "SECRET_KEYS"),
        "/services/endpoints": ("app/modules/transformers/blob_storage.py::BlobStorage._get_auth_and_config", "ENDPOINTS"),
        "/services/storage": ("app/modules/transformers/blob_storage.py::BlobStorage._get_auth_and_config", "STORAGE"),
        "/services/arangodb": ("app/health/health.py::Health.health_check_arango", "ARANGODB"),
        "/services/kafka": ("app/services/messaging/utils.py::MessagingUtils._create_kafka_consumer_config", "KAFKA"),
        "/services/redis": ("app/config/configuration_service.py::ConfigurationService.get_redis_config", "REDIS"),
        "/services/qdrant": ("app/services/vector_db/qdrant/qdrant.py::QdrantService.connect", "QDRANT"),
        "/services/pgvector": ("app/services/vector_db/pgvector/pgvector.py::PgVectorService._load_config", "PGVECTOR"),
        "/services/opensearch": ("app/services/vector_db/opensearch/opensearch.py::OpenSearchService.connect", "OPENSEARCH"),
        "/services/redis-vector": ("app/services/vector_db/redis/redis_vector.py::RedisVectorService._load_config", "REDIS_VECTOR"),
        "/services/aiModels": ("app/modules/transformers/entity_vectorstore.py::EntityVectorStore._load_embedding_configs", "AI_MODELS"),
        "/services/aiGateway/identityPolicy": ("app/services/model_identity/policy_store.py::IdentityPolicyStore._load", "AI_GATEWAY_IDENTITY_POLICY"),
        "/services/namedEntities": ("app/modules/named_entities/domain/config.py::load_named_entity_config", "NAMED_ENTITIES"),
        "/services/metricsCollection": ("app/telemetry/pusher.py::METRICS_CONFIG_KEY", "/services/metricsCollection"),
        "/services/platform/settings": ("app/services/featureflag/platform_settings.py::PLATFORM_SETTINGS_KEY", "/services/platform/settings"),  # pipeline stages
        "/services/file_types/config": ("app/services/messaging/kafka/handlers/record.py::RecordEventHandler._disabled_file_types", "FILE_TYPES_CONFIG_KEY"),  # the record event handler
        "/migrations/kh_listing_state_v1": ("app/services/graph_db/interface/graph_db_provider.py::KH_LISTING_STATE_FLAG", "/migrations/kh_listing_state_v1"),
        "/migrations/etcd_to_redis": ("app/config/configuration_service.py::ConfigurationService._start_watch", "/migrations/etcd_to_redis"),
        _META: ("app/modules/record_metadata/schemas.py::_schemas_key", "metadataSchemas"),  # sink_orchestrator.py::SinkOrchestrator._project_properties (indexing)
        "/services/indexing/spendGuard": ("app/modules/pipeline/pause_judge.py::spend_limits_source.read", "SPEND_GUARD_KEY"),  # the pause judge (pipeline/runtime.py::build_pipeline_runtime)
        "/services/indexing/spendGuard/hmacKey": ("app/services/llm_gateway/fleet_breaker.py::load_install_secret", "INSTALL_SECRET_KEY"),  # indexing_main.py::_bind_fleet_state, at start
        # the class admission's live numbers: indexing_main.py::lifespan attaches the store (attach_parse_admission_config)
        "/services/indexing/parseAdmission": ("app/modules/parse_engine/admission.py::LiveAdmissionConfig.refresh", "ADMISSION_CONFIG_KEY"),
    },
}

_CONN = "connector configuration, credentials and leases: the connectors service and query's agent tools"
_TOOLS = "toolsets, API toolsets, MCP servers and their credentials: query's agents and the connectors service's admin routes"
_ADMIN = "the Node API's own settings"
_QUERY = "query only (chat, agents, search)"
_CJOBS = "the connectors service's own background jobs and migrations (containers/connector.py, connectors_main.py)"
_ENVFB = "named by ConfigurationService's environment fallback (a key comparison), read only by the service that opens that store"
_DEAD = "named in config_node_constants, read by no code"
_LIST = "listed in encrypted_store.py as stored unencrypted, not read there"
# Reachable from a service's entry module but never read by it at run time, with why.
_COMMON_NOT_READ = {
    "/services/connectors/*": _CONN, "/services/oauth/*": _CONN, "/services/oauths/*": _TOOLS, "/services/toolset*": _TOOLS,
    "/services/api-toolsets*": _TOOLS, "/services/mcp*": _TOOLS, "/services/agents/*": _QUERY, "/services/webSearch": _QUERY,
    "/services/aiModelApiModes": "read only by query, which creates the store at start (query_main.py::initialize_container); elsewhere get_llm_api_mode_store() is None",
    "/services/systemPrompts": _QUERY, "/services/demoData/*": "demo data markers: the connectors service and query's retrieval",
    "/services/softDeletePurge/*": _CJOBS, "/services/storageRelease/*": _CJOBS, "/services/lateWriteSweep/*": _CJOBS,
    "/services/storageMigration/*": _CJOBS, "/migrations/*": _CJOBS,
    "/services/deployment": "read by the connectors container at start (containers/connector.py::initialize_container); " + _LIST,
    "/services/migrations": _DEAD, "/services/inheritance": _DEAD, "/services/neo4j": _DEAD, "/services/message-broker": _DEAD,
    "/services/redis-streams": _DEAD, "/services/aiModels/*": _DEAD + " (/services/aiModels/embedding)",
    "/services/mongodb": _ADMIN, "/services/smtp": _ADMIN, "/services/slackBot": _ADMIN, "/services/auth/*": _ADMIN, "crawl:*": _ADMIN,
}
NOT_READ = {
    "indexing": {**_COMMON_NOT_READ,
                 "/services/platform/*": "only settings (READS) and the rollout membership (READS) under it are read",
                 "/services/scopeFields*": "the keys under /services/scopeFields/ are in READS"},
    "kv": {**_COMMON_NOT_READ,
           "state:*": "runtime locks, leases and retry counters: the message consumers (connectors, indexing, query)",
           "/services/scopeFields*": "register_scope_generation runs in connectors_main, indexing_main and query_main only",
           "/services/vectordb/*": "vector collection settings: indexing and query",
           "/services/platform/*": "feature flags and rollout membership: read by the indexing pipeline, query and the connectors service",
           "/services/entityCleanup/*": "cleanup intents: the connectors service and indexing",
           "/services/endpoints": "service URLs: read by query, indexing and the connectors service (caller_role.py is not mounted here)",
           "/services/storage": "storage settings: the Node API and indexing's blob reads",
           "/services/namedEntities": "named-entity settings: indexing and query",
           "/services/arangodb": "BaseAppContainer's ArangoDB client is resolved only by services with a graph connection; " + _ENVFB,
           "/services/kafka": _ENVFB, "/services/qdrant": _ENVFB, "/services/opensearch": _ENVFB,
           "/services/redis-vector": _ENVFB, "/services/pgvector": "the pgvector store: indexing and query",
           "/services/file_types/config": "file-type settings: read by indexing's record event handler only (record.py::RecordEventHandler._disabled_file_types)",
           "/services/indexing/spendGuard": "the pause caps of stage jobs: read by the indexing pipeline's pause judge only (modules/pipeline/runtime.py::build_pipeline_runtime)",
           "/services/indexing/parseAdmission": "the class admission's numbers: only indexing_main.py::lifespan attaches the store; elsewhere "
                                                "the wiring's LiveAdmissionConfig(None) reads no key (parse_engine/wiring.py::_Registry)"},
}


def _scope(node: ast.AST) -> Iterator[tuple[str, ast.AST]]:
    """(name, node) for each def, class and assigned name in node's own scope, inside if/try/with blocks too."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield child.name, child
        elif isinstance(child, (ast.Assign, ast.AnnAssign)):
            for target in child.targets if isinstance(child, ast.Assign) else [child.target]:
                if isinstance(target, ast.Name):
                    yield target.id, child
        else:
            yield from _scope(child)


# The same as backend/python/tests/permissions/matrix.py, which this package cannot import (test_cited_line_is_the_matrix_one).
def cited_line(path: Path, where: str, snippet: str) -> int | None:
    """The line holding `snippet` inside the def, class or assignment the dotted name `where` names in `path` (a class's
    decorators included), or None when the name is gone or no longer holds the snippet."""
    text = path.read_text(encoding="utf-8")
    lines, nodes = text.splitlines(), [ast.parse(text)]
    for part in where.split("."):
        nodes = [n for parent in nodes for name, n in _scope(parent) if name == part]
    for n in nodes:
        start = min([n.lineno] + [d.lineno for d in getattr(n, "decorator_list", [])])
        hit = next((i for i in range(start, n.end_lineno + 1) if snippet in lines[i - 1]), None)
        if hit:
            return hit
    return None


def _module_file(mod: str) -> Path | None:
    p = PYTHON / mod.replace(".", "/")
    return p.with_suffix(".py") if p.with_suffix(".py").exists() else (p / "__init__.py" if (p / "__init__.py").exists() else None)


def _closure(entry: str) -> dict[str, ast.Module]:
    seen: dict[str, ast.Module] = {}
    todo = [entry]
    while todo:
        mod = todo.pop()
        f = _module_file(mod)
        if mod in seen or f is None:
            continue
        tree = seen[mod] = ast.parse(f.read_text(encoding="utf-8"))
        parts = mod.split(".")
        todo += [".".join(parts[:i]) for i in range(1, len(parts))]                    # a package's __init__ runs first
        pkg = mod if f.name == "__init__.py" else mod.rsplit(".", 1)[0]
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                todo += [a.name for a in n.names if a.name.startswith("app")]
            elif isinstance(n, ast.ImportFrom):
                base = n.module or ""
                if n.level:
                    base = ".".join(pkg.split(".")[: len(pkg.split(".")) - (n.level - 1)] + ([base] if base else []))
                if base.startswith("app"):
                    todo += [base, *(f"{base}.{a.name}" for a in n.names)]
    return seen


def _constants() -> dict[str, str]:
    tree = ast.parse((PYTHON / "app/config/constants/service.py").read_text(encoding="utf-8"))
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "config_node_constants")
    return {s.targets[0].id: s.value.value for s in cls.body if isinstance(s, ast.Assign) and isinstance(s.value, ast.Constant)}


@functools.cache
def reachable_keys(entry: str) -> dict[str, str]:
    """{key as written (placeholders as x): the first module that names it}"""
    consts, out = _constants(), {}
    for mod, tree in sorted(_closure(entry).items()):
        if any(str(_module_file(mod)).startswith(str(PYTHON / p)) for p in NOT_KEYS):
            continue
        for n in ast.walk(tree):
            key = None
            if (isinstance(n, ast.Attribute) and n.attr == "value" and isinstance(n.value, ast.Attribute)
                    and isinstance(n.value.value, ast.Name) and n.value.value.id == "config_node_constants"):
                key = consts[n.value.attr]
            elif isinstance(n, ast.Constant) and isinstance(n.value, str) and KEY.match(n.value):
                key = n.value
            elif isinstance(n, ast.JoinedStr):
                s = "".join(v.value if isinstance(v, ast.Constant) else "x" for v in n.values)
                key = s if KEY.match(s) else None
            if key is not None:
                out.setdefault(key, mod)
    return out


def _entry_matches(entry: str, key: str) -> bool:
    if entry.startswith("re:"):
        return re.match(entry[3:], key) is not None
    return key.startswith(entry[:-1]) if entry.endswith("*") else key == entry


def _reachable(role: str) -> dict[str, str]:
    keys: dict[str, str] = {}
    for entry in SERVICES[role]:
        for k, mod in reachable_keys(entry).items():
            keys.setdefault(k, f"{entry}: {mod}")
    return keys


def test_the_scan_finds_the_keys_it_must():
    """The scan itself: it sees constants, literals, f-strings and lazily imported modules."""
    keys = reachable_keys("app.extraction_main")
    for k in ("/services/aiGateway/identityPolicy", "/services/secretKeys", "/services/aiModels", "/services/connectors/x/config",
              "state:lease:", "/migrations/etcd_to_redis"):
        assert k in keys, k
    assert not any(k.startswith("/services/data/") for k in keys)          # Salesforce REST paths are not keys


@pytest.mark.parametrize("role", sorted(READS))
def test_each_reader_citation_names_its_key(role):
    wrong = [(key, where, text) for key, (where, text) in READS[role].items()
             if cited_line(PYTHON / where.partition("::")[0], where.partition("::")[2], text) is None]
    assert wrong == []


_READER = """\
FLAG = "/migrations/x"


class Store:
    async def load(self):
        return await self.config.get_config(config_node_constants.AI_MODELS.value)

    async def other(self):
        return None
"""
_MOVED = _READER.replace("return await self.config.get_config(config_node_constants.AI_MODELS.value)", "return None", 1).replace(
    "    async def other(self):\n        return None", "    async def other(self):\n        return await self.config.get_config(config_node_constants.AI_MODELS.value)")


@pytest.mark.parametrize(("source", "line"), [
    (_READER, 6),
    ("import os\n\n\n" + _READER, 9),                                    # an unrelated edit shifts every line
    (_READER.replace("config_node_constants.AI_MODELS.value", "None"), None),
    (_READER.replace("def load", "def load_all"), None),
    (_MOVED, None),
], ids=["intact", "lines-shifted", "code-deleted", "function-renamed", "moved-to-another-function"])
def test_a_citation_holds_only_while_its_function_names_the_key(tmp_path, source, line):
    path = tmp_path / "store.py"
    path.write_text(source)
    assert cited_line(path, "Store.load", "AI_MODELS") == line
    assert cited_line(path, "FLAG", "/migrations/x") == source.splitlines().index('FLAG = "/migrations/x"') + 1


def test_cited_line_is_the_matrix_one():
    def source(path: Path) -> dict[str, str]:
        text = path.read_text(encoding="utf-8")
        return {n.name: ast.get_source_segment(text, n) for n in ast.parse(text).body
                if isinstance(n, ast.FunctionDef) and n.name in ("_scope", "cited_line")}
    assert source(Path(__file__)) == source(PYTHON / "tests/permissions/matrix.py")


@pytest.mark.parametrize("role", sorted(READS))
def test_every_reachable_key_is_classified_as_read_or_not_read(role):
    """Under-grant, by review: a key a service can reach and its role cannot read needs a reason it is never read."""
    loose = [(k, where) for k, where in sorted(_reachable(role).items())
             if not any(_entry_matches(e, k) or _entry_matches(e, k.rstrip("/") + "/x") for e in (*READS[role], *NOT_READ[role]))]
    assert loose == [], f"pipeshub_{role}: classify these keys in READS (with the line that reads them) or NOT_READ (with why): {loose}"


@pytest.fixture(scope="module")
def matrix(cluster):
    """A database at the heads; yields may(role, key): may that role read the key in enforce."""
    name = "t_kv_access_map"
    with cluster.admin() as a:
        a.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        _drop_roles(a)
        a.execute(f"CREATE DATABASE {name}")
    with cluster.admin(name) as a:
        bootstrap_roles.bootstrap(a, VETTED, extensions=EXTENSIONS)
    assert run_migrate(cluster.url(name, "pipeshub_migrate")).returncode == 0
    with cluster.admin(name) as a:
        def may(role: str, key: str) -> bool:
            a.execute(f"SET ROLE pipeshub_{role}")
            try:
                return a.execute("SELECT platform.kv_may(%s, false)", (key,)).fetchone()[0]
            finally:
                a.execute("RESET ROLE")
        yield may
    with cluster.admin() as a:
        a.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.mark.parametrize("role", sorted(READS))
def test_the_role_can_read_every_key_it_reads(matrix, role):
    """Under-grant, against the matrix: every READS entry is readable in enforce (a prefix through a key under it)."""
    probe = {e: (e[:-1] + "x" if e.endswith("*") else ("/services/connectors/c1/metadataSchemas/latest" if e == _META else e)) for e in READS[role]}
    assert [e for e, k in probe.items() if not matrix(role, k)] == []


@pytest.mark.parametrize("role", sorted(READS))
def test_the_role_can_read_nothing_else(matrix, role):
    """Over-grant: on every key the code names, the keys under it and made-up ones, the matrix grants exactly what READS lists."""
    universe = {"/services/someNewProvider", "/services/someNewProvider/secret", "/migrations/some_new_flag", "state:x", "other:x"}
    for k in _reachable(role):
        universe |= {k, k.rstrip("/") + "/x"}
    for e in (*READS[role], *NOT_READ[role]):
        if not e.startswith("re:"):
            universe |= {e.rstrip("*"), e.rstrip("*") + "x"}
    wrong = [(k, got) for k in sorted(universe)
             if (got := matrix(role, k)) != any(_entry_matches(e, k) for e in READS[role])]
    assert wrong == [], f"pipeshub_{role}: the matrix and READS disagree on (key, matrix grants): {wrong}"


@pytest.mark.parametrize("role", ["connectors", "query"])
def test_connectors_and_query_read_every_reachable_key_but_the_node_api_own(matrix, role):
    refused = [(probe, where) for key, where in sorted(_reachable(role).items()) for probe in sorted({key, key.rstrip("/") + "/x"})
               if not matrix(role, probe) and not probe.startswith(NODE_ONLY)]
    assert refused == []
    assert [p for p in NODE_ONLY if matrix(role, p + "x" if p.endswith((":", "/")) else p)] == []
