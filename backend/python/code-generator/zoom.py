#!/usr/bin/env python3
"""
zoom.py - unified Zoom datasource generator (single-file ZoomDataSource)

Generates:
  - backend/python/app/sources/external/zoom/zoom.py
  - backend/python/app/sources/external/zoom/example.py
  - backend/python/app/sources/external/zoom/example_build_from_services.py

Behavior:
  - Reads all .json files from --spec-dir (default backend/python/code-generator/zoom_specs)
  - Preserves the order of files returned by Path.iterdir() (this matches "order JSON files appear in zoom_specs")
  - Combines endpoints into a single class ZoomDataSource, grouped by source spec
  - Method names generated from operationId, summary or path+method fallback
  - Example files demonstrate using ZoomClient (token/config) and services builder pattern
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional, Tuple

# ------------------ Utilities ------------------


def safe_load_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8", errors="replace")
    # sanitize weird control characters that break JSON
    cleaned = "".join(
        ch for ch in text if ch == "\n" or ch == "\r" or (32 <= ord(ch) < 127) or ord(ch) >= 160
    )
    return json.loads(cleaned)


def to_snake_case(s: str) -> str:
    s = re.sub(r"[^\w]+", "_", s)
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", s)
    s2 = re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1)
    return re.sub(r"__+", "_", s2).strip("_").lower()


def to_pascal_case(s: str) -> str:
    parts = re.split(r"[^0-9a-zA-Z]+", s)
    return "".join(p.capitalize() for p in parts if p)


def sanitize_param_name(name: str) -> str:
    if not name:
        return "param"
    name = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    if name[0].isdigit():
        name = "param_" + name
    reserved = {
        "self",
        "async",
        "await",
        "None",
        "True",
        "False",
        "from",
        "class",
        "def",
        "in",
        "type",
    }
    if name in reserved:
        return name + "_param"
    return name


def map_schema_type(schema: Any) -> str:
    # conservative mapping for typing hints
    if not isinstance(schema, dict):
        return "Any"
    t = schema.get("type", "string")
    if t == "string":
        return "str"
    if t == "integer":
        return "int"
    if t == "number":
        return "float"
    if t == "boolean":
        return "bool"
    if t == "array":
        items = schema.get("items", {"type": "string"})
        return f"List[{map_schema_type(items)}]"
    if t == "object":
        return "Dict[str, Any]"
    return "Any"


# ------------------ Spec parser ------------------


class SpecParser:
    def __init__(self, raw: Any):
        self.raw = raw

    def parse(self) -> List[Dict[str, Any]]:
        data = self.raw
        if isinstance(data, list):
            return self._parse_list(data)
        if isinstance(data, dict) and "paths" in data:
            return self._parse_paths(data["paths"])
        # fallback: try common keys
        if isinstance(data, dict) and ("endpoints" in data or "methods" in data or "resources" in data):
            arr = data.get("endpoints") or data.get("methods") or data.get("resources")
            return self._parse_list(arr or [])
        # some Zoom specs are top-level mapping of endpoints (rare)
        if isinstance(data, dict):
            # attempt to find endpoints by scanning for objects with 'path' or 'url'
            flat = []
            for v in data.values():
                if isinstance(v, dict) and ("path" in v or "url" in v):
                    flat.append(v)
            if flat:
                return self._parse_list(flat)
        raise ValueError("Unsupported spec structure (not list or OpenAPI 'paths')")

    def _parse_list(self, arr: List[Any]) -> List[Dict[str, Any]]:
        endpoints = []
        for item in arr:
            if not isinstance(item, dict):
                continue
            path = item.get("path") or item.get("endpoint") or item.get("url") or item.get("endpointUrl")
            if not path:
                continue
            method = (item.get("method") or item.get("httpMethod") or "GET").upper()
            endpoints.append(
                {
                    "path": path,
                    "method": method,
                    "operationId": item.get("operationId") or item.get("id") or "",
                    "summary": item.get("summary") or item.get("description") or "",
                    "description": item.get("description", ""),
                    "parameters": item.get("parameters", []) or [],
                    "requestBody": item.get("requestBody"),
                }
            )
        return endpoints

    def _parse_paths(self, paths: Dict[str, Any]) -> List[Dict[str, Any]]:
        endpoints = []
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, meta in methods.items():
                if method.lower() not in {"get", "post", "put", "patch", "delete", "options", "head"}:
                    continue
                meta = meta or {}
                endpoints.append(
                    {
                        "path": path,
                        "method": method.upper(),
                        "operationId": meta.get("operationId", "") or "",
                        "summary": meta.get("summary", "") or meta.get("description", ""),
                        "description": meta.get("description", ""),
                        "parameters": meta.get("parameters", []) or [],
                        "requestBody": meta.get("requestBody"),
                    }
                )
        return endpoints


# ------------------ Generator ------------------


class UnifiedZoomGenerator:
    def __init__(self):
        self.seen_method_names = set()

    def _method_name(self, ep: Dict[str, Any], feature_hint: str) -> str:
        op = (ep.get("operationId") or ep.get("summary") or f"{ep['method'].lower()}_{ep['path']}")
        op = re.sub(r"[{}]", "", op)
        name = to_snake_case(op)
        name = re.sub(r"__+", "_", name).strip("_")
        if not name:
            # fallback include feature hint
            name = f"{to_snake_case(feature_hint)}_{to_snake_case(ep['path'])}_{ep['method'].lower()}"
        base = name
        i = 1
        while name in self.seen_method_names:
            name = f"{base}_{i}"
            i += 1
        self.seen_method_names.add(name)
        return name

    def _extract_params(self, endpoint: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        path_params: List[Dict[str, Any]] = []
        query_params: List[Dict[str, Any]] = []
        for p in endpoint.get("parameters", []) or []:
            if not isinstance(p, dict):
                continue
            name = p.get("name")
            if not name:
                continue
            loc = p.get("in", "query")
            required = bool(p.get("required", False))
            schema = p.get("schema", {}) or {}
            ptype = map_schema_type(schema)
            sanitized = sanitize_param_name(name)
            entry = {"name": sanitized, "original": name, "required": required, "type": ptype, "in": loc}
            if loc == "path":
                path_params.append(entry)
            else:
                query_params.append(entry)
        # requestBody handling: attempt to read application/json schema
        body = endpoint.get("requestBody")
        body_schema = None
        if body and isinstance(body, dict):
            content = body.get("content", {}) or {}
            if "application/json" in content:
                body_schema = content["application/json"].get("schema", {"type": "object"})
            else:
                first = next(iter(content.values()), None)
                if first:
                    body_schema = first.get("schema", {"type": "object"})
                else:
                    body_schema = {"type": "object"}
        return path_params, query_params, body_schema

    def _build_signature(self, method_name: str, path_params, query_params, body_schema) -> str:
        parts = []
        # required path params first
        for p in path_params:
            if p["required"]:
                parts.append(f"{p['name']}: {p['type']}")
        # optional path params
        for p in path_params:
            if not p["required"]:
                parts.append(f"{p['name']}: Optional[{p['type']}] = None")
        # query params (optional)
        for q in query_params:
            parts.append(f"{q['name']}: Optional[{q['type']}] = None")
        # body
        if body_schema:
            parts.append("body: Optional[Dict[str, Any]] = None")
        parts.append("timeout: Optional[int] = None")
        if not parts:
            return f"    async def {method_name}(self) -> Dict[str, Any]:"
        # short flat signature if small
        if len(parts) <= 4:
            return f"    async def {method_name}(self, " + ", ".join(parts) + ") -> Dict[str, Any]:"
        joined = ",\n        ".join(parts)
        return f"    async def {method_name}(\n        {joined}\n    ) -> Dict[str, Any]:"

    def _build_docstring(self, ep: Dict[str, Any], params_docs: List[Dict[str, Any]]) -> str:
        summary = (ep.get("summary") or ep.get("description") or "").strip()
        doc_lines = ['        """']
        if summary:
            doc_lines.append(f"        {summary}")
            doc_lines.append("")
        doc_lines.append(f"        API: {ep['method']} {ep['path']}")
        doc_lines.append("")
        if params_docs:
            doc_lines.append("        Args:")
            for p in params_docs:
                req = "required" if p.get("required") else "optional"
                doc_lines.append(f"            {p['name']} ({p.get('type','Any')}, {req}): original param name `{p.get('original')}`")
            doc_lines.append("")
        doc_lines.append("        Returns:")
        doc_lines.append("            Dict[str, Any]: API response")
        doc_lines.append('        """')
        return "\n".join(doc_lines)

    def _build_method_body(self, ep: Dict[str, Any], path_params, query_params, body_schema) -> str:
        lines: List[str] = []
        path_template = ep["path"]
        # replace original path param names with sanitized names for f-string
        for p in path_params:
            path_template = path_template.replace("{" + p["original"] + "}", "{" + p["name"] + "}")
        lines.append(f'        endpoint = f"{{self._base_url}}{path_template}"')
        lines.append("")
        lines.append("        params: Dict[str, Any] = {}")
        for q in query_params:
            lines.append(f"        if {q['name']} is not None:")
            lines.append(f"            params['{q['original']}'] = {q['name']}")
        lines.append("")
        if body_schema:
            lines.append("        req_body: Optional[Dict[str, Any]] = body")
        else:
            lines.append("        req_body: Optional[Dict[str, Any]] = None")
        lines.append("")
        lines.append("        # execute request using provided client (IClient.request async)")
        lines.append("        resp = await self._rest_client.request(")
        lines.append(f'            "{ep["method"]}",')
        lines.append("            endpoint,")
        lines.append("            params=params,")
        lines.append("            body=req_body,")
        lines.append("            timeout=timeout")
        lines.append("        )")
        lines.append("")
        lines.append("        # status-code handling")
        lines.append("        status = getattr(resp, 'status_code', None) or getattr(resp, 'status', None)")
        lines.append("        if status == 204:")
        lines.append("            return {'status': 'no_content'}")
        lines.append("")
        lines.append("        # try to return JSON, fallback to text")
        lines.append("        try:")
        lines.append("            if callable(getattr(resp, 'json', None)):")
        lines.append("                maybe = resp.json()")
        lines.append("                if hasattr(maybe, '__await__'):")
        lines.append("                    return await maybe")
        lines.append("                return maybe")
        lines.append("            if isinstance(resp, dict):")
        lines.append("                return resp")
        lines.append("            if callable(getattr(resp, 'text', None)):")
        lines.append("                maybe = resp.text()")
        lines.append("                if hasattr(maybe, '__await__'):")
        lines.append("                    return await maybe")
        lines.append("                return maybe")
        lines.append("            return str(resp)")
        lines.append("        except Exception:")
        lines.append("            try:")
        lines.append("                return resp.json() if hasattr(resp, 'json') else {'raw': str(resp)}")
        lines.append("            except Exception:")
        lines.append("                return {'raw': str(resp)}")
        return "\n".join(lines)

    def generate_unified(self, spec_paths: List[Path], out_dir: Path, overwrite: bool) -> Tuple[bool, str]:
        """
        Generate one zoom.py containing ZoomDataSource with methods grouped by spec files order.
        """
        all_sections: List[Tuple[str, List[Dict[str, Any]]]] = []
        for sp in spec_paths:
            try:
                raw = safe_load_json(sp)
            except Exception as e:
                return False, f"Failed to parse {sp}: {e}"
            try:
                parsed = SpecParser(raw).parse()
            except Exception as e:
                return False, f"Failed to parse endpoints in {sp}: {e}"
            feature = re.sub(r"\.json$", "", sp.name, flags=re.IGNORECASE)
            all_sections.append((feature, parsed))

        # Build zoom.py contents
        header = [
            '"""',
            "zoom.py — Auto-generated unified Zoom datasource (single-class ZoomDataSource)",
            '"""',
            "",
            "from typing import Any, Dict, List, Optional",
            "import logging",
            "",
            "# NOTE: This file is auto-generated. Review & adjust auth/typing as needed.",
            "from app.sources.client.iclient import IClient  # type: ignore",
            "",
            "",
            "class ZoomDataSource:",
            "    def __init__(self, rest_client: IClient, base_url: str = 'https://api.zoom.us/v2', logger: Optional[logging.Logger] = None) -> None:",
            '        """',
            "        rest_client: IClient providing async request(method, url, params=None, body=None, timeout=None)",
            '        """',
            "        self._rest_client = rest_client",
            "        self._base_url = base_url.rstrip('/')",
            "        self._logger = logger",
            "",
        ]
        body_parts: List[str] = []
        # for example selection of first callable method
        first_method_name = None
        first_call_args = ""
        for feature, endpoints in all_sections:
            if not endpoints:
                continue
            # add section header comment
            body_parts.append(f"    # ===== {feature} =====")
            for ep in endpoints:
                try:
                    method_name = self._method_name(ep, feature)
                    if first_method_name is None:
                        first_method_name = method_name
                        # create naive first_call_args based on params: give placeholders
                        path_params, query_params, body_schema = self._extract_params(ep)
                        call_args = []
                        for p in path_params:
                            call_args.append(f"{p['name']}='REPLACE'")
                        for q in query_params[:2]:  # only 2 sample query params
                            call_args.append(f"{q['name']}=None")
                        if body_schema:
                            call_args.append("body={}")
                        first_call_args = ", ".join(call_args)
                    path_params, query_params, body_schema = self._extract_params(ep)
                    sig = self._build_signature(method_name, path_params, query_params, body_schema)
                    doc = self._build_docstring(ep, path_params + query_params)
                    body = self._build_method_body(ep, path_params, query_params, body_schema)
                    body_parts.append("\n".join([sig, doc, body, ""]))
                except Exception as e:
                    body_parts.append(f"    # Failed to generate for {ep.get('path')} {ep.get('method')}: {e}\n")
        if not first_method_name:
            first_method_name = "TODO_method"
            first_call_args = ""

        final_code = "\n".join(header) + "\n".join(body_parts) + "\n"

        # write zoom.py
        out_dir.mkdir(parents=True, exist_ok=True)
        zoom_py = out_dir / "zoom.py"
        if zoom_py.exists() and not overwrite:
            return False, f"Skipped (exists): {zoom_py}"
        zoom_py.write_text(final_code, encoding="utf-8")

        # write example.py and example_build_from_services.py using safe templates
        self._write_examples(out_dir, feature_list=[f for f, _ in all_sections], first_method=first_method_name, first_call_args=first_call_args, overwrite=overwrite)

        return True, f"Wrote {zoom_py} and examples"

    def _write_examples(self, out_dir: Path, feature_list: List[str], first_method: str, first_call_args: str, overwrite: bool) -> None:
        # Use Template.safe_substitute to avoid KeyError and to allow braces easily
        example_tpl = Template(
            r'''"""Example usage for the Zoom datasource.

This example demonstrates token-based usage via ZoomClient.build_with_config().
"""

import asyncio
from typing import Any, Dict, Optional

# NOTE: Adjust import path to your project layout if necessary.
from app.sources.client.zoom.zoom import ZoomClient, ZoomTokenConfig  # type: ignore
from app.sources.external.zoom.zoom import ZoomDataSource  # type: ignore


async def main() -> None:
    # Example: Using a static token
    config = ZoomTokenConfig(
        base_url="https://api.zoom.us/v2",
        token="REPLACE_WITH_VALID_TOKEN"
    )
    client = ZoomClient.build_with_config(config)

    ds = ZoomDataSource(client)

    # TODO: replace with a real method and valid parameters
    try:
        resp = await ds.${first_method}(${first_call_args})
        print("Response:", resp)
    finally:
        # Close underlying http client if supported
        close = getattr(client, "close", None)
        if callable(close):
            await close()


if __name__ == "__main__":
    asyncio.run(main())
''')
        build_tpl = Template(
            r'''"""Example builder for PipesHub service registry / build_from_services pattern.

This file demonstrates how the Zoom datasource can be wired into the services container.
"""

from typing import Any
from app.services.service_builder import service_builder  # type: ignore
from app.services.service_manager import ServiceManager  # type: ignore

from app.sources.client.zoom.zoom import ZoomClient  # type: ignore
from app.sources.external.zoom.zoom import ZoomDataSource  # type: ignore

@service_builder("zoom")
def build_zoom(services: ServiceManager) -> Any:
    """Build the Zoom datasource from the services container.

    Expected:
      - services.get("zoom_rest_client") returns an IClient instance configured for Zoom.
    """
    client = services.get("zoom_rest_client")
    return ZoomDataSource(client)
''')

        example_py = out_dir / "example.py"
        example_bfs = out_dir / "example_build_from_services.py"
        if (not example_py.exists()) or overwrite:
            example_py.write_text(example_tpl.safe_substitute(first_method=first_method, first_call_args=first_call_args), encoding="utf-8")
        if (not example_bfs.exists()) or overwrite:
            example_bfs.write_text(build_tpl.safe_substitute(), encoding="utf-8")


# ------------------ CLI / Orchestrator ------------------


def collect_spec_files(spec_dir: Path) -> List[Path]:
    # Return files in directory order (Path.iterdir), selecting .json files only
    return [p for p in spec_dir.iterdir() if p.is_file() and p.suffix.lower() == ".json"]


def main():
    ap = argparse.ArgumentParser(prog="zoom.py", description="Generate unified Zoom datasource from JSON specs")
    ap.add_argument("--spec-dir", default="backend/python/code-generator/zoom_specs", help="Directory containing spec .json files")
    ap.add_argument("--out-dir", default="backend/python/app/sources/external/zoom", help="Output directory for generated files")
    ap.add_argument("--features", default=None, help="Comma-separated feature filters (case-insensitive substring match). e.g. meetings,users")
    ap.add_argument("--no-overwrite", action="store_true", help="Do not overwrite existing generated files")
    args = ap.parse_args()

    spec_dir = Path(args.spec_dir)
    out_dir = Path(args.out_dir)
    overwrite = not args.no_overwrite

    if not spec_dir.exists():
        print(f"Spec directory not found: {spec_dir}")
        raise SystemExit(1)

    files = collect_spec_files(spec_dir)
    if not files:
        print(f"No .json spec files found in {spec_dir}")
        raise SystemExit(1)

    # preserve order of files as they appear in directory listing
    feature_filters = None
    if args.features:
        feature_filters = [f.strip().lower() for f in args.features.split(",") if f.strip()]

    # filter while preserving order
    to_process = []
    for f in files:
        if feature_filters:
            low = f.name.lower()
            if not any(ff in low for ff in feature_filters):
                continue
        to_process.append(f)

    if not to_process:
        print("No spec files matched filters.")
        raise SystemExit(1)

    gen = UnifiedZoomGenerator()
    ok, msg = gen.generate_unified(to_process, out_dir, overwrite=overwrite)
    if ok:
        print("Summary:")
        print(f"  Scanned: {len(to_process)}")
        print("  Generated: 1 unified datasource + examples")
        print("\nDone. Inspect generated file in:", out_dir)
    else:
        print("Failed:", msg)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
