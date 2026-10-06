"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/skills."""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Callable

import requests
from pymongo import MongoClient

from helper.config import MONGO_DB_NAME, MONGO_URI
from helper.http.api_client import APIClient
from helper.second_user import SecondUser

if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol
    from helper.pipeshub_client import PipeshubClient

SKILLS_BASE = "/api/v1/skills"

# Well-formed kebab-case name that no test ever creates.
MISSING_SKILL_NAME = "spec-audit-missing-skill"
# Passes Node's path guard but fails the Python name rule ^[a-z0-9]+(-[a-z0-9]+)*$.
MALFORMED_SKILL_NAME = "Spec_Audit_Bad_Name"
# Decodes to "a/b": guardPathParams answers 400 before the request reaches Python.
UNSAFE_PATH_SEGMENT = "a%2Fb"
MISSING_CANDIDATE_ID = "spec-audit-missing-candidate"
MISSING_VERSION = "999.0.0"
MISSING_RESOURCE_PATH = "references/spec-audit-missing.md"
RESOURCE_PATH = "references/spec-audit.md"

SKILL_BODY_MARKER = "spec-audit-marker"

PLATFORM_SETTINGS = "/api/v1/configurationManager/platform/settings"
SKILLS_FEATURE_FLAG = "ENABLE_SKILLS"
SKILLS_DISABLED_DETAIL = "Skills are disabled for this organization."
# A scope the suite's OAuth client may hold that grants neither skill:read nor skill:write.
UNRELATED_SCOPE = "org:read"
# Body that the JSON body parser cannot parse.
MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}
# The four import routes share one limiter: this many calls per user per minute.
IMPORT_CALLS_PER_MINUTE = 10

# Small, pinned public npm packages with a valid SKILL.md (fetched live by the skills service).
NPM_MULTI_SKILL_PACKAGE = "@arcjet/skills@1.14.0"
NPM_MULTI_SKILL_PICK = "guard"
SINGLE_SKILL_TARBALL_URL = "https://registry.npmjs.org/@tanstack/ai-skills/-/ai-skills-0.1.14.tgz"

SeedSkill = Callable[..., dict[str, Any]]


def unique_skill_name() -> str:
    return f"spec-audit-{uuid.uuid4().hex[:10]}"


def skill_payload(name: str | None = None, **overrides: Any) -> dict[str, Any]:
    """A valid POST / and PUT /:name body (Python SkillWriteRequest)."""
    payload: dict[str, Any] = {
        "description": "Seeded by the skills spec audit",
        "body": f"# Spec audit\n\nUse this skill for nothing. {SKILL_BODY_MARKER}\n",
    }
    if name is not None:
        payload["name"] = name
    payload.update(overrides)
    return payload


def skill_md(name: str) -> str:
    """A SKILL.md document, as POST /import/finalize expects in ``content``."""
    return (
        f"---\nname: {name}\ndescription: Seeded by the skills spec audit\n---\n"
        f"# Spec audit\n\n{SKILL_BODY_MARKER}\n"
    )


def mint_scoped_token(base_url: str, scope: str, timeout: float = 60) -> str:
    """A client-credentials token of the suite's own OAuth client, limited to ``scope``."""
    resp = requests.post(
        f"{base_url}/api/v1/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "client_id": os.environ["CLIENT_ID"],
            "client_secret": os.environ["CLIENT_SECRET"],
            "scope": scope,
        },
        timeout=timeout,
    )
    assert resp.status_code == 200, f"minting a {scope} token: {resp.status_code} {resp.text[:300]}"
    assert resp.json().get("scope") == scope, f"asked for {scope!r}, got {resp.json().get('scope')!r}"
    return str(resp.json()["access_token"])


def forget_access_token(token: str) -> None:
    """Remove the stored row of an access token this suite minted (there is no delete API)."""
    client: MongoClient[dict[str, Any]] = MongoClient(MONGO_URI)
    try:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        client[MONGO_DB_NAME]["oauthAccessTokens"].delete_one({"tokenHash": token_hash})
    finally:
        client.close()


def _platform_settings(client: PipeshubClient) -> dict[str, Any]:
    resp = client.request("GET", PLATFORM_SETTINGS)
    assert resp.status_code == 200, f"reading platform settings: {resp.status_code} {resp.text[:300]}"
    return dict(resp.json())


def _post_platform_flags(client: PipeshubClient, settings: dict[str, Any], flags: dict[str, Any]) -> None:
    # The POST replaces the whole settings object, so the size limit is sent back unchanged.
    max_bytes = settings.get("fileUploadMaxSizeBytes") or 30 * 1024 * 1024
    resp = client.request(
        "POST", PLATFORM_SETTINGS, json={"fileUploadMaxSizeBytes": max_bytes, "featureFlags": flags}
    )
    assert resp.status_code == 200, f"writing platform settings: {resp.status_code} {resp.text[:300]}"


@contextmanager
def skills_feature_disabled(client: PipeshubClient) -> Iterator[None]:
    """Turn the org-wide ENABLE_SKILLS flag off for the body of the ``with``, then put it back.

    The restore re-reads the settings first, so a flag another suite flipped meanwhile survives.
    """
    before = _platform_settings(client)
    flags = dict(before.get("featureFlags") or {})
    had_flag, original = SKILLS_FEATURE_FLAG in flags, flags.get(SKILLS_FEATURE_FLAG)
    _post_platform_flags(client, before, {**flags, SKILLS_FEATURE_FLAG: False})
    try:
        yield
    finally:
        current = _platform_settings(client)
        restored = dict(current.get("featureFlags") or {})
        if had_flag:
            restored[SKILLS_FEATURE_FLAG] = original
        else:
            restored.pop(SKILLS_FEATURE_FLAG, None)
        _post_platform_flags(client, current, restored)


async def seed_candidate(graph: GraphProviderProtocol, org_id: str, **fields: Any) -> dict[str, Any]:
    """Queue one learning-loop candidate the way ``GraphSkillStore.queue_candidate`` does.

    No route creates a candidate: the learning loop does after an agent run. Returns the
    stored document; ``fields`` override its camelCase fields.
    """
    from app.config.constants.arangodb import CollectionNames  # noqa: PLC0415 - needs backend on sys.path
    from app.utils.time_conversion import get_epoch_timestamp_in_ms  # noqa: PLC0415

    candidate_id = f"spec-audit-candidate-{uuid.uuid4().hex[:10]}"
    doc: dict[str, Any] = {
        "id": candidate_id,
        "orgId": org_id,
        "candidateId": candidate_id,
        "name": unique_skill_name(),
        "description": "Proposed by the skills spec audit",
        "body": f"# Spec audit candidate\n\n{SKILL_BODY_MARKER}\n",
        "category": "spec-audit",
        "subcategory": None,
        "tags": ["spec-audit"],
        "status": "pending",
        "sourceTrajectorySummary": "Seeded by the skills spec audit",
        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
        **fields,
    }
    await graph.batch_upsert_nodes([doc], CollectionNames.AGENT_SKILL_CANDIDATES.value)
    return doc


async def remove_candidate(graph: GraphProviderProtocol, candidate_id: str) -> None:
    from app.config.constants.arangodb import CollectionNames  # noqa: PLC0415

    await graph.delete_nodes([candidate_id], CollectionNames.AGENT_SKILL_CANDIDATES.value)


class SkillsClient(APIClient):
    """Client for /api/v1/skills, acting as the shared org admin."""

    BASE = SKILLS_BASE

    def list(self, *, auth: bool = True, **params: Any) -> requests.Response:
        return self.get("/", auth=auth, params=params)

    def create(self, payload: dict[str, Any], *, auth: bool = True) -> requests.Response:
        return self.post("/", auth=auth, json=payload)

    def fetch(self, name: str, *, auth: bool = True) -> requests.Response:
        return self.get(f"/{name}", auth=auth)

    def remove(self, name: str, *, auth: bool = True, **params: Any) -> requests.Response:
        return self.delete(f"/{name}", auth=auth, params=params)


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a skills route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    headers = {"Authorization": f"Bearer {user.token}", **kwargs.pop("headers", {})}
    return requests.request(
        method, f"{user.base_url}{SKILLS_BASE}{path}", headers=headers, **kwargs
    )
