"""Every key-value key the Python services can reach is readable by the role each service runs as, or is denied on purpose.

How the map is built (static, an over-approximation): for each service's entry module, the import closure inside ``backend/python/app``
(every ``import app...`` / ``from app... import`` / relative import, lazy ones included), and in it every key the code names: the
``config_node_constants`` members, and string or f-string literals starting with ``/services/``, ``/migrations/`` or ``state:`` (an
f-string's placeholders become ``x``). A literal is checked as written and with ``/x`` appended (the keys under it). The closure of
every entry module is nearly the whole package, so this cannot tell which keys a service reads at run time; it can tell that no key
any service could read is refused without a reviewed reason. A denied key must be listed in ``DENIED`` with its reason, and every
listed prefix must really be denied (so the list cannot go stale). Keys built only at run time from other values are not seen.
"""
import ast
import re
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

_ADMIN = "the Node API's own settings; no Python service reads them"
_CRED = ("credentials used only by connectors (sync, token refresh, admin routes) and query (agent tools and toolsets); indexing reads "
         "record bytes through the connectors service and the configuration-only services never open a connector, toolset or MCP server")
_DENIED_ALL = {"/services/smtp": _ADMIN, "/services/slackBot": _ADMIN, "/services/auth/": _ADMIN, "/services/mcp/accessPolicy/": _ADMIN,
               "crawl:": _ADMIN}
_DENIED_CRED = {
    **_DENIED_ALL,
    "/services/connectors/": _CRED + " (a connector's metadata schemas, which indexing reads, stay readable)",
    "/services/oauth/": _CRED, "/services/oauths/": _CRED, "/services/toolset": _CRED, "/services/api-toolsets/": _CRED,
    "/services/mcp/": _CRED, "/services/webSearch": "web search runs in query's agents only",
    "/services/agents/": "parked agent turns (conversation content): query's agent loop only",
}
DENIED = {"connectors": _DENIED_ALL, "query": _DENIED_ALL, "indexing": _DENIED_CRED, "kv": _DENIED_CRED}


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


def test_the_scan_finds_the_keys_it_must():
    """The scan itself: it sees constants, literals, f-strings and lazily imported modules."""
    keys = reachable_keys("app.extraction_main")
    for k in ("/services/aiGateway/identityPolicy", "/services/secretKeys", "/services/aiModels", "/services/connectors/x/config",
              "state:lease:", "/migrations/etcd_to_redis"):
        assert k in keys, k
    assert not any(k.startswith("/services/data/") for k in keys)          # Salesforce REST paths are not keys


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


@pytest.mark.parametrize("role", sorted(SERVICES))
def test_every_key_a_service_can_reach_is_readable_by_its_role_or_denied_with_a_reason(matrix, role):
    keys: dict[str, str] = {}
    for entry in SERVICES[role]:
        for k, mod in reachable_keys(entry).items():
            keys.setdefault(k, f"{entry}: {mod}")
    refused = [(probe, where) for key, where in sorted(keys.items()) for probe in sorted({key, key.rstrip("/") + "/x"})
               if not matrix(role, probe) and not any(probe.startswith(p) for p in DENIED[role])]
    assert refused == [], f"pipeshub_{role} cannot read these keys and no reason is listed in DENIED: {refused}"


@pytest.mark.parametrize("role", sorted(SERVICES))
def test_every_listed_denial_is_a_real_one(matrix, role):
    assert [p for p in DENIED[role] if matrix(role, p + "x" if p.endswith((":", "/")) else p)] == []
