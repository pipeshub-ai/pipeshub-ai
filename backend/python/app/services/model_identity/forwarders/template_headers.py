"""Header templates for gateways other than LiteLLM.

An admin writes ``{"X-User": "{{SUBJECT}}"}``; only the placeholders in
``ALLOWED_PLACEHOLDERS`` exist, and each is filled from the already-minimised
``ForwardedIdentity``. A template cannot reach a field governance removed.

Presets fill in the headers a known gateway reads when the admin gave no
templates. Presets that carry JSON build it with ``json.dumps`` so no value can
break out of a string. Explicit templates are added on top of the preset's
headers and win on a name clash.
"""

from __future__ import annotations

import json
from typing import Callable, Dict, List, Tuple

from app.services.model_identity.forwarders.base import HeaderSet
from app.services.model_identity.forwarders.litellm_headers import service_key_headers
from app.services.model_identity.projector import ForwardedIdentity
from app.services.model_identity.sanitize import clean_header_value, join_capped
from app.services.model_identity.settings import (
    PLACEHOLDER_RE,
    ConnectionForwardingSettings,
)

_JSON_UNSAFE = str.maketrans("", "", '"\\')


def placeholder_values(identity: ForwardedIdentity) -> Dict[str, str]:
    values = {
        "SUBJECT": identity.subject,
        "EMAIL": identity.email or "",
        "GROUP_IDS": join_capped(identity.group_ids),
        "ORG_ID": identity.org_id,
        "WORKLOAD": identity.workload,
        "REQUEST_ID": identity.request_id or "",
        "CONVERSATION_ID": identity.conversation_id or "",
        "AGENT_ID": identity.agent_id or "",
    }
    # Quotes and backslashes are dropped so a value is safe inside a JSON string
    # in a template such as '{"user":"{{SUBJECT}}"}'.
    return {key: value.translate(_JSON_UNSAFE) for key, value in values.items()}


def render_template(template: str, values: Dict[str, str]) -> str:
    return clean_header_value(PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), ""), template))


def _portkey(identity: ForwardedIdentity) -> List[Tuple[str, str]]:
    metadata = {
        "_user": identity.subject,
        "org_id": identity.org_id,
        "workload": identity.workload,
    }
    if identity.group_ids:
        metadata["group_ids"] = join_capped(identity.group_ids, max_length=512)
    return [("x-portkey-metadata", json.dumps(metadata, separators=(",", ":")))]


def _helicone(identity: ForwardedIdentity) -> List[Tuple[str, str]]:
    headers = [
        ("Helicone-User-Id", identity.subject),
        ("Helicone-Property-Workload", identity.workload),
        ("Helicone-Property-Org", identity.org_id),
    ]
    if identity.group_ids:
        headers.append(("Helicone-Property-Groups", join_capped(identity.group_ids, max_length=512)))
    return headers


def _cloudflare(identity: ForwardedIdentity) -> List[Tuple[str, str]]:
    # AI Gateway accepts at most five flat entries.
    metadata = {"userid": identity.subject, "org": identity.org_id, "workload": identity.workload}
    if identity.group_ids:
        metadata["groups"] = join_capped(identity.group_ids, max_length=512)
    return [("cf-aig-metadata", json.dumps(metadata, separators=(",", ":")))]


def _apim(identity: ForwardedIdentity) -> List[Tuple[str, str]]:
    # Azure API Management reads these in a policy, for example as the counter
    # key of llm-token-limit.
    headers = [("x-pipeshub-counter-key", identity.subject), ("x-pipeshub-org", identity.org_id)]
    if identity.group_ids:
        headers.append(("x-pipeshub-user-groups", join_capped(identity.group_ids)))
    return headers


_PRESETS: Dict[str, Callable[[ForwardedIdentity], List[Tuple[str, str]]]] = {
    "portkey": _portkey,
    "helicone": _helicone,
    "cloudflare": _cloudflare,
    "apim": _apim,
}


class TemplateHeadersForwarder:
    name = "template-headers"

    def requires_groups(self, settings: ConnectionForwardingSettings) -> bool:
        if any("GROUP_IDS" in template for _, template in settings.header_templates):
            return True
        return settings.preset in _PRESETS and not settings.header_templates

    def render(
        self, identity: ForwardedIdentity, settings: ConnectionForwardingSettings
    ) -> HeaderSet:
        out: Dict[str, str] = {}
        preset = _PRESETS.get(settings.preset)
        if preset is not None:
            for name, value in preset(identity):
                out[name.lower()] = clean_header_value(value)

        values = placeholder_values(identity)
        for name, template in settings.header_templates:
            rendered = render_template(template, values)
            if rendered:
                out[name.lower()] = rendered

        headers = tuple(out.items()) + tuple(service_key_headers(identity, settings))
        return HeaderSet(set=headers)
