# ruff: noqa
"""
Zoom REST API Code Generator

Reads OpenAPI JSON specs from zoom_specs/ directory and generates a
ZoomDataSource class with async wrapper methods for all Zoom API endpoints.

Usage:
    python code-generator/zoom.py
    python code-generator/zoom.py --filename zoom.py

Output:
    app/sources/external/zoom/zoom.py
"""

import json
import keyword
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
SPEC_DIR = SCRIPT_DIR / "zoom_specs"


def _to_snake_case(name: str) -> str:
    """Convert camelCase/PascalCase to snake_case."""
    if not name:
        return "method"
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = s.replace("-", "_")
    s = re.sub(r"[^0-9a-zA-Z_]", "_", s)
    s = re.sub(r"_+", "_", s).lower().strip("_")
    if s and s[0].isdigit():
        s = f"_{s}"
    return s or "method"


_PYTHON_BUILTINS = frozenset({
    "format", "license", "type", "input", "id", "hash", "range", "list",
    "dict", "set", "map", "filter", "open", "print", "next", "object",
    "property", "staticmethod", "classmethod", "super", "abs", "all",
    "any", "bin", "bool", "bytes", "callable", "chr", "complex",
    "delattr", "dir", "divmod", "enumerate", "eval", "exec", "float",
    "frozenset", "getattr", "globals", "hasattr", "help", "hex", "int",
    "isinstance", "issubclass", "iter", "len", "locals", "max", "memoryview",
    "min", "oct", "ord", "pow", "repr", "reversed", "round", "setattr",
    "slice", "sorted", "str", "sum", "tuple", "vars", "zip",
})


def _sanitize_name(name: str) -> str:
    """Sanitize a string to be a valid Python identifier."""
    if not name:
        return "param"
    s = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    if s and s[0].isdigit():
        s = f"_{s}"
    s = re.sub(r"_+", "_", s).strip("_")
    if keyword.iskeyword(s) or s in ("self", "cls") or s in _PYTHON_BUILTINS:
        s = f"{s}_"
    return s or "param"


def _get_type_hint(schema: dict) -> str:
    """Map OpenAPI schema to Python type hint string."""
    t = schema.get("type", "")
    if t == "integer":
        return "int"
    if t == "boolean":
        return "bool"
    if t == "number":
        return "float"
    if t == "array":
        return "list[object]"
    if t == "object":
        return "dict[str, object]"
    return "str"


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class ZoomDataSourceGenerator:
    """Generates ZoomDataSource class from OpenAPI specs."""

    def __init__(self) -> None:
        self.generated_methods: List[Dict[str, str]] = []
        self._used_names: Set[str] = set()

    def _unique_method_name(self, base: str) -> str:
        """Ensure unique method name within the class."""
        name = base
        counter = 1
        while name in self._used_names:
            name = f"{base}_{counter}"
            counter += 1
        self._used_names.add(name)
        return name

    def _parse_parameters(self, params_list: list) -> tuple:
        """Parse OpenAPI parameters into path/query lists.

        Returns:
            (path_params, query_params) where each is a list of dicts
            with keys: original_name, py_name, type, description, required
        """
        path_params: List[dict] = []
        query_params: List[dict] = []
        seen: Set[str] = set()

        for param in params_list:
            if not isinstance(param, dict):
                continue
            name = param.get("name")
            if not name:
                continue

            py_name = _sanitize_name(name)
            # Deduplicate
            if py_name in seen:
                idx = 1
                while f"{py_name}_{idx}" in seen:
                    idx += 1
                py_name = f"{py_name}_{idx}"
            seen.add(py_name)

            schema = param.get("schema", {})
            type_hint = _get_type_hint(schema)
            raw_desc = (param.get("description") or "").replace("\r", "").replace("\n", " ").strip()
            description = raw_desc[:120].rstrip()
            required = param.get("required", False)
            location = param.get("in", "query")

            entry = {
                "original_name": name,
                "py_name": py_name,
                "type": type_hint,
                "description": description[:120],
                "required": required,
            }

            if location == "path":
                entry["required"] = True
                path_params.append(entry)
            elif location == "query":
                query_params.append(entry)

        return path_params, query_params

    def _generate_method(
        self,
        method_name: str,
        http_method: str,
        path: str,
        summary: str,
        path_params: list,
        query_params: list,
        has_body: bool,
    ) -> str:
        """Generate a single async method."""
        lines: List[str] = []

        # --- Signature ---
        sig_parts = ["self"]

        # Required path params
        for p in path_params:
            sig_parts.append(f"{p['py_name']}: str")

        # Body param
        if has_body:
            sig_parts.append("body: dict[str, Any]")

        # Optional query params — check for bool
        optional_parts: List[str] = []
        has_bool_optional = False
        for p in query_params:
            t = p["type"]
            optional_parts.append(f"{p['py_name']}: {t} | None = None")
            if t == "bool":
                has_bool_optional = True

        if has_bool_optional and optional_parts:
            sig_parts.append("*")

        sig_parts.extend(optional_parts)

        sig_str = ",\n        ".join(sig_parts)
        lines.append(f"    async def {method_name}(\n        {sig_str}\n    ) -> ZoomResponse:")

        # --- Docstring ---
        clean_summary = (summary or method_name).replace("\r", "").replace('"', "'").rstrip()
        lines.append(f'        """{clean_summary}')
        lines.append("")
        lines.append(f"        HTTP {http_method} {path}")

        if path_params or query_params or has_body:
            lines.append("")
            lines.append("        Args:")
            for p in path_params:
                desc = (p['description'] or 'Path parameter').rstrip()
                lines.append(f"            {p['py_name']}: {desc}")
            if has_body:
                lines.append("            body: Request body payload")
            for p in query_params:
                desc = (p['description'] or 'Query parameter').rstrip()
                lines.append(f"            {p['py_name']}: {desc}")

        lines.append("")
        lines.append("        Returns:")
        lines.append("            ZoomResponse with operation result")
        lines.append('        """')

        # --- Query params dict ---
        has_query = len(query_params) > 0
        if has_query:
            lines.append("        query_params: dict[str, Any] = {}")
            for p in query_params:
                lines.append(f"        if {p['py_name']} is not None:")
                if p["type"] == "bool":
                    lines.append(f"            query_params['{p['original_name']}'] = str({p['py_name']}).lower()")
                elif p["type"] == "int":
                    lines.append(f"            query_params['{p['original_name']}'] = str({p['py_name']})")
                else:
                    lines.append(f"            query_params['{p['original_name']}'] = {p['py_name']}")
            lines.append("")

        # --- URL construction ---
        # Replace {original_name} with {py_name} in path
        safe_path = path
        for p in path_params:
            safe_path = safe_path.replace(
                f"{{{p['original_name']}}}", f"{{{p['py_name']}}}"
            )

        if path_params:
            format_args = ", ".join(
                f"{p['py_name']}={p['py_name']}" for p in path_params
            )
            lines.append(f'        url = self.base_url + "{safe_path}".format({format_args})')
        else:
            lines.append(f'        url = self.base_url + "{safe_path}"')

        # --- Body ---
        if has_body:
            pass  # body already in signature
        # If no body param, declare body as None for methods that don't need it

        # --- Request construction ---
        lines.append("")
        lines.append("        try:")
        lines.append("            request = HTTPRequest(")
        lines.append(f'                method="{http_method}",')
        lines.append("                url=url,")
        lines.append('                headers={"Content-Type": "application/json"},')
        if has_query:
            lines.append("                query=query_params,")
        if has_body:
            lines.append("                body=body,")
        lines.append("            )")
        lines.append("            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]")
        lines.append("            response_data = response.json() if response.text() else None")
        lines.append("            return ZoomResponse(")
        lines.append("                success=response.status < HTTP_ERROR_THRESHOLD,")
        lines.append("                data=response_data,")
        lines.append(f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"')
        lines.append("            )")
        lines.append("        except Exception as e:")
        lines.append(f'            return ZoomResponse(success=False, error=str(e), message="Failed to execute {method_name}")')

        self.generated_methods.append({
            "name": method_name,
            "endpoint": path,
            "method": http_method,
            "description": clean_summary[:80],
        })

        return "\n".join(lines)

    def _load_all_endpoints(self) -> List[dict]:
        """Load all endpoints from zoom_specs/ JSON files."""
        endpoints: List[dict] = []

        for spec_file in sorted(SPEC_DIR.glob("*.json")):
            try:
                spec = _load_json(spec_file)
            except Exception:
                print(f"  Skipping bad JSON: {spec_file.name}")
                continue

            paths = spec.get("paths", {}) or {}
            for raw_path, path_item in paths.items():
                if not isinstance(path_item, dict):
                    continue
                for verb in ("get", "post", "put", "delete", "patch"):
                    if verb not in path_item:
                        continue
                    operation = path_item[verb]
                    if not isinstance(operation, dict):
                        continue

                    endpoints.append({
                        "operationId": operation.get("operationId", ""),
                        "summary": (operation.get("summary") or "").strip(),
                        "path": raw_path,
                        "method": verb.upper(),
                        "parameters": operation.get("parameters", []) or [],
                        "has_body": "requestBody" in operation,
                    })

        return endpoints

    def generate_zoom_datasource(self) -> str:
        """Generate the complete ZoomDataSource class from specs."""
        endpoints = self._load_all_endpoints()

        class_lines = [
            '"""',
            "Zoom REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from Zoom OpenAPI specifications.",
            "Uses HTTP client for direct REST API interactions.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.http.http_request import HTTPRequest",
            "from app.sources.client.zoom.zoom import ZoomClient, ZoomResponse",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class ZoomDataSource:",
            '    """Zoom REST API DataSource',
            "",
            "    Auto-generated async wrapper methods for Zoom REST API operations",
            "    covering Users, Meetings, Phone, Team Chat, Calendar, Rooms,",
            "    Accounts, Clips, Whiteboard, Mail, and more.",
            "",
            "    All methods return ZoomResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: ZoomClient) -> None:",
            '        """Initialize with ZoomClient.',
            "",
            "        Args:",
            "            client: ZoomClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'ZoomDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> ZoomClient:",
            '        """Return the underlying ZoomClient."""',
            "        return self._client",
            "",
        ]

        # Generate methods from all specs
        for ep in endpoints:
            op_id = ep["operationId"]
            raw_path = ep["path"]
            http_method = ep["method"]
            summary = ep["summary"]
            has_body = ep["has_body"]

            # Derive method name
            if op_id:
                base_name = _to_snake_case(op_id)
            else:
                clean_path = raw_path.strip("/").replace("/", "_").replace("{", "").replace("}", "").replace("-", "_")
                base_name = f"{http_method.lower()}_{clean_path}"

            method_name = self._unique_method_name(base_name)

            # Parse parameters
            path_params, query_params = self._parse_parameters(ep["parameters"])

            # Generate method
            method_code = self._generate_method(
                method_name=method_name,
                http_method=http_method,
                path=raw_path,
                summary=summary,
                path_params=path_params,
                query_params=query_params,
                has_body=has_body,
            )
            class_lines.append(method_code)
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Zoom datasource to a file."""
        if filename is None:
            filename = "zoom.py"

        zoom_dir = SCRIPT_DIR.parent / "app" / "sources" / "external" / "zoom"
        zoom_dir.mkdir(parents=True, exist_ok=True)

        full_path = zoom_dir / filename
        class_code = self.generate_zoom_datasource()
        # Strip trailing whitespace from every line (OpenAPI descriptions may contain it)
        clean_lines = [line.rstrip() for line in class_code.split("\n")]
        full_path.write_text("\n".join(clean_lines), encoding="utf-8")

        print(f"Generated Zoom data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by spec category
        categories: Dict[str, int] = {}
        for method in self.generated_methods:
            endpoint = method["endpoint"]
            # Extract first path segment as category
            parts = endpoint.strip("/").split("/")
            category = parts[0] if parts else "other"
            categories[category] = categories.get(category, 0) + 1

        print(f"\nMethods by API category:")
        for category, count in sorted(categories.items(), key=lambda x: -x[1]):
            print(f"  - {category}: {count}")


def main() -> int:
    """Main function for Zoom data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Zoom REST API data source from OpenAPI specs"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    if not SPEC_DIR.exists():
        print(f"Spec directory not found: {SPEC_DIR}")
        return 1

    spec_count = len(list(SPEC_DIR.glob("*.json")))
    if spec_count == 0:
        print(f"No JSON specs found in {SPEC_DIR}")
        return 1

    print(f"Found {spec_count} spec files in {SPEC_DIR}")

    try:
        generator = ZoomDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Zoom data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
