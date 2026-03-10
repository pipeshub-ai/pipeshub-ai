# ruff: noqa
"""
Databricks DataSource Code Generator

Generates a comprehensive DatabricksDataSource class by introspecting the
databricks-sdk WorkspaceClient at build time. Covers all workspace-level API
services (128+ services, 700+ methods).

Unlike the OpenAPI-based generators (jira.py, etc.), this generator wraps the
official databricks-sdk Python package since Databricks does not publish public
OpenAPI specs. It follows the Snowflake generator pattern (explicit parameter
signatures, typed methods, standardized response wrapping) but discovers
endpoints via SDK introspection rather than hardcoded definitions.

Usage:
    python databricks.py                         # Generate with defaults
    python databricks.py --include-wait          # Include _and_wait / wait_* helpers
    python databricks.py --include-legacy        # Include *_legacy service variants
    python databricks.py -o /custom/path.py      # Custom output path

Generated output goes to:
    backend/python/app/sources/external/databricks/databricks.py
"""

import argparse
import inspect
import keyword
import re
import sys
import textwrap
import typing
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Prevent this script (databricks.py) from shadowing the installed databricks package.
# Remove the script's own directory from sys.path before importing the SDK.
_script_dir = str(Path(__file__).resolve().parent)
_orig_path = sys.path[:]
sys.path = [p for p in sys.path if str(Path(p).resolve()) != _script_dir]
try:
    from databricks.sdk import WorkspaceClient # type: ignore
except ImportError:
    print("ERROR: databricks-sdk is required. Install with: pip install databricks-sdk")
    sys.exit(1)
finally:
    sys.path = _orig_path


# ---------------------------------------------------------------------------
# Type annotation simplification
# ---------------------------------------------------------------------------

# Python types that pass through unchanged
_BASIC_TYPES = {"str", "int", "float", "bool", "bytes", "None", "Any"}

# SDK type names we know should be treated as enum strings
_KNOWN_ENUMS: Set[str] = set()  # populated at discovery time


def _simplify_type_str(raw: str) -> str:
    """Simplify a type annotation string for the generated DataSource.

    SDK-specific types (e.g. AutoScale, ClusterSpec) become Dict[str, Any]
    since the SDK also accepts plain dicts for these. Basic Python types
    are preserved as-is.
    """
    s = raw.strip().strip("'\"")

    # Basic types
    if s in _BASIC_TYPES:
        return s

    # Optional[X]
    m = re.match(r"^Optional\[(.+)\]$", s)
    if m:
        inner = _simplify_type_str(m.group(1))
        return f"Optional[{inner}]"

    # Union[X, Y, ...]
    m = re.match(r"^Union\[(.+)\]$", s)
    if m:
        parts = _split_type_args(m.group(1))
        non_none = [p for p in parts if p.strip() != "None"]
        simplified = [_simplify_type_str(p) for p in non_none]
        if len(simplified) == 1:
            return f"Optional[{simplified[0]}]"
        return f"Union[{', '.join(simplified)}]"

    # List[X]
    m = re.match(r"^List\[(.+)\]$", s)
    if m:
        inner = _simplify_type_str(m.group(1))
        return f"List[{inner}]"

    # Iterator[X]  (return types)
    m = re.match(r"^Iterator\[(.+)\]$", s)
    if m:
        inner = _simplify_type_str(m.group(1))
        return f"Iterator[{inner}]"

    # Wait[X]  (return types from async SDK methods)
    m = re.match(r"^Wait\[(.+)\]$", s)
    if m:
        return "Dict[str, Any]"

    # Dict[K, V] — keep as-is
    m = re.match(r"^Dict\[(.+)\]$", s)
    if m:
        return s

    # BinaryIO → bytes
    if s in ("BinaryIO", "IO[bytes]"):
        return "bytes"

    # Callable → skip (handled at param level)
    if s.startswith("Callable"):
        return "__SKIP__"

    # datetime.timedelta → float (seconds)
    if "timedelta" in s:
        return "float"

    # requests.models.Response → Dict[str, Any]
    if "Response" in s and "requests" in s:
        return "Dict[str, Any]"

    # FieldMask → str
    if s == "FieldMask":
        return "str"

    # Anything else is an SDK-specific type → Dict[str, Any]
    return "Dict[str, Any]"


def _split_type_args(s: str) -> List[str]:
    """Split comma-separated type args respecting bracket nesting."""
    parts: List[str] = []
    depth = 0
    current: List[str] = []
    for ch in s:
        if ch in ("(", "["):
            depth += 1
            current.append(ch)
        elif ch in (")", "]"):
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return parts


# ---------------------------------------------------------------------------
# Parameter and method extraction
# ---------------------------------------------------------------------------

_PY_RESERVED = set(keyword.kwlist) | {"from", "global", "async", "await", "None", "self", "cls"}


def _sanitize_name(name: str) -> str:
    """Make a parameter name safe for Python."""
    n = name.replace("-", "_").replace(".", "_")
    if n in _PY_RESERVED or keyword.iskeyword(n):
        n = f"{n}_"
    return n


def _type_to_str(ann: Any) -> str:
    """Convert a type annotation object to a clean string representation."""
    if ann is inspect.Parameter.empty or ann is None:
        return "Any"
    if isinstance(ann, str):
        return ann

    # Handle basic types directly
    _basic_map = {str: "str", int: "int", float: "float", bool: "bool", bytes: "bytes", type(None): "None"}
    if ann in _basic_map:
        return _basic_map[ann]

    # Use typing-level introspection for generic types
    origin = getattr(ann, "__origin__", None)
    args = getattr(ann, "__args__", None)

    # typing.Optional[X] = Union[X, None]
    if origin is typing.Union:
        non_none = [a for a in (args or ()) if a is not type(None)]
        if len(non_none) == 1:
            return f"Optional[{_type_to_str(non_none[0])}]"
        parts = [_type_to_str(a) for a in non_none]
        return f"Union[{', '.join(parts)}]"

    # List[X]
    if origin is list:
        if args:
            return f"List[{_type_to_str(args[0])}]"
        return "List[Any]"

    # Dict[K, V]
    if origin is dict:
        if args and len(args) == 2:
            return f"Dict[{_type_to_str(args[0])}, {_type_to_str(args[1])}]"
        return "Dict[str, Any]"

    # Iterator[X]
    if origin in (typing.Iterator, getattr(typing, "Iterator", None)):
        if args:
            return f"Iterator[{_type_to_str(args[0])}]"
        return "Iterator[Any]"

    # Check for collections.abc.Iterator
    import collections.abc
    if origin is collections.abc.Iterator:
        if args:
            return f"Iterator[{_type_to_str(args[0])}]"
        return "Iterator[Any]"

    # For any other class type, return its qualified name
    if isinstance(ann, type):
        module = getattr(ann, "__module__", "")
        name = getattr(ann, "__qualname__", getattr(ann, "__name__", str(ann)))
        if module and "databricks" in module:
            return name  # SDK type name, will be simplified later
        if module == "builtins":
            return name
        return name

    # Fallback: stringify and clean up
    s = str(ann)
    s = s.replace("typing.", "").replace("collections.abc.", "")
    # Clean up <class 'X'> patterns
    s = re.sub(r"<class '([^']+)'>", r"\1", s)
    return s


def _get_annotation_str(param: inspect.Parameter, hints: dict, name: str) -> str:
    """Get a string representation of a parameter's type annotation."""
    # Try type hints first (resolved actual types)
    if name in hints:
        ann = hints[name]
        if ann is inspect.Parameter.empty:
            return "Any"
        return _type_to_str(ann)

    # Fall back to signature annotation (may be string)
    ann = param.annotation
    if ann is inspect.Parameter.empty:
        return "Any"
    if isinstance(ann, str):
        return ann
    return _type_to_str(ann)


def _format_default(val: Any) -> str:
    """Format a default value for code generation."""
    if val is None:
        return "None"
    if val is inspect.Parameter.empty:
        return "None"
    if isinstance(val, bool):
        return str(val)
    if isinstance(val, str):
        return repr(val)
    if isinstance(val, (int, float)):
        return str(val)
    # timedelta → seconds as float
    if hasattr(val, "total_seconds"):
        return str(val.total_seconds())
    return "None"


def _extract_method_info(
    method: Any,
    service_name: str,
) -> Optional[Dict[str, Any]]:
    """Extract parameter and return type info from an SDK method."""
    try:
        sig = inspect.signature(method)
    except (ValueError, TypeError):
        return None

    # Try to get type hints (may fail for some methods)
    try:
        hints = typing.get_type_hints(method)
    except Exception:
        hints = {}

    # Extract return type
    ret_ann = hints.get("return", sig.return_annotation)
    if ret_ann is inspect.Parameter.empty:
        ret_str = "Any"
    elif isinstance(ret_ann, str):
        ret_str = ret_ann
    else:
        ret_str = _type_to_str(ret_ann)
    is_iterator = "Iterator[" in ret_str or "Iterator" == ret_str
    is_wait = "Wait[" in ret_str

    params: List[Dict[str, Any]] = []
    for p_name, param in sig.parameters.items():
        if p_name == "self":
            continue

        # Skip **kwargs and *args
        if param.kind in (
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        ):
            continue

        ann_str = _get_annotation_str(param, hints, p_name)
        simplified = _simplify_type_str(ann_str)

        # Skip Callable parameters (used in wait callbacks)
        if simplified == "__SKIP__":
            continue

        has_default = param.default is not inspect.Parameter.empty
        default_val = param.default if has_default else None
        required = not has_default

        # If the type is already Optional but has no default, make it optional
        if simplified.startswith("Optional[") and required:
            # SDK marks some Optional params without defaults — treat as optional
            required = False
            has_default = True
            default_val = None

        safe_name = _sanitize_name(p_name)

        params.append({
            "name": p_name,
            "safe_name": safe_name,
            "type": simplified,
            "required": required,
            "has_default": has_default,
            "default": default_val,
        })

    docstring = inspect.getdoc(method) or ""
    # Take first paragraph only (before double newline)
    first_para = docstring.split("\n\n")[0].replace("\n", " ").strip()
    if len(first_para) > 200:
        first_para = first_para[:197] + "..."

    return {
        "params": params,
        "docstring": first_para,
        "return_type": ret_str,
        "is_iterator": is_iterator,
        "is_wait": is_wait,
    }


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class DatabricksDataSourceGenerator:
    """Generates DatabricksDataSource by introspecting databricks-sdk."""

    # Services to skip entirely (infrastructure, not API)
    SKIP_SERVICES = {"api_client", "config", "dbutils"}

    # Methods to skip (return external clients, not data)
    SKIP_METHODS = {
        "get_open_ai_client",
        "get_langchain_chat_open_ai_client",
    }

    def __init__(
        self,
        include_wait: bool = False,
        include_legacy: bool = False,
    ):
        self.include_wait = include_wait
        self.include_legacy = include_legacy
        self.services: List[Dict[str, Any]] = []
        self.total_methods = 0
        self.skipped_methods = 0

    def discover(self) -> None:
        """Discover all services and methods from WorkspaceClient."""
        print("🔍 Discovering services from databricks-sdk WorkspaceClient...")

        for svc_name in sorted(dir(WorkspaceClient)):
            if svc_name.startswith("_"):
                continue
            if svc_name in self.SKIP_SERVICES:
                continue
            if not self.include_legacy and svc_name.endswith("_legacy"):
                continue

            member = getattr(WorkspaceClient, svc_name, None)
            if not isinstance(member, property):
                continue

            fget = member.fget
            if not fget:
                continue

            try:
                hints = typing.get_type_hints(fget)
            except Exception:
                continue
            ret_type = hints.get("return")
            if not ret_type or not isinstance(ret_type, type):
                continue

            methods = self._extract_service_methods(ret_type, svc_name)
            if methods:
                self.services.append({
                    "name": svc_name,
                    "class_name": ret_type.__name__,
                    "methods": methods,
                })
                self.total_methods += len(methods)

        print(f"✅ Discovered {len(self.services)} services, {self.total_methods} methods")
        if self.skipped_methods:
            print(f"   (skipped {self.skipped_methods} wait/legacy/special methods)")

    def _extract_service_methods(
        self, cls: type, svc_name: str
    ) -> List[Dict[str, Any]]:
        """Extract all public methods from a service API class."""
        methods = []
        for m_name in sorted(dir(cls)):
            if m_name.startswith("_"):
                continue

            # Skip wait helpers unless requested
            if not self.include_wait:
                if m_name.startswith("wait_"):
                    self.skipped_methods += 1
                    continue
                if m_name.endswith("_and_wait"):
                    self.skipped_methods += 1
                    continue

            # Skip known non-data methods
            if m_name in self.SKIP_METHODS:
                self.skipped_methods += 1
                continue

            method = getattr(cls, m_name, None)
            if not callable(method) or isinstance(method, type):
                continue

            info = _extract_method_info(method, svc_name)
            if info is None:
                continue

            info["name"] = m_name
            methods.append(info)

        return methods

    # -------------------------------------------------------------------
    # Code generation
    # -------------------------------------------------------------------

    def generate(self) -> str:
        """Generate the complete DatabricksDataSource Python source."""
        self.discover()

        parts: List[str] = []
        parts.append(self._gen_file_header())
        parts.append(self._gen_class_header())

        for svc in self.services:
            parts.append(self._gen_service_section(svc))

        return "\n".join(parts) + "\n"

    def _gen_file_header(self) -> str:
        return textwrap.dedent(f'''\
            # ruff: noqa
            """
            Databricks DataSource — Auto-generated SDK wrapper

            Generated by introspecting databricks-sdk v0.85.0 WorkspaceClient.
            Covers {len(self.services)} services and {self.total_methods} methods.

            All methods return DatabricksResponse for consistent handling.
            SDK dataclass objects are automatically serialized to dicts.
            Iterator results are materialized to lists.

            DO NOT EDIT — regenerate with:
                python code-generator/databricks.py
            """

            import logging
            from typing import Any, Dict, Iterator, List, Optional, Union

            from app.sources.client.databricks.databricks import DatabricksClient, DatabricksResponse

            logger = logging.getLogger(__name__)


            def _serialize(obj: Any) -> Any:
                """Serialize SDK objects to JSON-compatible Python types."""
                if obj is None:
                    return None
                if isinstance(obj, (str, int, float, bool)):
                    return obj
                if isinstance(obj, (list, tuple)):
                    return [_serialize(item) for item in obj]
                if isinstance(obj, dict):
                    return {{str(k): _serialize(v) for k, v in obj.items()}}
                if hasattr(obj, "as_dict"):
                    return obj.as_dict()
                if hasattr(obj, "__dict__"):
                    return {{k: _serialize(v) for k, v in obj.__dict__.items() if not k.startswith("_")}}
                return str(obj)

        ''')

    def _gen_class_header(self) -> str:
        return textwrap.dedent(f'''\
            class DatabricksDataSource:
                """Databricks SDK DataSource — auto-generated wrapper.

                Wraps the official databricks-sdk WorkspaceClient, providing typed async
                methods for all {len(self.services)} workspace API services ({self.total_methods} methods).

                Each method:
                - Accepts explicit, typed parameters (no **kwargs)
                - Delegates to the corresponding SDK service method
                - Returns DatabricksResponse with serialized data or error info
                - Automatically serializes SDK dataclass objects to dicts
                - Materializes iterators to lists

                Usage::

                    client = DatabricksClient.build_from_services(logger, config_service)
                    ds = DatabricksDataSource(client)
                    resp = await ds.clusters_list()
                    if resp.success:
                        for cluster in resp.data:
                            print(cluster["cluster_name"])
                """

                def __init__(self, client: DatabricksClient) -> None:
                    """Initialize with DatabricksClient.

                    Args:
                        client: DatabricksClient instance (IClient) with configured authentication
                    """
                    self._client = client
                    sdk_client = client.get_client()
                    self._ws = sdk_client.get_workspace_client()

                def get_data_source(self) -> "DatabricksDataSource":
                    """Return the data source instance."""
                    return self

                def get_client(self) -> DatabricksClient:
                    """Return the underlying DatabricksClient."""
                    return self._client
        ''')

    def _gen_service_section(self, svc: Dict[str, Any]) -> str:
        lines: List[str] = []
        svc_name = svc["name"]
        class_name = svc["class_name"]
        num = len(svc["methods"])

        lines.append(f"    # {'=' * 76}")
        lines.append(f"    # {svc_name.upper().replace('_', ' ')} ({class_name}) — {num} methods")
        lines.append(f"    # {'=' * 76}")
        lines.append("")

        for method_info in svc["methods"]:
            lines.append(self._gen_method(svc_name, method_info))

        return "\n".join(lines)

    def _gen_method(self, svc_name: str, info: Dict[str, Any]) -> str:
        method_name = f"{svc_name}_{info['name']}"
        params = info["params"]
        docstring = info["docstring"]
        is_iterator = info["is_iterator"]
        is_wait = info["is_wait"]

        # --- Signature ---
        sig_parts: List[str] = ["self"]

        # Required parameters first
        for p in params:
            if p["required"]:
                sig_parts.append(f"{p['safe_name']}: {p['type']}")

        # Optional parameters
        for p in params:
            if not p["required"]:
                t = p["type"]
                if not t.startswith("Optional["):
                    t = f"Optional[{t}]"
                default = _format_default(p["default"])
                sig_parts.append(f"{p['safe_name']}: {t} = {default}")

        # Format signature
        if len(sig_parts) <= 3 and all(len(p) < 40 for p in sig_parts):
            sig = ", ".join(sig_parts)
            sig_line = f"    async def {method_name}({sig}) -> DatabricksResponse:"
        else:
            formatted = ",\n        ".join(sig_parts)
            sig_line = f"    async def {method_name}(\n        {formatted},\n    ) -> DatabricksResponse:"

        # --- Docstring ---
        if docstring:
            doc = f'        """{docstring}"""'
        else:
            doc = f'        """{svc_name}.{info["name"]}"""'

        # --- Build kwargs ---
        kwargs_lines: List[str] = []
        kwargs_lines.append("        kwargs: Dict[str, Any] = {}")

        for p in params:
            if p["required"]:
                # Use original SDK param name as key
                if p["safe_name"] != p["name"]:
                    kwargs_lines.append(f"        kwargs['{p['name']}'] = {p['safe_name']}")
                else:
                    kwargs_lines.append(f"        kwargs['{p['name']}'] = {p['safe_name']}")
            else:
                kwargs_lines.append(f"        if {p['safe_name']} is not None:")
                if p["safe_name"] != p["name"]:
                    kwargs_lines.append(f"            kwargs['{p['name']}'] = {p['safe_name']}")
                else:
                    kwargs_lines.append(f"            kwargs['{p['name']}'] = {p['safe_name']}")

        # --- SDK call ---
        if is_iterator:
            call_line = f"            result = list(self._ws.{svc_name}.{info['name']}(**kwargs))"
        else:
            call_line = f"            result = self._ws.{svc_name}.{info['name']}(**kwargs)"

        # For Wait objects, extract the response
        if is_wait:
            serialize_line = "            data = _serialize(getattr(result, 'response', result))"
        else:
            serialize_line = "            data = _serialize(result)"

        # --- Full method ---
        method_lines = [
            sig_line,
            doc,
            *kwargs_lines,
            "        try:",
            call_line,
            serialize_line,
            "            return DatabricksResponse(success=True, data=data)",
            "        except Exception as e:",
            "            return DatabricksResponse(",
            f"                success=False, error=type(e).__name__, message=str(e)",
            "            )",
            "",
        ]

        return "\n".join(method_lines)

    # -------------------------------------------------------------------
    # Output
    # -------------------------------------------------------------------

    def save_to_file(self, output_path: Optional[str] = None) -> str:
        """Generate and write the DataSource file. Returns the path."""
        if output_path is None:
            script_dir = Path(__file__).parent
            out_dir = script_dir.parent / "app" / "sources" / "external" / "databricks"
            out_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(out_dir / "databricks.py")

        code = self.generate()
        Path(output_path).write_text(code, encoding="utf-8")

        print(f"\n✅ Generated DatabricksDataSource")
        print(f"   Services : {len(self.services)}")
        print(f"   Methods  : {self.total_methods}")
        print(f"   Output   : {output_path}")

        # Print top 25 services by method count
        print(f"\n📊 Top services by method count:")
        for svc in sorted(self.services, key=lambda s: len(s["methods"]), reverse=True)[:25]:
            print(f"   {svc['name']:45s} {len(svc['methods']):3d} methods  ({svc['class_name']})")

        # Print category summary
        categories: Dict[str, int] = {}
        cat_map = {
            "compute": ["clusters", "cluster_policies", "instance_pools", "instance_profiles",
                        "libraries", "command_execution", "global_init_scripts",
                        "policy_compliance_for_clusters", "policy_families"],
            "jobs_workflows": ["jobs", "policy_compliance_for_jobs"],
            "sql": ["warehouses", "statement_execution", "alerts", "alerts_v2",
                     "dashboards", "dashboard_widgets", "data_sources", "dbsql_permissions",
                     "queries", "query_history", "query_visualizations", "redash_config"],
            "unity_catalog": ["catalogs", "schemas", "tables", "volumes", "functions",
                               "connections", "credentials", "external_locations",
                               "metastores", "storage_credentials", "grants",
                               "system_schemas", "table_constraints", "workspace_bindings",
                               "artifact_allowlists", "model_versions", "registered_models",
                               "online_tables", "policies", "resource_quotas", "rfa",
                               "external_lineage", "external_metadata",
                               "entity_tag_assignments", "quality_monitors", "quality_monitor_v2"],
            "file_management": ["dbfs", "files"],
            "workspace": ["workspace", "repos", "git_credentials", "workspace_conf",
                           "workspace_entity_tag_assignments", "workspace_iam_v2",
                           "workspace_settings_v2"],
            "ml": ["experiments", "model_registry", "feature_engineering", "feature_store",
                     "materialized_features", "forecasting"],
            "serving": ["serving_endpoints", "serving_endpoints_data_plane"],
            "vector_search": ["vector_search_endpoints", "vector_search_indexes"],
            "iam": ["users", "users_v2", "groups", "groups_v2", "service_principals",
                     "service_principals_v2", "permissions", "permission_migration",
                     "access_control", "account_access_control_proxy", "current_user",
                     "ip_access_lists", "token_management", "tokens",
                     "service_principal_secrets_proxy", "credentials_manager"],
            "delta_sharing": ["shares", "recipients", "recipient_activation",
                               "recipient_federation_policies", "providers"],
            "delta_live_tables": ["pipelines"],
            "dashboards_lakeview": ["lakeview", "lakeview_embedded", "genie"],
            "apps": ["apps", "apps_settings"],
            "secrets": ["secrets"],
            "settings": ["settings", "notification_destinations"],
            "clean_rooms": ["clean_rooms", "clean_room_assets", "clean_room_asset_revisions",
                             "clean_room_auto_approval_rules", "clean_room_task_runs"],
            "marketplace": ["consumer_fulfillments", "consumer_installations", "consumer_listings",
                             "consumer_personalization_requests", "consumer_providers",
                             "provider_exchange_filters", "provider_exchanges", "provider_files",
                             "provider_listings", "provider_personalization_requests",
                             "provider_provider_analytics_dashboards", "provider_providers"],
            "tags": ["tag_policies"],
            "database": ["database"],
            "agent_bricks": ["agent_bricks"],
            "postgres": ["postgres"],
            "data_quality": ["data_quality"],
            "temporary_credentials": ["temporary_path_credentials", "temporary_table_credentials"],
        }

        svc_names = {s["name"] for s in self.services}
        for cat, svc_list in cat_map.items():
            count = sum(
                len(s["methods"])
                for s in self.services
                if s["name"] in svc_list
            )
            if count > 0:
                categories[cat] = count

        uncategorized = svc_names - {s for svcs in cat_map.values() for s in svcs}
        if uncategorized:
            unc_count = sum(
                len(s["methods"])
                for s in self.services
                if s["name"] in uncategorized
            )
            if unc_count:
                categories["other"] = unc_count
                print(f"\n⚠️  Uncategorized services: {', '.join(sorted(uncategorized))}")

        print(f"\n📋 Methods by category:")
        for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
            print(f"   {cat:30s} {count:4d} methods")

        return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate Databricks DataSource from SDK introspection"
    )
    parser.add_argument(
        "--output", "-o",
        help="Output file path (default: app/sources/external/databricks/databricks.py)",
    )
    parser.add_argument(
        "--include-wait",
        action="store_true",
        help="Include _and_wait and wait_* polling helper methods",
    )
    parser.add_argument(
        "--include-legacy",
        action="store_true",
        help="Include *_legacy API variants (deprecated)",
    )
    args = parser.parse_args()

    try:
        generator = DatabricksDataSourceGenerator(
            include_wait=args.include_wait,
            include_legacy=args.include_legacy,
        )
        generator.save_to_file(args.output)
        print("\n🎉 Done!")
        return 0
    except Exception as e:
        print(f"\n❌ Generation failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
