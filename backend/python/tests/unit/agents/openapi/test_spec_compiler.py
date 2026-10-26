import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.agents.agent_loop.tool_adapter import resolve_json_schema_refs
from app.agents.openapi.models import (
    AuthType,
    CompiledOperation,
    CompiledSpec,
    ParameterLocation,
)
from app.agents.openapi.spec_compiler import MAX_SPEC_BYTES, SpecError, compile_spec

ACME_SPEC = Path(__file__).resolve().parents[6] / "integration-tests" / "openapi_fixture" / "openapi.yaml"


def _spec(paths: dict[str, Any], **extra: object) -> bytes:
    return yaml.safe_dump({"openapi": "3.1.0", "info": {"title": "T", "version": "1"}, "paths": paths, **extra}, sort_keys=False).encode()


def _op(spec: bytes, tool_name: str) -> CompiledOperation:
    return next(op for op in compile_spec(spec).operations if op.tool_name == tool_name)


class TestAcmeReferenceSpec:
    @pytest.fixture(scope="class")
    def compiled(self) -> CompiledSpec:
        return compile_spec(ACME_SPEC.read_bytes())

    def test_every_operation_compiles_without_warnings(self, compiled: CompiledSpec) -> None:
        assert [op.tool_name for op in compiled.operations] == [
            "list_tickets", "create_ticket", "get_ticket", "update_ticket", "add_ticket_comment", "get_current_user",
            "list_announcements",
        ]
        assert compiled.warnings == []
        assert compiled.title == "Acme Helpdesk API" and compiled.openapi_version == "3.1.0"

    def test_server_variables_take_their_defaults(self, compiled: CompiledSpec) -> None:
        assert [s.url for s in compiled.servers] == ["http://localhost:8098"]

    def test_every_security_scheme_is_offered(self, compiled: CompiledSpec) -> None:
        by_name = {s.name: s for s in compiled.auth_schemes}
        assert by_name["apiKeyAuth"].type is AuthType.API_KEY
        assert (by_name["apiKeyAuth"].location, by_name["apiKeyAuth"].param_name) == ("header", "X-API-Key")
        assert by_name["bearerAuth"].type is AuthType.HTTP_BEARER
        assert by_name["basicAuth"].type is AuthType.HTTP_BASIC
        oauth = by_name["oauth2ClientCredentials"]
        assert oauth.type is AuthType.OAUTH2_CLIENT_CREDENTIALS and oauth.token_url == "/oauth/token"
        assert set(oauth.scopes) == {"tickets:read", "tickets:write"}
        sign_in = by_name["oauth2SignIn"]
        assert sign_in.type is AuthType.OAUTH2_AUTHORIZATION_CODE
        assert (sign_in.authorization_url, sign_in.token_url) == ("/oauth/authorize", "/oauth/token")

    def test_the_public_operation_needs_no_credentials(self, compiled: CompiledSpec) -> None:
        requires_auth = {op.tool_name: op.requires_auth for op in compiled.operations}
        assert requires_auth.pop("list_announcements") is False
        assert all(requires_auth.values())

    def test_kinds_follow_the_method(self, compiled: CompiledSpec) -> None:
        kinds = {op.tool_name: op.kind for op in compiled.operations}
        assert kinds["list_tickets"] == "read" and kinds["get_ticket"] == "read"
        assert kinds["create_ticket"] == "write" and kinds["update_ticket"] == "write"

    def test_path_level_parameter_and_body(self, compiled: CompiledSpec) -> None:
        update = next(op for op in compiled.operations if op.tool_name == "update_ticket")
        assert [(p.name, p.location, p.required) for p in update.parameters] == [("ticketId", ParameterLocation.PATH, True)]
        assert update.body_argument == "body" and update.body_required
        assert update.input_schema["required"] == ["ticketId", "body"]
        assert update.input_schema["properties"]["body"]["$ref"] == "#/$defs/TicketUpdate"
        assert {"TicketUpdate", "TicketStatus", "Priority"} <= set(update.input_schema["$defs"])

    def test_header_parameter_is_kept(self, compiled: CompiledSpec) -> None:
        create = next(op for op in compiled.operations if op.tool_name == "create_ticket")
        assert [(p.name, p.location, p.required) for p in create.parameters] == [("Idempotency-Key", ParameterLocation.HEADER, False)]

    def test_array_query_parameter_keeps_its_style(self, compiled: CompiledSpec) -> None:
        listing = next(op for op in compiled.operations if op.tool_name == "list_tickets")
        tag = next(p for p in listing.parameters if p.name == "tag")
        assert (tag.style, tag.explode) == ("form", True)
        assert listing.input_schema["properties"]["status"]["$ref"] == "#/$defs/TicketStatus"
        assert listing.input_schema["properties"]["status"]["description"] == "Only tickets in this state."

    def test_runtime_inlines_the_compiled_schema(self, compiled: CompiledSpec) -> None:
        """The agent runtime's own inliner turns the $defs into one flat schema a provider accepts."""
        update = next(op for op in compiled.operations if op.tool_name == "update_ticket")
        flat = resolve_json_schema_refs(update.input_schema)
        assert "$ref" not in json.dumps(flat)
        body = flat["properties"]["body"]
        assert body["properties"]["status"]["enum"] == ["open", "pending", "closed"]

    def test_hash_is_of_the_uploaded_bytes(self, compiled: CompiledSpec) -> None:
        import hashlib

        assert compiled.spec_hash == hashlib.sha256(ACME_SPEC.read_bytes()).hexdigest()


class TestDocumentChecks:
    def test_json_is_accepted(self) -> None:
        spec = json.dumps({"openapi": "3.0.3", "info": {"title": "J"}, "paths": {"/a": {"get": {"operationId": "a"}}}})
        assert compile_spec(spec.encode()).operations[0].tool_name == "a"

    def test_json_is_read_without_the_yaml_parser(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The pure-Python YAML parser takes seconds over a large JSON spec such as Jira's.
        def no_yaml(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("a JSON spec was parsed as YAML")

        monkeypatch.setattr(yaml, "load", no_yaml)
        spec = json.dumps({"openapi": "3.0.3", "info": {"title": "J"}, "paths": {"/a": {"get": {"operationId": "a"}}}})
        assert compile_spec(("﻿" + spec).encode()).operations[0].tool_name == "a"

    def test_what_only_looks_like_json_is_read_as_yaml(self) -> None:
        spec = b"{openapi: 3.0.3, info: {title: J}, paths: {/a: {get: {operationId: a}}}}  # flow style\n"
        assert compile_spec(spec).operations[0].tool_name == "a"

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            (b"swagger: '2.0'\npaths: {}\n", "Swagger 2.0"),
            (b"openapi: 2.9.0\npaths: {/a: {get: {}}}\n", "3.0 and 3.1"),
            (b"openapi: 4.0.0\npaths: {/a: {get: {}}}\n", "3.0 and 3.1"),
            (b"- a\n- b\n", "must be a JSON or YAML object"),
            (b"openapi: 3.1.0\npaths: {}\n", "no paths"),
            (b"openapi: 3.1.0\npaths: [\n", "not valid JSON or YAML"),
            (b"openapi: 3.1.0\nx: &a [1]\ny: *a\npaths: {/a: {get: {}}}\n", "aliases"),
        ],
    )
    def test_unusable_documents_are_refused(self, raw: bytes, message: str) -> None:
        with pytest.raises(SpecError, match=message):
            compile_spec(raw)

    def test_the_error_says_where_and_spots_html_escapes(self) -> None:
        with pytest.raises(SpecError) as raised:
            compile_spec(b"openapi: 3.1.0\ninfo:\n  description: &gt;\n    An API.\npaths: {/a: {get: {}}}\n")
        message = str(raised.value)
        assert "(line 3, column 19)" in message and "HTML escapes such as &gt;" in message

    def test_size_limit(self) -> None:
        with pytest.raises(SpecError, match="larger than"):
            compile_spec(b" " * (MAX_SPEC_BYTES + 1))

    def test_a_spec_with_nothing_callable_is_refused(self) -> None:
        spec = _spec({"/a": {"get": {"parameters": [{"$ref": "https://evil.example/p.yaml#/x"}]}}})
        with pytest.raises(SpecError, match="no operations"):
            compile_spec(spec)


class TestOperations:
    def test_remote_ref_skips_only_that_operation(self) -> None:
        spec = _spec({
            "/a": {"get": {"operationId": "a", "responses": {}}},
            "/b": {"get": {"operationId": "b", "parameters": [{"$ref": "other.yaml#/components/parameters/X"}]}},
        })
        compiled = compile_spec(spec)
        assert [op.tool_name for op in compiled.operations] == ["a"]
        assert "GET /b: skipped" in compiled.warnings[0] and "never fetched" in compiled.warnings[0]

    def test_a_path_ref_to_something_else_skips_only_that_path(self) -> None:
        spec = _spec({
            "/a": {"get": {"operationId": "a"}},
            "/b": {"$ref": "#/info/title"},
        })
        compiled = compile_spec(spec)
        assert [op.tool_name for op in compiled.operations] == ["a"]
        assert compiled.warnings == ["/b: skipped, its $ref doesn't point at a path item."]

    def test_an_operation_with_fields_of_the_wrong_type_is_skipped(self) -> None:
        spec = _spec({
            "/a": {"post": {"operationId": "a", "requestBody": {"content": {1: {"schema": {}}}}}},
            "/b": {"get": {"operationId": "b"}},
        })
        compiled = compile_spec(spec)
        assert [op.tool_name for op in compiled.operations] == ["b"]
        assert compiled.warnings == ["POST /a: skipped, some of its fields have the wrong type."]

    def test_remote_ref_inside_a_schema_skips_the_operation(self) -> None:
        spec = _spec({
            "/a": {"get": {"operationId": "a"}},
            "/b": {"post": {"operationId": "b", "requestBody": {"content": {"application/json": {"schema": {"$ref": "https://x/s.json"}}}}}},
        })
        assert [op.tool_name for op in compile_spec(spec).operations] == ["a"]

    def test_recursive_schema_compiles(self) -> None:
        spec = _spec(
            {"/tree": {"post": {"operationId": "save_tree", "requestBody": {"required": True, "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/Node"}}}}}}},
            components={"schemas": {"Node": {"type": "object", "properties": {
                "name": {"type": "string"},
                "children": {"type": "array", "items": {"$ref": "#/components/schemas/Node"}},
            }}}},
        )
        op = _op(spec, "save_tree")
        assert op.input_schema["$defs"]["Node"]["properties"]["children"]["items"] == {"$ref": "#/$defs/Node"}
        flat = resolve_json_schema_refs(op.input_schema)
        assert "recursive reference" in json.dumps(flat)

    def test_property_named_like_a_keyword_still_has_its_ref_rewritten(self) -> None:
        spec = _spec(
            {"/a": {"post": {"operationId": "a", "requestBody": {"content": {"application/json": {"schema": {
                "type": "object", "properties": {"enum": {"$ref": "#/components/schemas/E"}}}}}}}}},
            components={"schemas": {"E": {"type": "string"}}},
        )
        body = _op(spec, "a").input_schema["properties"]["body"]
        assert body["properties"]["enum"] == {"$ref": "#/$defs/E"}

    def test_refs_inside_examples_are_left_alone(self) -> None:
        spec = _spec({"/a": {"get": {"operationId": "a", "parameters": [
            {"name": "q", "in": "query", "schema": {"type": "string", "example": {"$ref": "not-a-ref"}}}]}}})
        assert _op(spec, "a").input_schema["properties"]["q"]["example"] == {"$ref": "not-a-ref"}

    def test_tool_names_are_derived_cleaned_and_unique(self) -> None:
        long_id = "x" * 80
        spec = _spec({
            "/items/{id}": {"get": {"parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}]}},
            "/a": {"get": {"operationId": "list.items v2"}, "post": {"operationId": "list.items v2"}},
            "/b": {"get": {"operationId": long_id}},
        })
        names = [op.tool_name for op in compile_spec(spec).operations]
        assert names == ["get_items_id", "list_items_v2", "list_items_v2_2", "x" * 64]

    def test_required_non_json_body_skips_and_optional_is_dropped(self) -> None:
        spec = _spec({
            "/upload": {"post": {"operationId": "upload", "requestBody": {"required": True, "content": {"multipart/form-data": {}}}}},
            "/note": {"post": {"operationId": "note", "requestBody": {"content": {"text/plain": {}}}}},
        })
        compiled = compile_spec(spec)
        assert [op.tool_name for op in compiled.operations] == ["note"]
        assert compiled.operations[0].body_argument is None
        assert any("POST /upload: skipped" in w for w in compiled.warnings)
        assert any("optional request body" in w for w in compiled.warnings)

    def test_json_variants_count_as_json(self) -> None:
        spec = _spec({"/a": {"post": {"operationId": "a", "requestBody": {"content": {
            "application/vnd.api+json; charset=utf-8": {"schema": {"type": "object"}}}}}}})
        assert _op(spec, "a").body_argument == "body"

    def test_body_argument_avoids_a_parameter_named_body(self) -> None:
        spec = _spec({"/a": {"post": {"operationId": "a",
            "parameters": [{"name": "body", "in": "query", "schema": {"type": "string"}}],
            "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}}}}})
        assert _op(spec, "a").body_argument == "request_body"

    def test_reserved_headers_are_dropped(self) -> None:
        spec = _spec({"/a": {"get": {"operationId": "a", "parameters": [
            {"name": "Authorization", "in": "header", "schema": {"type": "string"}},
            {"name": "X-Trace", "in": "header", "schema": {"type": "string"}},
        ]}}})
        assert [p.name for p in _op(spec, "a").parameters] == ["X-Trace"]

    def test_operation_parameter_overrides_path_parameter(self) -> None:
        spec = _spec({"/a": {
            "parameters": [{"name": "q", "in": "query", "description": "path level", "schema": {"type": "string"}}],
            "get": {"operationId": "a", "parameters": [{"name": "q", "in": "query", "required": True, "description": "op level", "schema": {"type": "string"}}]},
        }})
        op = _op(spec, "a")
        assert op.input_schema["properties"]["q"]["description"] == "op level"
        assert op.input_schema["required"] == ["q"]

    def test_parameter_with_content_instead_of_schema(self) -> None:
        spec = _spec({"/a": {"get": {"operationId": "a", "parameters": [
            {"name": "filter", "in": "query", "content": {"application/json": {"schema": {"type": "object"}}}},
            {"name": "raw", "in": "query", "content": {"text/plain": {}}},
        ]}}})
        compiled = compile_spec(spec)
        assert list(compiled.operations[0].input_schema["properties"]) == ["filter"]
        assert "parameter raw left out" in compiled.warnings[0]

    def test_looping_parameter_ref_skips_the_operation(self) -> None:
        spec = _spec(
            {"/a": {"get": {"operationId": "a"}}, "/b": {"get": {"operationId": "b", "parameters": [{"$ref": "#/components/parameters/P"}]}}},
            components={"parameters": {"P": {"$ref": "#/components/parameters/Q"}, "Q": {"$ref": "#/components/parameters/P"}}},
        )
        compiled = compile_spec(spec)
        assert [op.tool_name for op in compiled.operations] == ["a"]
        assert "loops" in compiled.warnings[0]

    @pytest.mark.parametrize(
        ("method", "operation_id", "kind"),
        [("get", "a", "read"), ("head", "a", "read"), ("post", "a", "write"), ("put", "a", "write"),
         ("patch", "a", "write"), ("delete", "a", "destructive"), ("post", "removeUser", "destructive")],
    )
    def test_kind(self, method: str, operation_id: str, kind: str) -> None:
        assert _op(_spec({"/a": {method: {"operationId": operation_id}}}), operation_id).kind == kind

    def test_security_decides_whether_auth_is_sent(self) -> None:
        spec = _spec(
            {"/a": {"get": {"operationId": "a"}}, "/health": {"get": {"operationId": "health", "security": []}},
             "/opt": {"get": {"operationId": "opt", "security": [{}, {"k": []}]}}},
            security=[{"k": []}],
        )
        ops = {op.tool_name: op.requires_auth for op in compile_spec(spec).operations}
        assert ops == {"a": True, "health": False, "opt": False}


class TestSecuritySchemes:
    def test_oauth_with_two_flows_becomes_two_choices(self) -> None:
        spec = _spec({"/a": {"get": {"operationId": "a"}}}, components={"securitySchemes": {"o": {"type": "oauth2", "flows": {
            "clientCredentials": {"tokenUrl": "https://auth/t", "scopes": {"r": "read"}},
            "authorizationCode": {"tokenUrl": "https://auth/t", "authorizationUrl": "https://auth/a", "scopes": {}},
            "implicit": {"authorizationUrl": "https://auth/a", "scopes": {}},
        }}}})
        schemes = compile_spec(spec).auth_schemes
        assert [(s.name, s.type) for s in schemes] == [
            ("o.clientCredentials", AuthType.OAUTH2_CLIENT_CREDENTIALS),
            ("o.authorizationCode", AuthType.OAUTH2_AUTHORIZATION_CODE),
        ]
        assert schemes[1].authorization_url == "https://auth/a"

    def test_scopes_written_as_a_list_are_ignored(self) -> None:
        spec = _spec({"/a": {"get": {"operationId": "a"}}}, components={"securitySchemes": {"o": {"type": "oauth2", "flows": {
            "clientCredentials": {"tokenUrl": "https://auth/t", "scopes": ["read"]},
        }}}})
        assert compile_spec(spec).auth_schemes[0].scopes == {}

    def test_unsupported_schemes_are_reported(self) -> None:
        spec = _spec({"/a": {"get": {"operationId": "a"}}}, components={"securitySchemes": {
            "oidc": {"type": "openIdConnect", "openIdConnectUrl": "https://x"},
            "digest": {"type": "http", "scheme": "digest"},
            "key": {"type": "apiKey", "in": "query", "name": "k"},
        }})
        compiled = compile_spec(spec)
        assert [(s.name, s.location) for s in compiled.auth_schemes] == [("key", "query")]
        assert len(compiled.warnings) == 2
