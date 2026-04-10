# ruff: noqa

# Development-only utilities:
# - Download OpenAPI specs
# - Generate Pydantic models (v2)
# - Generate API client methods with typed params and request-body support (incl. additionalProperties)

import importlib.util
import json
import keyword
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


# ---------------------------
# OpenAPI fetch & model gen
# ---------------------------

def download_spec(connector: str, url: str, spec_file: Path) -> None:
    """Download OpenAPI spec JSON to a local file."""
    # If the url looks like a local file path, just copy/read it
    if url.startswith("file://") or (not url.lower().startswith("http://") and not url.lower().startswith("https://")):
        src_path = Path(url.replace("file://", ""))
        if not src_path.exists():
            raise FileNotFoundError(f"Spec file not found: {src_path}")
        print(f"📥 Using local {connector.capitalize()} OpenAPI spec from {src_path}")
        # Convert YAML to JSON if needed
        if src_path.suffix in [".yaml", ".yml"] and spec_file.suffix == ".json":
            import yaml
            with open(src_path, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f)
            with open(spec_file, "w", encoding="utf-8") as f:
                json.dump(yaml_data, f, indent=2)
            print(f"✅ {connector.capitalize()} OpenAPI YAML converted to JSON at {spec_file}")
        else:
            spec_file.write_text(src_path.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"✅ {connector.capitalize()} OpenAPI spec copied to {spec_file}")
        return

    try:
        import requests
    except ImportError:
        print("📦 Installing 'requests'...")
        subprocess.run([sys.executable, "-m", "pip", "install", "requests"], check=True)
        import requests  # type: ignore

    print(f"📥 Downloading {connector.capitalize()} OpenAPI spec from {url}")
    resp = requests.get(url)
    resp.raise_for_status()
    spec_file.write_text(resp.text, encoding="utf-8")
    print(f"✅ {connector.capitalize()} OpenAPI spec saved to {spec_file}")


def generate_models(connector: str, spec_file: Path, model_file: Path) -> None:
    """Generate Pydantic v2 models from the OpenAPI spec."""
    subprocess.run([sys.executable, "-m", "pip", "install", "datamodel-code-generator"], check=True)
    print("✅ Installed datamodel-code-generator")

    cmd = [
        sys.executable, "-m", "datamodel_code_generator",
        "--input", str(spec_file),
        "--input-file-type", "openapi",
        "--output", str(model_file),
        "--target-python-version", "3.10",
        "--use-standard-collections",
        "--output-model-type", "pydantic_v2.BaseModel",
    ]
    print(f"🚀 Generating Pydantic v2 models for {connector.capitalize()}...")
    subprocess.run(cmd, check=True)
    print(f"✅ {connector.capitalize()} models generated at {model_file}")


def fix_constr_calls(file_path: str) -> None:
    """Patch legacy Pydantic v1 constructs to v2-friendly forms."""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    imports_to_add = []
    if "Annotated" not in content:
        imports_to_add.append("from typing import Annotated")
    if "StringConstraints" not in content or "Field" not in content:
        imports_to_add.append("from pydantic import StringConstraints, Field")

    if imports_to_add:
        match = re.search(r"(^((?:from|import)[^\n]*\n)+)", content, flags=re.MULTILINE)
        if match:
            insert_pos = match.end()
            content = content[:insert_pos] + "\n".join(imports_to_add) + "\n\n" + content[insert_pos:]
        else:
            content = "\n".join(imports_to_add) + "\n\n" + content

    patterns = [
        (r'constr\(min_length=(\d+),\s*max_length=(\d+)\)', r'Annotated[str, StringConstraints(min_length=\1, max_length=\2)]'),
        (r'constr\(max_length=(\d+)\)', r'Annotated[str, StringConstraints(max_length=\1)]'),
        (r'constr\(min_length=(\d+)\)', r'Annotated[str, StringConstraints(min_length=\1)]'),
        (r'constr\(regex=["\']([^"\']+)["\']\)', r'Annotated[str, StringConstraints(pattern=r"\1")]'),
        (r'StrictStr', r'Annotated[str, Field(strict=True)]'),
        (r'StrictInt', r'Annotated[int, Field(strict=True)]'),
        (r'StrictFloat', r'Annotated[float, Field(strict=True)]'),
        (r'StrictBool', r'Annotated[bool, Field(strict=True)]'),
    ]
    before = content
    for pattern, replacement in patterns:
        content = re.sub(pattern, replacement, content)
    if content != before:
        print("🛠  Patched constr()/Strict* for Pydantic v2.")

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"✅ Fixed constr(), Strict* types, and safely added imports in {file_path}")


# --------------------
#  Helpers for params
# --------------------

_PY_RESERVED = set(keyword.kwlist) | {"from", "global", "async", "await", "None", "self", "cls"}
_ALWAYS_RESERVED_NAMES = {"self", "headers", "body", "body_additional"}

def _ensure_unique(base: str, used: set, suffix: str) -> str:
    """
    Ensure name uniqueness; if taken, apply suffix, then numeric suffixes.
    Example: 'id' -> 'id_body', then 'id_body2', 'id_body3', ...
    """
    name = base
    if name in used or name in _ALWAYS_RESERVED_NAMES:
        candidate = f"{base}_{suffix}"
        i = 2
        while candidate in used or candidate in _ALWAYS_RESERVED_NAMES:
            candidate = f"{base}_{suffix}{i}"
            i += 1
        name = candidate
    used.add(name)
    return name

def _dedupe_param_names(
    path_params: List[dict],
    query_params: List[dict],
    header_params: List[dict],
    body_fields: List[dict],
) -> None:
    """
    Mutates py_name in-place to avoid collisions across all sources.
    Priority order: path > query > header > body (body gets suffixed if colliding).
    """
    used: set = set(_ALWAYS_RESERVED_NAMES)
    # Path first (no suffix)
    for p in path_params:
        p["py_name"] = _ensure_unique(p["py_name"], used, "path")
    # Query
    for p in query_params:
        p["py_name"] = _ensure_unique(p["py_name"], used, "query")
    # Header
    for p in header_params:
        p["py_name"] = _ensure_unique(p["py_name"], used, "header")
    # Body fields
    for bf in body_fields:
        bf["py_name"] = _ensure_unique(bf["py_name"], used, "body")

def _sanitize_name(name: str) -> str:
    name = name.replace("-", "_")
    if not name.isidentifier() or name in _PY_RESERVED:
        name = f"{name}_"
    return name


def _to_snake_case(name: str) -> str:
    """camelCase/PascalCase -> snake_case."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    snake = s2.replace("__", "_").lower().strip("_")
    return snake


def _schema_to_py_type(schema: Optional[Dict[str, Any]]) -> str:
    """OpenAPI schema -> Python typing for parameter annotations (best-effort)."""
    if not schema:
        return "str"
    if "$ref" in schema:
        # Treat referenced param types as generic objects for signatures
        return "Dict[str, Any]"
    t = schema.get("type")
    fmt = (schema.get("format") or "").lower()

    if t == "integer":
        return "int"
    if t == "number":
        return "float"
    if t == "boolean":
        return "bool"
    if t == "array":
        items = schema.get("items") or {}
        item_t = _schema_to_py_type(items)
        return f"list[{item_t}]"
    if t == "object":
        # Could be a free-form object or map-like (handled by additionalProperties elsewhere)
        return "Dict[str, Any]"
    if t == "string":
        if fmt in {"byte", "binary"}:
            return "bytes"
        return "str"
    return "str"


def _resolve_ref(ref: str, root: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve a local JSON pointer like '#/components/schemas/X'."""
    if not (isinstance(ref, str) and ref.startswith("#/")):
        return {}
    node: Any = root
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            return {}
        node = node[part]
    return node if isinstance(node, dict) else {}


def _deep_resolve_schema(schema: Dict[str, Any], root: Dict[str, Any], _seen: Optional[set] = None) -> Dict[str, Any]:
    """
    Recursively resolve $ref and merge simple allOf object shapes (properties + required).
    Keeps other combinators as-is.
    """
    if _seen is None:
        _seen = set()
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in _seen:
            return {}
        _seen.add(ref)
        base = _resolve_ref(ref, root)
        merged = {**base, **{k: v for k, v in schema.items() if k != "$ref"}}
        schema = merged

    # Merge simple allOf of objects: combine properties & required
    if isinstance(schema.get("allOf"), list):
        props: Dict[str, Any] = {}
        required: List[str] = []
        for part in schema["allOf"]:
            part = _deep_resolve_schema(part, root, _seen)
            if part.get("type") == "object":
                props.update(part.get("properties") or {})
                required.extend(part.get("required") or [])
        schema = {
            **schema,
            "type": schema.get("type", "object"),
            "properties": {**(schema.get("properties") or {}), **props},
            "required": list(dict.fromkeys(required + (schema.get("required") or []))),
        }
    return schema


def _collect_operation_params(details: dict) -> Tuple[List[dict], List[dict], List[dict]]:
    """
    Return (path_params, query_params, header_params)
    each item: {name, py_name, type, required, description, style, explode}
    """
    path_params: List[dict] = []
    query_params: List[dict] = []
    header_params: List[dict] = []

    for p in details.get("parameters", []) or []:
        where = (p.get("in") or "").lower()
        name = p.get("name") or ""
        schema = (p.get("schema") or {})
        py_type = _schema_to_py_type(schema)
        desc = p.get("description") or ""
        item = {
            "name": name,
            "py_name": _sanitize_name(name),
            "type": py_type,
            "required": bool(p.get("required", False)),
            "description": desc,
            "style": p.get("style"),
            "explode": p.get("explode", True),
        }
        if where == "path":
            item["required"] = True  # path params always required
            path_params.append(item)
        elif where == "query":
            query_params.append(item)
        elif where == "header":
            header_params.append(item)

    return path_params, query_params, header_params


def _collect_request_body(
    details: dict,
    root: Dict[str, Any],
) -> Tuple[bool, List[dict], Optional[str], str, bool]:
    """
    Parse requestBody. Returns:
      (has_body, body_fields, raw_body_type, content_type, allow_additional)
    - If the body is an object schema with properties, body_fields is a list of
      {name, py_name, type, required, description} (explode into typed kwargs).
    - If the object uses additionalProperties, allow_additional=True and we expose
      an extra `body_additional: Optional[Dict[str, Any]] = None` parameter.
    - Otherwise, body_fields=[], raw_body_type is a python type for the top-level body.
    """
    rb = details.get("requestBody")
    if not rb:
        return False, [], None, "", False

    content = rb.get("content") or {}
    # Prefer JSON, then x-www-form-urlencoded, multipart, octet-stream, else first
    prefer = [
        "application/json",
        "application/x-www-form-urlencoded",
        "multipart/form-data",
        "application/octet-stream",
    ]
    media = next((m for m in prefer if m in content), None) or (next(iter(content.keys()), None))
    if not media:
        return True, [], "Any", "", False

    schema = (content.get(media, {}) or {}).get("schema") or {}
    schema = _deep_resolve_schema(schema, root)

    allow_additional = False

    # object with properties and/or additionalProperties -> explode to args
    if schema.get("type") == "object":
        addl = schema.get("additionalProperties", False)
        if addl is True or isinstance(addl, dict):
            allow_additional = True
        if isinstance(schema.get("properties"), dict):
            props: Dict[str, dict] = schema["properties"]  # type: ignore
            required_props = set(schema.get("required") or [])
            fields: List[dict] = []
            for name, prop_schema in props.items():
                prop_schema = _deep_resolve_schema(prop_schema if isinstance(prop_schema, dict) else {}, root)
                fields.append({
                    "name": name,
                    "py_name": _sanitize_name(name),
                    "type": _schema_to_py_type(prop_schema),
                    "required": name in required_props,
                    "description": (prop_schema.get("description") or "") if isinstance(prop_schema, dict) else "",
                })
            return True, fields, None, media, allow_additional

        # object without explicit properties but with additionalProperties => map-like
        if allow_additional:
            return True, [], "Dict[str, Any]", media, allow_additional

        # plain object with no properties -> still accept dict
        return True, [], "Dict[str, Any]", media, False

    # top-level (array, primitive, $ref, binary, etc.) -> single raw body param
    body_type = _schema_to_py_type(schema) or "Any"
    # If binary, we allow bytes; also allow Dict for flexibility where applicable
    if body_type == "bytes":
        body_type = "Union[Dict[str, Any], bytes]"
    return True, [], body_type, media, False


def _matches_prefix(path_: str, prefixes: Optional[List[str]]) -> bool:
    if not prefixes:
        return True
    return any(path_.startswith(p) for p in prefixes)


def _safe_format_url(template: str, params: Dict[str, object]) -> str:
    class _SafeDict(dict):
        def __missing__(self, key):  # type: ignore
            return "{" + key + "}"
    try:
        return template.format_map(_SafeDict(params))
    except Exception:
        return template


def _to_bool_str(v: Union[bool, str, int, float]) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _serialize_value(v: Union[bool, str, int, float, list, tuple, set, None]) -> str:
    """Serialize a value for query/header/path dicts."""
    if v is None:
        return ""
    if isinstance(v, (list, tuple, set)):
        return ",".join(_to_bool_str(x) for x in v)
    return _to_bool_str(v)


def _as_str_dict(d: Dict[str, Any]) -> Dict[str, str]:
    return {str(k): _serialize_value(v) for k, v in (d or {}).items()}


# ------------------------------------------------------
#  Client generation with default __init__
# ------------------------------------------------------

def generate_client_methods(
    connector: str,
    spec_file: Path,
    client_file: Path,
    path_prefixes: Optional[List[str]] = None,
) -> None:
    print(f"⚙️ Generating API client methods for {connector.capitalize()} (typed params, full body support incl. additionalProperties)...")

    spec = json.loads(spec_file.read_text(encoding="utf-8"))
    paths: Dict[str, Dict[str, dict]] = spec.get("paths", {})  # type: ignore

    generated = 0
    skipped = 0
    Title = connector.capitalize()

    lines: List[str] = [
        "from typing import Any, Dict, Optional, Union\n",
        "from app.sources.client.http.http_response import HTTPResponse\n",
        "from app.sources.client.http.http_request import HTTPRequest\n",
        f"from app.sources.client.{connector}.{connector} import {Title}Client\n\n",
        f"class {Title}DataSource:\n",
        f"    def __init__(self, client: {Title}Client) -> None:\n",
        "        \"\"\"Default init for the connector-specific data source.\"\"\"\n",
        "        self._client = client\n",
        "        self.http = client.get_client()\n",
        "        if self.http is None:\n",
        "            raise ValueError('HTTP client is not initialized')\n",
        "        try:\n",
        "            self.base_url = self.http.get_base_url().rstrip('/')\n",
        "        except AttributeError as exc:\n",
        "            raise ValueError('HTTP client does not have get_base_url method') from exc\n\n",
        f"    def get_data_source(self) -> '{Title}DataSource':\n",
        "        return self\n\n",
    ]

    for raw_path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        if not _matches_prefix(raw_path, path_prefixes):
            skipped += len(methods)
            continue

        for http_method, details in methods.items():
            if not isinstance(details, dict):
                continue

            # method name (prefer operationId)
            func_name = details.get("operationId") or f"{http_method}_{raw_path.strip('/').replace('/', '_')}"
            func_name = _to_snake_case(re.sub(r'[^0-9a-zA-Z_]', '_', func_name))
            func_name = _sanitize_name(func_name)

            summary = (details.get("summary") or "").strip()

            # params
            path_params, query_params, header_params = _collect_operation_params(details)
            has_body, body_fields, raw_body_type, body_ct, allow_additional = _collect_request_body(details, spec)
            # dedupe names across all arg sources to prevent collisions like 'id' vs body 'id'
            _dedupe_param_names(path_params, query_params, header_params, body_fields)
            # -----------------------
            # Build signature
            # -----------------------
            required_parts: List[str] = ["self"]
            optional_parts: List[str] = []

            # required path
            for p in path_params:
                required_parts.append(f"{p['py_name']}: {p['type']}")

            # required query
            for p in (pp for pp in query_params if pp["required"]):
                required_parts.append(f"{p['py_name']}: {p['type']}")

            # required header
            for p in (pp for pp in header_params if pp["required"]):
                required_parts.append(f"{p['py_name']}: {p['type']}")

            # required body fields
            if has_body and body_fields:
                for bf in (bf for bf in body_fields if bf["required"]):
                    required_parts.append(f"{bf['py_name']}: {bf['type']}")

            # optional query
            for p in (pp for pp in query_params if not pp["required"]):
                optional_parts.append(f"{p['py_name']}: Optional[{p['type']}] = None")

            # optional header
            for p in (pp for pp in header_params if not pp["required"]):
                optional_parts.append(f"{p['py_name']}: Optional[{p['type']}] = None")

            # optional body fields OR raw body
            if has_body and body_fields:
                for bf in (bf for bf in body_fields if not bf["required"]):
                    optional_parts.append(f"{bf['py_name']}: Optional[{bf['type']}] = None")
            elif has_body and raw_body_type:
                optional_parts.append(f"body: Optional[Dict[str, Any]] = None")

            # map-like additionalProperties support
            if has_body and (body_fields or (raw_body_type and raw_body_type.startswith("Dict["))) and allow_additional:
                optional_parts.append("body_additional: Optional[Dict[str, Any]] = None")

            # always allow extra headers
            optional_parts.append("headers: Optional[Dict[str, Any]] = None")

            sig_parts = required_parts + optional_parts

            # -----------------------
            # Docstring
            # -----------------------
            doc_lines = [
                f'Auto-generated from OpenAPI: {summary}' if summary else "Auto-generated from OpenAPI",
                "",
                f"HTTP {http_method.upper()} {raw_path}",
            ]
            if path_params:
                doc_lines.append("Path params:")
                for p in path_params:
                    doc_lines.append(f"  - {p['name']} ({p['type']})")
            if query_params:
                doc_lines.append("Query params:")
                for p in query_params:
                    req = "required" if p["required"] else "optional"
                    doc_lines.append(f"  - {p['name']} ({p['type']}, {req})")
            if header_params:
                doc_lines.append("Header params:")
                for p in header_params:
                    req = "required" if p["required"] else "optional"
                    doc_lines.append(f"  - {p['name']} ({p['type']}, {req})")
            if has_body:
                if body_fields:
                    doc_lines.append(f"Body ({body_ct or 'application/json'}) fields:")
                    for bf in body_fields:
                        req = "required" if bf["required"] else "optional"
                        doc_lines.append(f"  - {bf['name']} ({bf['type']}, {req})")
                    if allow_additional:
                        doc_lines.append("  - additionalProperties allowed (pass via body_additional)")
                else:
                    doc_lines.append(f"Body: {body_ct or 'application/json'} ({raw_body_type or 'Any'})")

            doc = "\\n".join(doc_lines)

            # -----------------------
            # Method source
            # -----------------------
            lines.append(f"    async def {func_name}(\n")
            for i, part in enumerate(sig_parts):
                comma = "," if i < len(sig_parts) - 1 else ""
                lines.append(f"        {part}{comma}\n")
            lines.append("    ) -> HTTPResponse:\n")
            lines.append(f"        \"\"\"{doc}\"\"\"\n")
            lines.append("        if self.http is None:\n")
            lines.append("            raise ValueError('HTTP client is not initialized')\n")
            lines.append("        _headers: Dict[str, Any] = dict(headers or {})\n")

            # Build header params into headers
            if header_params:
                for p in header_params:
                    py = p["py_name"]
                    nm = p["name"]
                    if p["required"]:
                        lines.append(f"        _headers['{nm}'] = {py}\n")
                    else:
                        lines.append(f"        if {py} is not None:\n")
                        lines.append(f"            _headers['{nm}'] = {py}\n")

            if has_body and body_ct:
                lines.append(f"        _headers.setdefault('Content-Type', '{body_ct}')\n")

            # Path dict
            if path_params:
                lines.append("        _path: Dict[str, Any] = {\n")
                for p in path_params:
                    lines.append(f"            '{p['name']}': {p['py_name']},\n")
                lines.append("        }\n")
            else:
                lines.append("        _path: Dict[str, Any] = {}\n")

            # Query dict
            if query_params:
                lines.append("        _query: Dict[str, Any] = {}\n")
                for p in query_params:
                    name = p["name"]
                    py_name = p["py_name"]
                    if p["required"]:
                        lines.append(f"        _query['{name}'] = {py_name}\n")
                    else:
                        lines.append(f"        if {py_name} is not None:\n")
                        lines.append(f"            _query['{name}'] = {py_name}\n")
            else:
                lines.append("        _query: Dict[str, Any] = {}\n")

            # Body build
            if has_body:
                if body_fields:
                    lines.append("        _body: Dict[str, Any] = {}\n")
                    for bf in body_fields:
                        if bf["required"]:
                            lines.append(f"        _body['{bf['name']}'] = {bf['py_name']}\n")
                        else:
                            lines.append(f"        if {bf['py_name']} is not None:\n")
                            lines.append(f"            _body['{bf['name']}'] = {bf['py_name']}\n")
                    if allow_additional:
                        lines.append("        if 'body_additional' in locals() and body_additional:\n")
                        lines.append("            _body.update(body_additional)\n")
                else:
                    lines.append("        _body = body\n")
            else:
                lines.append("        _body = None\n")

            # URL + HTTPRequest
            lines.append(f"        rel_path = '{raw_path}'\n")
            lines.append("        url = self.base_url + _safe_format_url(rel_path, _path)\n")
            lines.append("        req = HTTPRequest(\n")
            lines.append(f"            method='{http_method.upper()}',\n")
            lines.append("            url=url,\n")
            lines.append("            headers=_as_str_dict(_headers),\n")
            lines.append("            path_params=_as_str_dict(_path),\n")
            lines.append("            query_params=_as_str_dict(_query),\n")
            lines.append("            body=_body,\n")
            lines.append("        )\n")

            # Execute (no error handling per request)
            lines.append("        resp = await self.http.execute(req)\n")
            lines.append("        return resp\n\n")

            generated += 1

    # Helpers included in generated module
    lines.append("# ---- Helpers used by generated methods ----\n")
    lines.append("def _safe_format_url(template: str, params: Dict[str, object]) -> str:\n")
    lines.append("    class _SafeDict(dict):\n")
    lines.append("        def __missing__(self, key: str) -> str:\n")
    lines.append("            return '{' + key + '}'\n")
    lines.append("    try:\n")
    lines.append("        return template.format_map(_SafeDict(params))\n")
    lines.append("    except Exception:\n")
    lines.append("        return template\n")
    lines.append("\n")
    lines.append("def _to_bool_str(v: Union[bool, str, int, float]) -> str:\n")
    lines.append("    if isinstance(v, bool):\n")
    lines.append("        return 'true' if v else 'false'\n")
    lines.append("    return str(v)\n")
    lines.append("\n")
    lines.append("def _serialize_value(v: Any) -> str:\n")
    lines.append("    if v is None:\n")
    lines.append("        return ''\n")
    lines.append("    if isinstance(v, (list, tuple, set)):\n")
    lines.append("        return ','.join(_to_bool_str(x) for x in v)\n")
    lines.append("    return _to_bool_str(v)\n")
    lines.append("\n")
    lines.append("def _as_str_dict(d: Dict[str, Any]) -> Dict[str, str]:\n")
    lines.append("    return {str(k): _serialize_value(v) for k, v in (d or {}).items()}\n")

    client_file.write_text("".join(lines), encoding="utf-8")

    print(f"✅ Client methods generated at {client_file}")
    print(f"📈 Methods generated: {generated} | Skipped (path filter): {skipped}")


def inspect_models(connector: str, model_file: Path) -> None:
    """Quick smoke test: import generated models and list a few known names."""
    if not model_file.exists():
        print("⚠️ Models file not found.")
        return

    spec = importlib.util.spec_from_file_location(f"{connector}_models", model_file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore

    print(f"\n🧪 Sample models found in {connector.capitalize()} definitions:")
    found = 0
    for name in dir(module):
        if "issue" in name.lower() or "project" in name.lower():
            print(f" • {name}")
            found += 1
            if found >= 10:
                break


def process_connector(
    connector: str,
    url: str,
    base_dir: Path,
    path_prefixes: Optional[List[str]] = None,
) -> None:
    """End-to-end pipeline for a connector."""
    output_dir = base_dir / connector
    output_dir.mkdir(exist_ok=True)

    spec_file = output_dir / f"{connector}_openapi.json"
    model_file = output_dir / f"{connector}_models.py"
    client_file = output_dir / f"{connector}_client.py"

    download_spec(connector, url, spec_file)
    generate_models(connector, spec_file, model_file)
    fix_constr_calls(str(model_file))
    generate_client_methods(connector, spec_file, client_file, path_prefixes=path_prefixes)
    inspect_models(connector, model_file)
