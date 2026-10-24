"""Configuration for identity forwarding.

Two layers, kept apart on purpose:

* ``IdentityGovernancePolicy`` is the org's rule for *what* identity data may
  leave PipesHub at all. It is set once, by an admin, and applies to every
  connection.
* ``ConnectionForwardingSettings`` is per model connection: whether it gets
  identity, in which wire format, and with which gateway-specific options.

Both parse tolerantly at runtime (a bad stored value must not take model calls
down) and strictly on admin writes (``strict=True`` raises ``SettingsError`` with
a message the admin can act on).
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.services.model_identity.sanitize import (
    is_forbidden_header_name,
    is_valid_header_name,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SUBJECT_SOURCES = ("userId", "email", "idpObjectId")
ON_MISSING_IDENTITY = ("service", "block")
QUERY_EMBEDDING_IDENTITY = ("user", "service")
FORWARDING_MODES = ("headers", "template", "signedJwt")
PRESETS = ("litellm", "portkey", "helicone", "cloudflare", "apim", "custom")
SERVICE_AUTH = ("apiKey", "jwt")

DEFAULT_SERVICE_PREFIX = "svc:pipeshub"
DEFAULT_MAX_FORWARDED_GROUPS = 50
MAX_FORWARDED_GROUPS_LIMIT = 500

ALLOWED_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "SUBJECT",
        "EMAIL",
        "GROUP_IDS",
        "ORG_ID",
        "WORKLOAD",
        "REQUEST_ID",
        "CONVERSATION_ID",
        "AGENT_ID",
    }
)
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_]+)\s*\}\}")
MAX_TEMPLATES = 16


class SettingsError(ValueError):
    """An admin-supplied setting is not valid."""


def _choice(raw: Mapping[str, Any], key: str, allowed: tuple[str, ...], default: str, *, strict: bool) -> str:
    value = raw.get(key, default)
    if value in allowed:
        return value
    if strict:
        raise SettingsError(f"{key} must be one of {', '.join(allowed)}")
    logger.warning("ignoring invalid %s=%r, using %s", key, value, default)
    return default


def _flag(raw: Mapping[str, Any], key: str, *, default: bool, strict: bool) -> bool:
    value = raw.get(key, default)
    if isinstance(value, bool):
        return value
    if strict:
        raise SettingsError(f"{key} must be true or false")
    return default


def _as_mapping(raw: Any, name: str, *, strict: bool) -> Mapping[str, Any]:  # noqa: ANN401 - a value of unknown shape (JSON or user input)
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return raw
    if strict:
        raise SettingsError(f"{name} must be an object")
    return {}


@dataclass(frozen=True, slots=True)
class IdentityGovernancePolicy:
    subject_source: str = "userId"
    include_email: bool = False
    include_display_name: bool = False
    groups_enabled: bool = True
    # Empty means every group is forwarded, up to ``max_forwarded_groups``.
    groups_allow_list: frozenset[str] = frozenset()
    max_forwarded_groups: int = DEFAULT_MAX_FORWARDED_GROUPS
    pseudonymize: bool = False
    on_missing_identity: str = "service"
    query_embedding_identity: str = "user"
    service_identity_prefix: str = DEFAULT_SERVICE_PREFIX
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def parse(cls, raw: Any, *, strict: bool = False) -> "IdentityGovernancePolicy":  # noqa: ANN401 - a value of unknown shape (JSON or user input)
        data = _as_mapping(raw, "identity policy", strict=strict)
        groups = _as_mapping(data.get("groups"), "groups", strict=strict)

        allow_raw = groups.get("allowList", [])
        if not isinstance(allow_raw, (list, tuple, set, frozenset)):
            if strict:
                raise SettingsError("groups.allowList must be a list of group ids")
            allow_raw = []
        allow = frozenset(str(item).strip() for item in allow_raw if str(item).strip())

        max_raw = groups.get("maxForwarded", DEFAULT_MAX_FORWARDED_GROUPS)
        if isinstance(max_raw, bool) or not isinstance(max_raw, int) or not 1 <= max_raw <= MAX_FORWARDED_GROUPS_LIMIT:
            if strict:
                raise SettingsError(f"groups.maxForwarded must be between 1 and {MAX_FORWARDED_GROUPS_LIMIT}")
            max_raw = DEFAULT_MAX_FORWARDED_GROUPS

        prefix = data.get("serviceIdentityPrefix", DEFAULT_SERVICE_PREFIX)
        if not isinstance(prefix, str) or not prefix.strip() or len(prefix) > 64:
            if strict:
                raise SettingsError("serviceIdentityPrefix must be 1 to 64 characters")
            prefix = DEFAULT_SERVICE_PREFIX

        return cls(
            subject_source=_choice(data, "subjectSource", SUBJECT_SOURCES, "userId", strict=strict),
            include_email=_flag(data, "includeEmail", default=False, strict=strict),
            include_display_name=_flag(data, "includeDisplayName", default=False, strict=strict),
            groups_enabled=_flag(groups, "enabled", default=True, strict=strict),
            groups_allow_list=allow,
            max_forwarded_groups=max_raw,
            pseudonymize=_flag(data, "pseudonymize", default=False, strict=strict),
            on_missing_identity=_choice(data, "onMissingIdentity", ON_MISSING_IDENTITY, "service", strict=strict),
            query_embedding_identity=_choice(
                data, "queryEmbeddingIdentity", QUERY_EMBEDDING_IDENTITY, "user", strict=strict
            ),
            service_identity_prefix=prefix.strip(),
            schema_version=SCHEMA_VERSION,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "subjectSource": self.subject_source,
            "includeEmail": self.include_email,
            "includeDisplayName": self.include_display_name,
            "groups": {
                "enabled": self.groups_enabled,
                "allowList": sorted(self.groups_allow_list),
                "maxForwarded": self.max_forwarded_groups,
            },
            "pseudonymize": self.pseudonymize,
            "onMissingIdentity": self.on_missing_identity,
            "queryEmbeddingIdentity": self.query_embedding_identity,
            "serviceIdentityPrefix": self.service_identity_prefix,
        }


DEFAULT_GOVERNANCE = IdentityGovernancePolicy()


def _parse_templates(raw: Any, *, strict: bool) -> tuple[tuple[str, str], ...]:  # noqa: ANN401 - a value of unknown shape (JSON or user input)
    if raw is None:
        return ()
    if not isinstance(raw, Mapping):
        if strict:
            raise SettingsError("headerTemplates must be an object of header name to value")
        return ()
    if len(raw) > MAX_TEMPLATES:
        if strict:
            raise SettingsError(f"at most {MAX_TEMPLATES} header templates are allowed")
        raw = dict(list(raw.items())[:MAX_TEMPLATES])

    out = []
    for raw_name, template in raw.items():
        name = str(raw_name).strip()
        problem = _template_problem(name, template)
        if problem is not None:
            if strict:
                raise SettingsError(problem)
            logger.warning("dropping header template %r: %s", name, problem)
            continue
        out.append((name, template))
    return tuple(sorted(out))


def _template_problem(name: str, template: Any) -> str | None:  # noqa: ANN401 - a value of unknown shape (JSON or user input)
    if not is_valid_header_name(name):
        return f"{name!r} is not a valid header name"
    if is_forbidden_header_name(name):
        return f"header {name} cannot be set by a template"
    if not isinstance(template, str) or len(template) > 1024:
        return f"the template for {name} must be text of at most 1024 characters"
    for placeholder in PLACEHOLDER_RE.findall(template):
        if placeholder not in ALLOWED_PLACEHOLDERS:
            return (
                f"{{{{{placeholder}}}}} in {name} is not a known placeholder; "
                f"use one of {', '.join(sorted(ALLOWED_PLACEHOLDERS))}"
            )
    if "{{" in PLACEHOLDER_RE.sub("", template):
        return f"the template for {name} has an unclosed placeholder"
    return None


# Keys the fingerprint, which is only ever compared within this process: it is
# change detection for memo keys, not password storage.
_FINGERPRINT_SALT = secrets.token_bytes(16)


@dataclass(frozen=True, slots=True)
class ConnectionForwardingSettings:
    enabled: bool = False
    mode: str = "headers"
    preset: str = "litellm"
    header_templates: tuple[tuple[str, str], ...] = ()
    jwt_audience: str | None = None
    service_auth: str = "apiKey"
    service_api_key: str | None = field(default=None, repr=False)
    schema_version: int = SCHEMA_VERSION
    fingerprint: str = field(default="", init=False, compare=False)

    def __post_init__(self) -> None:
        # Part of every memo key, so it is computed once here. The service API
        # key is keyed into the digest rather than stored, so it never sits in a
        # cache key or a log.
        material = json.dumps(
            [
                self.enabled,
                self.mode,
                self.preset,
                self.header_templates,
                self.jwt_audience,
                self.service_auth,
                self.service_api_key or "",
            ],
            separators=(",", ":"),
        )
        digest = hmac.new(_FINGERPRINT_SALT, material.encode(), "sha256").hexdigest()
        object.__setattr__(self, "fingerprint", digest[:16])

    @classmethod
    def parse(cls, raw: Any, *, strict: bool = False) -> "ConnectionForwardingSettings":  # noqa: ANN401 - a value of unknown shape (JSON or user input)
        if raw is None:
            return cls()
        data = _as_mapping(raw, "identityForwarding", strict=strict)
        mode = _choice(data, "mode", FORWARDING_MODES, "headers", strict=strict)
        templates = _parse_templates(data.get("headerTemplates"), strict=strict)
        preset = _choice(data, "preset", PRESETS, "litellm", strict=strict)

        audience = data.get("jwtAudience")
        if audience is not None and (not isinstance(audience, str) or len(audience) > 256):
            if strict:
                raise SettingsError("jwtAudience must be text of at most 256 characters")
            audience = None
        audience = audience.strip() if isinstance(audience, str) and audience.strip() else None

        api_key = data.get("serviceApiKey")
        if api_key is not None and not isinstance(api_key, str):
            if strict:
                raise SettingsError("serviceApiKey must be text")
            api_key = None

        enabled = _flag(data, "enabled", default=False, strict=strict)
        if strict and enabled and mode == "template" and not templates:
            raise SettingsError("template mode needs at least one header template")
        if strict and enabled and mode == "signedJwt" and not audience:
            raise SettingsError("signedJwt mode needs jwtAudience")

        return cls(
            enabled=enabled,
            mode=mode,
            preset=preset,
            header_templates=templates,
            jwt_audience=audience,
            service_auth=_choice(data, "serviceAuth", SERVICE_AUTH, "apiKey", strict=strict),
            service_api_key=api_key or None,
            schema_version=SCHEMA_VERSION,
        )

    def to_dict(self, *, redact: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schemaVersion": self.schema_version,
            "enabled": self.enabled,
            "mode": self.mode,
            "preset": self.preset,
            "headerTemplates": dict(self.header_templates),
            "jwtAudience": self.jwt_audience,
            "serviceAuth": self.service_auth,
        }
        if self.service_api_key:
            out["serviceApiKey"] = "***" if redact else self.service_api_key
        return out


DISABLED_CONNECTION = ConnectionForwardingSettings()
