#!/usr/bin/env python3
"""
Route Coverage Checker for OpenAPI Spec.

Compares API routes defined in Express (TypeScript) and FastAPI (Python)
source code against the OpenAPI specification. Fails CI when public routes
exist in code but are not documented in the spec.

Usage:
    python .github/scripts/check-route-coverage.py
"""

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml is required. Install with: pip install pyyaml")
    sys.exit(2)

# =====================================================================
# CONFIGURATION
# =====================================================================

# Detect repo root (script is at .github/scripts/)
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent

SPEC_PATH = REPO_ROOT / "backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml"
NODEJS_ROUTES_DIR = REPO_ROOT / "backend/nodejs/apps/src/modules"
PYTHON_DIR = REPO_ROOT / "backend/python"

# Express route files → spec path prefix.
# The prefix is what appears in the OpenAPI spec (without /api/v1).
# Mapped from app.ts configureRoutes() method (lines 326-438).
EXPRESS_ROUTE_MAP: Dict[str, str] = {
    "tokens_manager/routes/health.routes.ts": "/health",
    "user_management/routes/users.routes.ts": "/users",
    "user_management/routes/teams.routes.ts": "/teams",
    "user_management/routes/userGroups.routes.ts": "/user-groups",
    "user_management/routes/org.routes.ts": "/org",
    "auth/routes/saml.routes.ts": "/saml",
    "auth/routes/userAccount.routes.ts": "/userAccount",
    "auth/routes/orgAuthConfig.routes.ts": "/orgAuthConfig",
    "storage/routes/storage.routes.ts": "/document",
    "tokens_manager/routes/connectors.routes.ts": "/connectors",
    "tokens_manager/routes/oauth.routes.ts": "/oauth",
    "knowledge_base/routes/kb.routes.ts": "/knowledgeBase",
    "configuration_manager/routes/cm_routes.ts": "/configurationManager",
    "crawling_manager/routes/cm_routes.ts": "/crawlingManager",
    "mail/routes/mail.routes.ts": "/mail",
    # OAuth provider mounted at /api/v1/oauth2 in code, but spec uses /oauth
    "oauth_provider/routes/oauth.provider.routes.ts": "/oauth",
    "oauth_provider/routes/oauth.clients.routes.ts": "/oauth-clients",
    "oauth_provider/routes/oid.provider.routes.ts": "/.well-known",
    "api-docs/docs.routes.ts": "/docs",
}

# es.routes.ts exports multiple factory functions mounted at different prefixes.
# Map factory function name → spec prefix.
ES_ROUTES_FILE = "enterprise_search/routes/es.routes.ts"
ES_FACTORY_MAP: Dict[str, str] = {
    "createConversationalRouter": "/conversations",
    "createSemanticSearchRouter": "/search",
    "createAgentConversationalRouter": "/agents",
}

# Python Query service router config.
# query_main.py: app.include_router(router, prefix="...")
# Python agent routes are proxied through Node.js /agents/ endpoint,
# so they're NOT included here (covered by Express agent routes).
QUERY_ROUTERS: Dict[str, dict] = {
    "search_router": {
        "file": "app/api/routes/search.py",
        "include_prefix": "/api/v1",
    },
    "chatbot_router": {
        "file": "app/api/routes/chatbot.py",
        "include_prefix": "/api/v1",
    },
    "tools_router": {
        "file": "app/agents/router/router.py",
        "include_prefix": "/api/v1",
    },
}
QUERY_SPEC_PREFIX = "/query"

# Python Connector service — only webhook routes are unique (non-proxied).
# The /api/v1/connectors/* and /api/v1/oauth/* routes are proxied through
# Node.js and already covered by the Express check.
CONNECTOR_ROUTER_FILE = "app/connectors/api/router.py"
CONNECTOR_SPEC_PREFIX = "/connector"
CONNECTOR_WEBHOOK_PATHS = {"/drive/webhook", "/gmail/webhook", "/admin/webhook"}

# Routes to exclude from coverage check
EXCLUDE_PATH_CONTAINS = [
    "/internal/",
    "/updateAppConfig",
    "/updateSmtpConfig",
]
EXCLUDE_PATH_SUFFIXES = [
    "/health",
    "/health/services",
]
EXCLUDE_PATH_EXACT = {
    "/health",
    "/health/services",
}
EXCLUDE_PATH_PREFIXES = [
    "/docs/",
    "/docs",
    "/indexing/",
    "/docling/",
    "/connector/health",
    "/connector/api/v1/",
    "/query/health",
    "/query/api/v1/health",
]

# Known discrepancies between code and spec that are acceptable.
# These are pre-existing issues that should be cleaned up over time.
# Format: (METHOD, path)
KNOWN_MISSING: Set[Tuple[str, str]] = {
    ("PATCH", "/users/{id}/email"),
    # storageConfig in code is a single POST, spec splits into #s3/#azureBlob/#local
    ("POST", "/configurationManager/storageConfig"),
}

# =====================================================================
# REGEX PATTERNS
# =====================================================================

# Express: router.get('/path', ...)
RE_EXPRESS_ROUTE = re.compile(
    r"router\.(get|post|put|patch|delete|options|head)\(\s*['\"]([^'\"]+)['\"]"
)

# Express function boundaries: export function createXRouter(...)
RE_EXPRESS_FACTORY = re.compile(
    r"export\s+function\s+(create\w+Router)\s*\("
)

# FastAPI: @router.get("/path")
RE_FASTAPI_ROUTE = re.compile(
    r"@router\.(get|post|put|patch|delete|options|head)\(\s*['\"]([^'\"]*)['\"]"
)

# FastAPI: APIRouter(prefix="/something")
RE_FASTAPI_ROUTER_PREFIX = re.compile(
    r"APIRouter\([^)]*prefix\s*=\s*['\"]([^'\"]+)['\"]"
)

# Single-line comment patterns
RE_TS_LINE_COMMENT = re.compile(r"^\s*//")
RE_PY_LINE_COMMENT = re.compile(r"^\s*#")


# =====================================================================
# PATH NORMALIZATION
# =====================================================================

def normalize_path(path: str) -> str:
    """Normalize a path for comparison against the OpenAPI spec."""
    # Convert Express :param to OpenAPI {param}
    path = re.sub(r":(\w+)", r"{\1}", path)
    # Strip hash fragments (spec uses #s3, #azureBlob for docs, not matching)
    if "#" in path:
        path = path.split("#")[0]
    # Remove trailing slash (except for root or paths where spec keeps it)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return path


def combine_paths(*parts: str) -> str:
    """Combine path parts, avoiding double slashes."""
    combined = "/".join(p.strip("/") for p in parts if p and p != "/")
    if not combined.startswith("/"):
        combined = "/" + combined
    # Restore leading dot for .well-known
    for part in parts:
        if part.startswith("/."):
            combined = part.rstrip("/") + combined[len(part.rstrip("/")):]
            break
    return re.sub(r"/+", "/", combined)


# =====================================================================
# OPENAPI SPEC PARSER
# =====================================================================

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def parse_openapi_spec(spec_path: Path) -> Set[Tuple[str, str]]:
    """Parse OpenAPI YAML and return set of (METHOD, path) tuples."""
    with open(spec_path) as f:
        spec = yaml.safe_load(f)

    routes: Set[Tuple[str, str]] = set()
    for path, methods in spec.get("paths", {}).items():
        if not isinstance(methods, dict):
            continue
        for method in methods:
            if method.lower() in HTTP_METHODS:
                normalized = normalize_path(path)
                routes.add((method.upper(), normalized))
    return routes


# =====================================================================
# EXPRESS ROUTE PARSER
# =====================================================================

def strip_ts_comments(content: str) -> str:
    """Remove single-line and block comments from TypeScript content."""
    # Remove block comments
    content = re.sub(r"/\*[\s\S]*?\*/", "", content)
    # Remove single-line comments (but keep the line)
    lines = content.split("\n")
    return "\n".join(line for line in lines if not RE_TS_LINE_COMMENT.match(line))


def extract_routes_from_content(content: str) -> Set[Tuple[str, str]]:
    """Extract (METHOD, path) from Express route file content."""
    cleaned = strip_ts_comments(content)
    routes: Set[Tuple[str, str]] = set()
    for match in RE_EXPRESS_ROUTE.finditer(cleaned):
        method = match.group(1).upper()
        path = match.group(2)
        # Skip wildcard routes (e.g., docs catch-all)
        if "*" in path:
            continue
        routes.add((method, path))
    return routes


def parse_express_multi_factory(file_path: Path) -> Dict[str, Set[Tuple[str, str]]]:
    """Parse es.routes.ts — split by factory function, extract routes per factory."""
    content = file_path.read_text()

    # Find all factory function positions
    factories: List[Tuple[str, int]] = []
    for match in RE_EXPRESS_FACTORY.finditer(content):
        factories.append((match.group(1), match.start()))

    result: Dict[str, Set[Tuple[str, str]]] = {}
    for i, (name, start) in enumerate(factories):
        end = factories[i + 1][1] if i + 1 < len(factories) else len(content)
        body = content[start:end]
        routes = extract_routes_from_content(body)
        prefix = ES_FACTORY_MAP.get(name)
        if prefix is not None:
            result[prefix] = routes

    return result


def parse_express_routes() -> Tuple[Set[Tuple[str, str]], Dict[Tuple[str, str], str]]:
    """Parse all Express route files. Returns (routes, sources)."""
    all_routes: Set[Tuple[str, str]] = set()
    sources: Dict[Tuple[str, str], str] = {}

    # Regular single-router files
    for rel_path, spec_prefix in EXPRESS_ROUTE_MAP.items():
        file_path = NODEJS_ROUTES_DIR / rel_path
        if not file_path.exists():
            print(f"  WARNING: Route file not found: {rel_path}")
            continue

        content = file_path.read_text()
        routes = extract_routes_from_content(content)
        for method, path in routes:
            if path == "/":
                full = spec_prefix + "/"
            else:
                full = spec_prefix + path
            normalized = normalize_path(full)
            all_routes.add((method, normalized))
            sources[(method, normalized)] = rel_path

    # Multi-factory file (es.routes.ts)
    es_file = NODEJS_ROUTES_DIR / ES_ROUTES_FILE
    if es_file.exists():
        factory_routes = parse_express_multi_factory(es_file)
        for prefix, routes in factory_routes.items():
            for method, path in routes:
                if path == "/":
                    full = prefix + "/"
                else:
                    full = prefix + path
                normalized = normalize_path(full)
                all_routes.add((method, normalized))
                sources[(method, normalized)] = ES_ROUTES_FILE
    else:
        print(f"  WARNING: Multi-factory route file not found: {ES_ROUTES_FILE}")

    return all_routes, sources


# =====================================================================
# FASTAPI ROUTE PARSER
# =====================================================================

def strip_py_comments(content: str) -> str:
    """Remove comment lines from Python content."""
    lines = content.split("\n")
    return "\n".join(line for line in lines if not RE_PY_LINE_COMMENT.match(line))


def parse_fastapi_file(file_path: Path) -> Tuple[str, Set[Tuple[str, str]]]:
    """Parse a FastAPI router file. Returns (router_prefix, routes)."""
    content = file_path.read_text()
    cleaned = strip_py_comments(content)

    # Get router prefix if defined
    router_prefix = ""
    prefix_match = RE_FASTAPI_ROUTER_PREFIX.search(cleaned)
    if prefix_match:
        router_prefix = prefix_match.group(1)

    routes: Set[Tuple[str, str]] = set()
    for match in RE_FASTAPI_ROUTE.finditer(cleaned):
        method = match.group(1).upper()
        path = match.group(2) or "/"
        routes.add((method, path))

    return router_prefix, routes


def parse_query_routes() -> Set[Tuple[str, str]]:
    """Parse Python Query service routes."""
    all_routes: Set[Tuple[str, str]] = set()

    for _, config in QUERY_ROUTERS.items():
        file_path = PYTHON_DIR / config["file"]
        if not file_path.exists():
            print(f"  WARNING: Python router file not found: {config['file']}")
            continue

        router_prefix, routes = parse_fastapi_file(file_path)
        include_prefix = config["include_prefix"]

        for method, path in routes:
            # Build full spec path: /query + include_prefix + router_prefix + path
            parts = [QUERY_SPEC_PREFIX, include_prefix]
            if router_prefix:
                parts.append(router_prefix)
            if path and path != "/":
                parts.append(path)
            full = combine_paths(*parts)
            normalized = normalize_path(full)
            all_routes.add((method, normalized))

    return all_routes


def parse_connector_webhook_routes() -> Set[Tuple[str, str]]:
    """Parse Python Connector service webhook routes only."""
    file_path = PYTHON_DIR / CONNECTOR_ROUTER_FILE
    if not file_path.exists():
        print(f"  WARNING: Connector router file not found: {CONNECTOR_ROUTER_FILE}")
        return set()

    _, routes = parse_fastapi_file(file_path)
    webhook_routes: Set[Tuple[str, str]] = set()

    for method, path in routes:
        if path in CONNECTOR_WEBHOOK_PATHS:
            full = CONNECTOR_SPEC_PREFIX + path
            normalized = normalize_path(full)
            webhook_routes.add((method, normalized))

    return webhook_routes


# =====================================================================
# EXCLUSION LOGIC
# =====================================================================

def should_exclude(method: str, path: str) -> bool:
    """Check if a route should be excluded from coverage check."""
    if path in EXCLUDE_PATH_EXACT:
        return True
    for substring in EXCLUDE_PATH_CONTAINS:
        if substring in path:
            return True
    for prefix in EXCLUDE_PATH_PREFIXES:
        if path.startswith(prefix):
            return True
    for suffix in EXCLUDE_PATH_SUFFIXES:
        if path.endswith(suffix):
            return True
    return False


# =====================================================================
# COMPARISON AND REPORTING
# =====================================================================

def compare_routes(
    spec_routes: Set[Tuple[str, str]],
    code_routes: Set[Tuple[str, str]],
) -> Tuple[Set[Tuple[str, str]], Set[Tuple[str, str]], Set[Tuple[str, str]]]:
    """Compare spec and code routes. Returns (missing, phantom, covered)."""
    missing = code_routes - spec_routes
    phantom = spec_routes - code_routes
    covered = code_routes & spec_routes
    return missing, phantom, covered


def print_report(
    missing: Set[Tuple[str, str]],
    phantom: Set[Tuple[str, str]],
    covered: Set[Tuple[str, str]],
    sources: Optional[Dict[Tuple[str, str], str]] = None,
) -> None:
    """Print the coverage report."""
    if missing:
        print(f"\n  MISSING FROM SPEC ({len(missing)} routes in code but not documented):")
        for method, path in sorted(missing, key=lambda r: (r[1], r[0])):
            source = f"  [{sources.get((method, path), '?')}]" if sources else ""
            print(f"    {method:7} {path}{source}")

    if phantom:
        print(f"\n  PHANTOM IN SPEC ({len(phantom)} routes documented but not in code):")
        for method, path in sorted(phantom, key=lambda r: (r[1], r[0])):
            print(f"    {method:7} {path}")

    if not missing and not phantom:
        print(f"\n  All {len(covered)} routes match.")


# =====================================================================
# MAIN
# =====================================================================

def main() -> int:
    print("=== OpenAPI Route Coverage Check ===\n")

    # 1. Parse OpenAPI spec
    if not SPEC_PATH.exists():
        print(f"ERROR: OpenAPI spec not found at {SPEC_PATH}")
        return 2

    print("Parsing OpenAPI spec...")
    all_spec_routes = parse_openapi_spec(SPEC_PATH)
    print(f"  Found {len(all_spec_routes)} routes in spec")

    # 2. Parse Express routes
    print("\nParsing Express routes...")
    express_routes_raw, express_sources = parse_express_routes()
    express_routes = {r for r in express_routes_raw if not should_exclude(*r)}
    print(f"  Found {len(express_routes)} public routes "
          f"({len(express_routes_raw) - len(express_routes)} excluded)")

    # 3. Parse Python Query service routes
    print("\nParsing Python Query service routes...")
    query_routes_raw = parse_query_routes()
    query_routes = {r for r in query_routes_raw if not should_exclude(*r)}
    print(f"  Found {len(query_routes)} public routes "
          f"({len(query_routes_raw) - len(query_routes)} excluded)")

    # 4. Parse Python Connector webhook routes
    print("\nParsing Python Connector webhook routes...")
    connector_routes = parse_connector_webhook_routes()
    print(f"  Found {len(connector_routes)} webhook routes")

    # 5. Combine all code routes
    all_code_routes = express_routes | query_routes | connector_routes

    # 6. Filter spec routes to exclude internal/health/docs/etc.
    spec_filtered = {r for r in all_spec_routes if not should_exclude(*r)}

    # 7. Compare
    missing, phantom, covered = compare_routes(spec_filtered, all_code_routes)

    # 8. Remove known discrepancies from missing count
    known_in_missing = missing & KNOWN_MISSING
    actionable_missing = missing - KNOWN_MISSING

    # 9. Report
    print(f"\n{'='*70}")
    print("RESULTS")
    print(f"{'='*70}")
    print(f"\n  Code routes (public):  {len(all_code_routes)}")
    print(f"  Spec routes (public):  {len(spec_filtered)}")
    print(f"  Covered:               {len(covered)}")
    print(f"  Missing from spec:     {len(actionable_missing)}")
    if known_in_missing:
        print(f"  Known discrepancies:   {len(known_in_missing)} (non-blocking)")
    print(f"  Phantom in spec:       {len(phantom)}")

    if all_code_routes:
        coverage = len(covered) / len(all_code_routes) * 100
        print(f"  Coverage:              {coverage:.1f}%")

    print_report(actionable_missing, phantom, covered, express_sources)

    if known_in_missing:
        print(f"\n  KNOWN DISCREPANCIES ({len(known_in_missing)}, non-blocking):")
        for method, path in sorted(known_in_missing, key=lambda r: (r[1], r[0])):
            print(f"    {method:7} {path}")

    # 10. Exit code
    if actionable_missing:
        print(f"\nFAILED: {len(actionable_missing)} route(s) in code are not "
              "documented in the OpenAPI spec.")
        print("Please update backend/nodejs/apps/src/modules/"
              "api-docs/pipeshub-openapi.yaml")
        return 1

    if phantom:
        print(f"\nWARNING: {len(phantom)} route(s) in spec don't match any "
              "code route (non-blocking).")

    print("\nPASSED: All public code routes are documented in the OpenAPI spec.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
