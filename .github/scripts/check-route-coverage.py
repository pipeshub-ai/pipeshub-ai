#!/usr/bin/env python3
"""
Route Coverage Checker for OpenAPI Spec.

Compares API routes defined in Express (TypeScript) source code against the
OpenAPI specification. Fails CI when public routes exist in code but are not
documented in the spec.

Route files and mount paths are auto-discovered from app.ts and globbed
route files — no manual mapping required.

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
CONFIG_PATH = SCRIPT_DIR / "route-coverage-config.yaml"

SPEC_PATH = REPO_ROOT / "backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml"
NODEJS_ROUTES_DIR = REPO_ROOT / "backend/nodejs/apps/src/modules"
APP_TS_PATH = REPO_ROOT / "backend/nodejs/apps/src/app.ts"

# Load route configuration from YAML
if not CONFIG_PATH.exists():
    print(f"ERROR: Config file not found at {CONFIG_PATH}")
    sys.exit(2)

with open(CONFIG_PATH) as _f:
    _config = yaml.safe_load(_f)

MOUNT_TO_SPEC_OVERRIDES: Dict[str, str] = _config.get("mount_to_spec_overrides") or {}
INLINE_ROUTES: Dict[str, List[str]] = _config.get("inline_routes") or {}
KNOWN_MISSING: Set[Tuple[str, str]] = {
    tuple(entry) for entry in _config.get("known_missing", [])
}
# Internal service-to-service routes (scoped token auth) excluded from coverage
INTERNAL_ROUTES: Set[Tuple[str, str]] = {
    tuple(entry) for entry in _config.get("internal_routes", [])
}

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
EXCLUDE_PATH_PREFIXES = [
    "/docs/",
    "/docs",
    "/indexing/",
    "/docling/",
    "/connector/",
    "/query/",
]

# Python service prefixes that must NOT appear in the OpenAPI spec.
# These services have their own specs; adding them here is an error.
PYTHON_SERVICE_PREFIXES = [
    "/indexing/",
    "/docling/",
    "/connector/",
    "/query/",
]

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

# app.ts: this.app.use('/mount', createXRouter(...))
RE_APP_USE_MOUNT = re.compile(
    r"this\.app\.use\(\s*['\"]([^'\"]+)['\"]"
    r"\s*,\s*(create\w+Router)\s*\("
)

# Single-line comment pattern for TypeScript
RE_TS_LINE_COMMENT = re.compile(r"^\s*//")


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
# AUTO-DISCOVERY
# =====================================================================

def parse_app_ts_mounts() -> Dict[str, str]:
    """Parse app.ts to extract factory->mount path mappings.

    Reads all this.app.use('/path', createXxxRouter(...)) calls.
    Returns dict: {factoryName: mountPath}.
    """
    content = APP_TS_PATH.read_text()
    mounts: Dict[str, str] = {}
    for match in RE_APP_USE_MOUNT.finditer(content):
        mount_path = match.group(1)
        factory_name = match.group(2)
        mounts[factory_name] = mount_path
    return mounts


def discover_route_files() -> Dict[str, str]:
    """Glob route files and extract factory function names.

    Searches modules/**/routes/*.ts and modules/**/*.routes.ts.
    Skips index.ts barrel files.
    Returns dict: {factoryName: relFilePath}.
    """
    seen_files: Set[Path] = set()
    factory_to_file: Dict[str, str] = {}

    for file_path in sorted(
        set(NODEJS_ROUTES_DIR.glob("**/routes/*.ts"))
        | set(NODEJS_ROUTES_DIR.glob("**/*.routes.ts"))
    ):
        if file_path.name == "index.ts":
            continue
        if file_path in seen_files:
            continue
        seen_files.add(file_path)
        content = file_path.read_text()
        rel_path = str(file_path.relative_to(NODEJS_ROUTES_DIR))
        for match in RE_EXPRESS_FACTORY.finditer(content):
            factory_to_file[match.group(1)] = rel_path

    return factory_to_file


def mount_to_spec_prefix(mount_path: str) -> str:
    """Convert an Express mount path to its OpenAPI spec prefix.

    Strips /api/v1 and applies any overrides from config.
    """
    if mount_path.startswith("/api/v1/"):
        prefix = mount_path[len("/api/v1"):]
    elif mount_path == "/api/v1":
        prefix = "/"
    else:
        # Root-level mounts like /.well-known
        prefix = mount_path
    override = MOUNT_TO_SPEC_OVERRIDES.get(prefix, prefix)
    return prefix if override is None else override


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


def parse_express_routes() -> Tuple[Set[Tuple[str, str]], Dict[Tuple[str, str], str]]:
    """Auto-discover and parse all Express route files.

    1. Parses app.ts for factory->mount mappings
    2. Globs route files for factory->file mappings
    3. Matches, extracts routes, and builds full spec paths
    4. Handles multi-factory files by splitting on factory boundaries
    5. Adds inline routes from config

    Returns (routes, sources).
    """
    all_routes: Set[Tuple[str, str]] = set()
    sources: Dict[Tuple[str, str], str] = {}

    # Step 1: Parse app.ts mounts
    factory_mounts = parse_app_ts_mounts()

    # Step 2: Discover route files
    factory_files = discover_route_files()

    # Step 3: Diagnostics
    print(f"  Auto-discovered {len(factory_mounts)} factory mounts in app.ts")
    print(f"  Found {len(factory_files)} factories in "
          f"{len(set(factory_files.values()))} route files")

    for factory, rel_path in sorted(factory_files.items()):
        if factory not in factory_mounts:
            print(f"  WARNING: Unmounted factory {factory} in {rel_path}")

    for factory, mount in sorted(factory_mounts.items()):
        if factory not in factory_files:
            print(f"  WARNING: No source file found for {factory} "
                  f"(mounted at {mount})")

    # Step 4: Group by file to handle multi-factory files
    file_to_factories: Dict[str, List[Tuple[str, str]]] = {}
    for factory, mount in factory_mounts.items():
        rel_path = factory_files.get(factory)
        if not rel_path:
            continue
        file_to_factories.setdefault(rel_path, []).append((factory, mount))

    for rel_path, factories in file_to_factories.items():
        abs_path = NODEJS_ROUTES_DIR / rel_path
        if not abs_path.exists():
            print(f"  WARNING: Route file not found: {rel_path}")
            continue

        content = abs_path.read_text()

        if len(factories) == 1:
            # Single-factory file: extract all routes
            _, mount_path = factories[0]
            spec_prefix = mount_to_spec_prefix(mount_path)
            routes = extract_routes_from_content(content)
            for method, path in routes:
                full = spec_prefix if path == "/" else spec_prefix + path
                normalized = normalize_path(full)
                all_routes.add((method, normalized))
                sources[(method, normalized)] = rel_path
        else:
            # Multi-factory file: split by factory boundary
            factory_positions: List[Tuple[str, int]] = []
            for match in RE_EXPRESS_FACTORY.finditer(content):
                factory_positions.append((match.group(1), match.start()))

            for i, (name, start) in enumerate(factory_positions):
                mount_path = factory_mounts.get(name)
                if mount_path is None:
                    continue
                end = (factory_positions[i + 1][1]
                       if i + 1 < len(factory_positions) else len(content))
                body = content[start:end]
                spec_prefix = mount_to_spec_prefix(mount_path)
                routes = extract_routes_from_content(body)
                for method, path in routes:
                    full = spec_prefix if path == "/" else spec_prefix + path
                    normalized = normalize_path(full)
                    all_routes.add((method, normalized))
                    sources[(method, normalized)] = rel_path

    # Step 5: Inline routes from config
    for path, methods in INLINE_ROUTES.items():
        for method in methods:
            normalized = normalize_path(path)
            all_routes.add((method.upper(), normalized))
            sources[(method.upper(), normalized)] = "app.ts (inline)"

    return all_routes, sources


# =====================================================================
# EXCLUSION LOGIC
# =====================================================================

def should_exclude(method: str, path: str) -> bool:
    """Check if a route should be excluded from coverage check."""
    if (method, path) in INTERNAL_ROUTES:
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

    if not APP_TS_PATH.exists():
        print(f"ERROR: app.ts not found at {APP_TS_PATH}")
        return 2

    print("Parsing OpenAPI spec...")
    all_spec_routes = parse_openapi_spec(SPEC_PATH)
    print(f"  Found {len(all_spec_routes)} routes in spec")

    # 2. Check for Python service routes that don't belong in the spec
    python_in_spec: Set[Tuple[str, str]] = set()
    for method, path in all_spec_routes:
        for prefix in PYTHON_SERVICE_PREFIXES:
            if path.startswith(prefix):
                python_in_spec.add((method, path))
                break

    if python_in_spec:
        print(f"\n  PYTHON SERVICE ROUTES IN SPEC ({len(python_in_spec)} found):")
        for method, path in sorted(python_in_spec, key=lambda r: (r[1], r[0])):
            print(f"    {method:7} {path}")
        print("  These belong to Python microservices which are internal services and should not be in "
              "the OpenAPI spec.")

    # 3. Parse Express routes (auto-discovered)
    print("\nParsing Express routes (auto-discovery)...")
    express_routes_raw, express_sources = parse_express_routes()
    express_routes = {r for r in express_routes_raw if not should_exclude(*r)}
    print(f"  Found {len(express_routes)} public routes "
          f"({len(express_routes_raw) - len(express_routes)} excluded)")

    # 4. All code routes (Python services excluded via EXCLUDE_PATH_PREFIXES)
    all_code_routes = express_routes

    # 5. Filter spec routes to exclude internal/health/docs/etc.
    spec_filtered = {r for r in all_spec_routes if not should_exclude(*r)}

    # 6. Compare
    missing, phantom, covered = compare_routes(spec_filtered, all_code_routes)

    # 7. Remove known discrepancies from missing count
    known_in_missing = missing & KNOWN_MISSING
    actionable_missing = missing - KNOWN_MISSING

    # 8. Report
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

    # 9. Exit code
    if python_in_spec:
        print(f"\nWARNING: {len(python_in_spec)} Python service route(s) found "
              "in the OpenAPI spec. These services have their own specs and "
              "should be removed from the Node.js OpenAPI spec.")

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
