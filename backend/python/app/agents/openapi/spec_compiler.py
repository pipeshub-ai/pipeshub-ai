"""Turns an uploaded OpenAPI 3.0/3.1 document into tools.

Pure: no network, no storage. Everything the runtime needs is decided here, once, so a
broken spec fails while the admin is still looking at it rather than during a chat.

Only references inside the document are followed. A remote `$ref` would make PipesHub
fetch an address the spec chooses, so an operation that needs one is skipped.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

import yaml

from app.agents.mcp.tool_kind import ToolKind, deletes_by_name
from app.agents.openapi.models import (
    AuthScheme,
    AuthType,
    CompiledOperation,
    CompiledSpec,
    OperationParameter,
    ParameterLocation,
    ServerOption,
)

MAX_SPEC_BYTES = 5 * 1024 * 1024
MAX_REF_DEPTH = 32
HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# OpenAPI says these header parameters are ignored: the content type and auth come from elsewhere.
_IGNORED_HEADERS = frozenset({"accept", "content-type", "authorization"})
# What every LLM provider accepts as a tool name.
_TOOL_NAME_MAX = 64
_TOOL_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")
_SERVER_VARIABLE = re.compile(r"\{([^{}]+)\}")
_COMPONENT_SCHEMA_PREFIX = "#/components/schemas/"
_BODY_ARGUMENT_NAMES = ("body", "request_body", "json_body")
# Values that are data, not schemas: a `$ref` inside an example is text, never followed.
_DATA_KEYWORDS = frozenset({"example", "examples", "default", "enum", "const"})
_SCHEMA_MAP_KEYWORDS = frozenset({"properties", "patternProperties", "dependentSchemas", "$defs", "definitions"})


class SpecError(ValueError):
    """The document can't be used at all. The message is safe to show the admin."""


class _SkipOperation(Exception):
    """One operation can't be offered; the rest of the spec still can."""


class _NoAliasLoader(yaml.SafeLoader):
    """YAML anchors let a few bytes expand into gigabytes ("billion laughs"); specs don't need them."""

    def compose_node(self, parent: Any, index: Any) -> Any:  # noqa: ANN401
        if self.check_event(yaml.AliasEvent):
            raise SpecError("The spec uses YAML aliases (*name), which are not supported. Expand them first.")
        return super().compose_node(parent, index)


def compile_spec(raw: bytes) -> CompiledSpec:
    """Parse and check `raw` (JSON or YAML) and compile every usable operation."""
    document = parse_document(raw)
    resolver = _Resolver(document)
    warnings: list[str] = []
    operations = _compile_operations(document, resolver, warnings)
    if not operations:
        raise SpecError("The spec has no operations PipesHub can call." + (f" {warnings[0]}" if warnings else ""))
    info = document.get("info") if isinstance(document.get("info"), dict) else {}
    return CompiledSpec(
        title=str(info.get("title") or "Untitled API"),
        version=str(info.get("version") or ""),
        openapi_version=str(document["openapi"]),
        description=str(info.get("description") or "").strip(),
        servers=_servers(document.get("servers")),
        auth_schemes=_auth_schemes(document, resolver, warnings),
        operations=operations,
        warnings=warnings,
        spec_hash=hashlib.sha256(raw).hexdigest(),
    )


def parse_document(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_SPEC_BYTES:
        raise SpecError(f"The spec is larger than {MAX_SPEC_BYTES // (1024 * 1024)} MB.")
    try:
        # JSON first: large specs mostly ship as JSON, which the pure-Python YAML parser takes seconds
        # over (Jira's 2.5 MB spec: ~10 s against 0.1 s). JSON has no aliases to expand.
        document = json.loads(raw)
    except ValueError:
        try:
            document = yaml.load(raw, Loader=_NoAliasLoader)  # a SafeLoader subclass, so no arbitrary objects
        except yaml.YAMLError as e:
            raise SpecError(_yaml_error_message(raw, e)) from e
    if not isinstance(document, dict):
        raise SpecError("The spec must be a JSON or YAML object.")
    if "swagger" in document:
        raise SpecError("Swagger 2.0 specs are not supported. Convert the spec to OpenAPI 3 first.")
    version = str(document.get("openapi") or "")
    if not re.match(r"^3\.[01]\.", version):
        raise SpecError("Only OpenAPI 3.0 and 3.1 specs are supported (the `openapi` field must be 3.0.x or 3.1.x).")
    if not isinstance(document.get("paths"), dict) or not document["paths"]:
        raise SpecError("The spec has no paths.")
    return document


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


class _Resolver:
    """Follows `#/...` pointers within the document. Remote references are refused."""

    def __init__(self, document: dict[str, Any]) -> None:
        self._document = document

    def deref(self, node: Any) -> Any:  # noqa: ANN401
        """`node` with any chain of `$ref`s on it followed (for parameters, bodies, schemes)."""
        seen: set[str] = set()
        while isinstance(node, dict) and isinstance(node.get("$ref"), str):
            ref = node["$ref"]
            if ref in seen or len(seen) >= MAX_REF_DEPTH:
                raise _SkipOperation(f"the reference {ref} loops")
            seen.add(ref)
            node = self.target(ref)
        return node

    def target(self, ref: str) -> Any:  # noqa: ANN401
        if not ref.startswith("#/"):
            raise _SkipOperation(f"it uses the remote reference {ref}, which is never fetched")
        node: Any = self._document
        for raw_part in ref[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                raise _SkipOperation(f"the reference {ref} points to nothing")
        return node

    def component_schema(self, name: str) -> Any:  # noqa: ANN401
        return self.target(_COMPONENT_SCHEMA_PREFIX + name.replace("~", "~0").replace("/", "~1"))


class _SchemaBuilder:
    """Rewrites schema references into one tool's `$defs`.

    `#/components/schemas/X` becomes `#/$defs/X` and X is copied in, along with everything
    X refers to. Any other in-document pointer is inlined. The agent runtime then inlines
    the `$defs` itself, with its own cycle and size limits.
    """

    def __init__(self, resolver: _Resolver) -> None:
        self._resolver = resolver
        self.defs: dict[str, Any] = {}

    def build(self, schema: Any, depth: int = 0) -> Any:  # noqa: ANN401
        if depth > MAX_REF_DEPTH:
            raise _SkipOperation("its schema nests references too deeply")
        if isinstance(schema, list):
            return [self.build(item, depth) for item in schema]
        if not isinstance(schema, dict):
            return schema
        ref = schema.get("$ref")
        if isinstance(ref, str):
            siblings = {k: self.build(v, depth) for k, v in schema.items() if k != "$ref"}
            if ref.startswith(_COMPONENT_SCHEMA_PREFIX):
                name = ref[len(_COMPONENT_SCHEMA_PREFIX):].replace("~1", "/").replace("~0", "~")
                self._add_definition(name, depth)
                return {"$ref": f"#/$defs/{name}", **siblings}
            return {**self.build(self._resolver.target(ref), depth + 1), **siblings}
        built: dict[str, Any] = {}
        for key, value in schema.items():
            if key in _DATA_KEYWORDS:
                built[key] = value
            elif key in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
                # Keys here are property names, so a property called "enum" is still a schema.
                built[key] = {name: self.build(sub, depth) for name, sub in value.items()}
            else:
                built[key] = self.build(value, depth)
        return built

    def _add_definition(self, name: str, depth: int) -> None:
        if name in self.defs:
            return
        self.defs[name] = {}  # reserved first, so a schema that refers to itself stops here
        self.defs[name] = self.build(self._resolver.component_schema(name), depth + 1)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def _compile_operations(document: dict[str, Any], resolver: _Resolver, warnings: list[str]) -> list[CompiledOperation]:
    root_security = document.get("security")
    operations: list[CompiledOperation] = []
    used_names: set[str] = set()
    for path, raw_item in document["paths"].items():
        if not isinstance(raw_item, dict):
            continue
        try:
            path_item = resolver.deref(raw_item)
        except _SkipOperation as e:
            warnings.append(f"{path}: skipped, {e}.")
            continue
        if not isinstance(path_item, dict):
            warnings.append(f"{path}: skipped, its $ref doesn't point at a path item.")
            continue
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            label = f"{method.upper()} {path}"
            try:
                compiled = _compile_operation(
                    method.upper(), str(path), path_item, operation, resolver, root_security, used_names, warnings
                )
            except _SkipOperation as e:
                warnings.append(f"{label}: skipped, {e}.")
                continue
            except (TypeError, AttributeError, ValueError):
                # A field of the wrong type (a number where a name goes, a list where a map goes):
                # one bad operation shouldn't fail the whole spec with a server error.
                warnings.append(f"{label}: skipped, some of its fields have the wrong type.")
                continue
            used_names.add(compiled.tool_name)
            operations.append(compiled)
    return operations


def _compile_operation(
    method: str,
    path: str,
    path_item: dict[str, Any],
    operation: dict[str, Any],
    resolver: _Resolver,
    root_security: Any,  # noqa: ANN401
    used_names: set[str],
    warnings: list[str],
) -> CompiledOperation:
    label = f"{method} {path}"
    schemas = _SchemaBuilder(resolver)
    properties: dict[str, Any] = {}
    required: list[str] = []
    parameters: list[OperationParameter] = []

    for param in _merged_parameters(path_item, operation, resolver):
        location = param.get("in")
        name = param.get("name")
        if not isinstance(name, str) or location not in {loc.value for loc in ParameterLocation}:
            raise _SkipOperation("a parameter has no name or an unknown location")
        if location == ParameterLocation.HEADER.value and name.lower() in _IGNORED_HEADERS:
            continue
        is_required = bool(param.get("required")) or location == ParameterLocation.PATH.value
        schema = _parameter_schema(param)
        if schema is None:
            if is_required:
                raise _SkipOperation(f"the required parameter {name} has no JSON schema")
            warnings.append(f"{label}: parameter {name} left out, it has no JSON schema.")
            continue
        if name in properties:
            raise _SkipOperation(f"two parameters are both named {name}")
        properties[name] = _described(schemas.build(schema), param.get("description"))
        if is_required:
            required.append(name)
        parameters.append(OperationParameter(
            name=name,
            location=ParameterLocation(location),
            required=is_required,
            style=param.get("style"),
            explode=param.get("explode"),
        ))

    body_argument, body_required = None, False
    body = _json_body(operation, resolver, label, warnings)
    if body is not None:
        body_schema, body_required, body_description = body
        body_argument = next(n for n in _BODY_ARGUMENT_NAMES if n not in properties)
        properties[body_argument] = _described(schemas.build(body_schema), body_description or "The JSON request body.")
        if body_required:
            required.append(body_argument)

    input_schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        input_schema["required"] = required
    if schemas.defs:
        input_schema["$defs"] = schemas.defs

    operation_id = operation.get("operationId") if isinstance(operation.get("operationId"), str) else None
    summary = _clean(operation.get("summary"))
    description = _clean(operation.get("description"))
    return CompiledOperation(
        tool_name=_unique_tool_name(operation_id, method, path, used_names),
        operation_id=operation_id,
        method=method,
        path=path,
        summary=summary,
        description=description,
        parameters=parameters,
        body_argument=body_argument,
        body_required=body_required,
        input_schema=input_schema,
        kind=_kind(method, operation_id or path),
        requires_auth=_requires_auth(operation.get("security", root_security)),
    )


def _merged_parameters(path_item: dict[str, Any], operation: dict[str, Any], resolver: _Resolver) -> list[dict[str, Any]]:
    """Path-level parameters, overridden by operation-level ones with the same name and location."""
    merged: dict[tuple[Any, Any], dict[str, Any]] = {}
    for source in (path_item.get("parameters"), operation.get("parameters")):
        for raw in source if isinstance(source, list) else []:
            param = resolver.deref(raw)
            if isinstance(param, dict):
                merged[(param.get("name"), param.get("in"))] = param
    return list(merged.values())


def _parameter_schema(param: dict[str, Any]) -> Optional[dict[str, Any]]:
    if isinstance(param.get("schema"), dict):
        return param["schema"]
    content = param.get("content")
    if isinstance(content, dict):
        media = next((m for t, m in content.items() if _is_json(t) and isinstance(m, dict)), None)
        if media is not None and isinstance(media.get("schema"), dict):
            return media["schema"]
    return None


def _json_body(
    operation: dict[str, Any], resolver: _Resolver, label: str, warnings: list[str],
) -> Optional[tuple[dict[str, Any], bool, str]]:
    """The JSON request body's schema, whether it is required, and its description."""
    raw = operation.get("requestBody")
    if raw is None:
        return None
    body = resolver.deref(raw)
    if not isinstance(body, dict):
        return None
    required = bool(body.get("required"))
    content = body.get("content") if isinstance(body.get("content"), dict) else {}
    media = next((m for t, m in content.items() if _is_json(t) and isinstance(m, dict)), None)
    if media is None:
        kinds = ", ".join(content) or "no content type"
        if required:
            raise _SkipOperation(f"its request body is {kinds}; only JSON bodies are supported")
        warnings.append(f"{label}: optional request body ({kinds}) left out; only JSON bodies are supported.")
        return None
    schema = media.get("schema") if isinstance(media.get("schema"), dict) else {}
    return schema, required, _clean(body.get("description"))


def _is_json(media_type: str) -> bool:
    base = media_type.split(";", 1)[0].strip().lower()
    return base == "application/json" or base.endswith("+json")


def _described(schema: Any, description: Any) -> Any:  # noqa: ANN401
    text = _clean(description)
    if not text or not isinstance(schema, dict) or schema.get("description"):
        return schema
    return {**schema, "description": text}


def _unique_tool_name(operation_id: Optional[str], method: str, path: str, used: set[str]) -> str:
    base = re.sub(r"_{2,}", "_", _TOOL_NAME_UNSAFE.sub("_", operation_id or f"{method.lower()}_{path}")).strip("_") or method.lower()
    base = base[:_TOOL_NAME_MAX]
    name, counter = base, 2
    while name in used:
        suffix = f"_{counter}"
        name = base[: _TOOL_NAME_MAX - len(suffix)] + suffix
        counter += 1
    return name


def _kind(method: str, name: str) -> ToolKind:
    if method == "DELETE" or deletes_by_name(name):
        return "destructive"
    return "read" if method in _READ_METHODS else "write"


def _requires_auth(security: Any) -> bool:  # noqa: ANN401
    """False only when the operation explicitly allows anonymous calls: `security: []` or `[{}]`."""
    if not isinstance(security, list):
        return True
    return bool(security) and not any(isinstance(item, dict) and not item for item in security)


# ---------------------------------------------------------------------------
# Servers and security schemes
# ---------------------------------------------------------------------------


def _servers(raw: Any) -> list[ServerOption]:  # noqa: ANN401
    servers: list[ServerOption] = []
    for server in raw if isinstance(raw, list) else []:
        if not isinstance(server, dict) or not isinstance(server.get("url"), str):
            continue
        variables = server.get("variables") if isinstance(server.get("variables"), dict) else {}

        def default(match: re.Match[str], variables: dict[str, Any] = variables) -> str:
            var = variables.get(match.group(1))
            return str(var.get("default")) if isinstance(var, dict) and var.get("default") is not None else match.group(0)

        servers.append(ServerOption(url=_SERVER_VARIABLE.sub(default, server["url"]), description=_clean(server.get("description"))))
    return servers


def _auth_schemes(document: dict[str, Any], resolver: _Resolver, warnings: list[str]) -> list[AuthScheme]:
    components = document.get("components") if isinstance(document.get("components"), dict) else {}
    raw_schemes = components.get("securitySchemes") if isinstance(components.get("securitySchemes"), dict) else {}
    schemes: list[AuthScheme] = []
    for name, raw in raw_schemes.items():
        try:
            scheme = resolver.deref(raw)
        except _SkipOperation as e:
            warnings.append(f"Security scheme {name}: skipped, {e}.")
            continue
        if not isinstance(scheme, dict):
            continue
        converted = _auth_scheme(str(name), scheme)
        if not converted:
            warnings.append(f"Security scheme {name}: {scheme.get('type')} is not supported, so it can't be chosen.")
        schemes.extend(converted)
    return schemes


def _auth_scheme(name: str, scheme: dict[str, Any]) -> list[AuthScheme]:
    kind = scheme.get("type")
    description = _clean(scheme.get("description"))
    if kind == "apiKey" and scheme.get("in") in ("header", "query", "cookie") and isinstance(scheme.get("name"), str):
        return [AuthScheme(name=name, type=AuthType.API_KEY, description=description,
                           location=scheme["in"], param_name=scheme["name"])]
    if kind == "http":
        http_scheme = str(scheme.get("scheme") or "").lower()
        if http_scheme == "bearer":
            return [AuthScheme(name=name, type=AuthType.HTTP_BEARER, description=description)]
        if http_scheme == "basic":
            return [AuthScheme(name=name, type=AuthType.HTTP_BASIC, description=description)]
        return []
    if kind == "oauth2" and isinstance(scheme.get("flows"), dict):
        flows = scheme["flows"]
        found: list[AuthScheme] = []
        for flow_key, auth_type in (("clientCredentials", AuthType.OAUTH2_CLIENT_CREDENTIALS),
                                    ("authorizationCode", AuthType.OAUTH2_AUTHORIZATION_CODE)):
            flow = flows.get(flow_key)
            if not isinstance(flow, dict) or not isinstance(flow.get("tokenUrl"), str):
                continue
            found.append(AuthScheme(
                name=name if len(flows) == 1 else f"{name}.{flow_key}",
                type=auth_type,
                description=description,
                token_url=flow["tokenUrl"],
                authorization_url=flow.get("authorizationUrl") if isinstance(flow.get("authorizationUrl"), str) else None,
                scopes={str(k): str(v) for k, v in flow["scopes"].items()} if isinstance(flow.get("scopes"), dict) else {},
            ))
        return found
    return []


_HTML_ESCAPES = re.compile(rb"&(gt|lt|amp|quot|#3[49]);")


def _yaml_error_message(raw: bytes, error: yaml.YAMLError) -> str:
    """Where the parse failed, and the usual cause when the text came from a web page."""
    mark = getattr(error, "problem_mark", None)
    where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark is not None else ""
    message = f"The spec is not valid JSON or YAML{where}: {_first_line(str(error))}."
    if _HTML_ESCAPES.search(raw):
        message += " It contains HTML escapes such as &gt;, so it was probably copied from a web page's source; copy the plain text instead."
    return message


def _clean(value: Any) -> str:  # noqa: ANN401
    return value.strip() if isinstance(value, str) else ""


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else text
