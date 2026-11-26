"""Anthropic's Messages API on AWS Bedrock and Google Vertex AI (E3c).

Both are ``AnthropicTransport`` with the Anthropic SDK's cloud client: the same
``messages.create`` / ``messages.stream`` calls, signed with SigV4 (Bedrock,
through ``botocore``) or authorised with a service account's access token
(Vertex, through ``google-auth``). Every input is the account's own: no ambient
AWS or Google credential chain, no base URL from the environment, and the token
endpoint a service account file names is never trusted (a hostile one would be
sent signed assertions).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, cast

from harness_core.models.transports.anthropic import AnthropicTransport

if TYPE_CHECKING:
    from collections.abc import Mapping

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
VERTEX_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
# `anthropic.claude-sonnet-4-6-v1:0`, with a cross-region prefix (`us.`, `eu.`, `apac.`, `global.`) and a date.
_BEDROCK_MODEL = re.compile(r"^(?:[a-z]{2,6}\.)?anthropic\.(claude-[a-z0-9.-]+?)(?:-\d{8})?(?:-v\d+(?::\d+)?)?$")
# `claude-sonnet-4-6`, `claude-sonnet-4-5@20250929`.
_VERTEX_MODEL = re.compile(r"^(claude-[a-z0-9.-]+?)(?:@\d{8})?$")
# The Vertex hosts the SDK picks from the region, without its environment override.
_VERTEX_HOSTS: Mapping[str, str] = {
    "global": "https://aiplatform.googleapis.com/v1",
    "us": "https://aiplatform.us.rep.googleapis.com/v1",
    "eu": "https://aiplatform.eu.rep.googleapis.com/v1",
}


def bedrock_anthropic_model(model_id: str) -> str | None:
    """The Anthropic model a Bedrock model id names (for pricing), or None for another vendor's model."""
    found = _BEDROCK_MODEL.match(model_id)
    return found.group(1) if found else None


def vertex_anthropic_model(model_id: str) -> str | None:
    """The Anthropic model a Vertex model id names, or None for another publisher's model."""
    found = _VERTEX_MODEL.match(model_id)
    return found.group(1) if found else None


def bedrock_base_url(region: str) -> str:
    return f"https://bedrock-runtime.{region}.amazonaws.com"


def vertex_base_url(location: str) -> str:
    return _VERTEX_HOSTS.get(location) or f"https://{location}-aiplatform.googleapis.com/v1"


def anthropic_on_bedrock(
    *,
    region: str,
    access_key_id: str,
    secret_access_key: str,
    model: str,
    base_url: str | None = None,
    model_key: str | None = None,
) -> AnthropicTransport:
    """``base_url``: tests only (a fake runtime); otherwise the region's Bedrock runtime."""
    from anthropic.lib.bedrock import AsyncAnthropicBedrock

    client = AsyncAnthropicBedrock(
        aws_region=region,
        aws_access_key=access_key_id,
        aws_secret_key=secret_access_key,
        base_url=base_url or bedrock_base_url(region),
    )
    return AnthropicTransport(api_key="", model=model, model_key=model_key, client=client)


def anthropic_on_vertex(
    *,
    project_id: str,
    location: str,
    service_account_json: str,
    model: str,
    base_url: str | None = None,
    token_uri: str = GOOGLE_TOKEN_URI,
    model_key: str | None = None,
) -> AnthropicTransport:
    """``base_url``, ``token_uri``: tests only (fake endpoints); the account file's own ``token_uri`` is replaced."""
    from anthropic.lib.vertex import AsyncAnthropicVertex
    from google.oauth2 import service_account

    info = cast("dict[str, Any]", json.loads(service_account_json))
    credentials = service_account.Credentials.from_service_account_info(  # pyright: ignore[reportUnknownMemberType]
        {**info, "token_uri": token_uri}, scopes=[VERTEX_SCOPE]
    )
    client = AsyncAnthropicVertex(
        region=location,
        project_id=project_id,
        credentials=credentials,
        base_url=base_url or vertex_base_url(location),
    )
    return AnthropicTransport(api_key="", model=model, model_key=model_key, client=client)


__all__ = [
    "GOOGLE_TOKEN_URI",
    "VERTEX_SCOPE",
    "anthropic_on_bedrock",
    "anthropic_on_vertex",
    "bedrock_anthropic_model",
    "bedrock_base_url",
    "vertex_anthropic_model",
    "vertex_base_url",
]
