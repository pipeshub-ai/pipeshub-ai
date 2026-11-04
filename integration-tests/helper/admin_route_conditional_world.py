"""The things the conditional-admin cases are aimed at, made for one run.

``helper/admin_route_conditional_table.py`` names them (``WORLD_GROUPS``). A group is
created the first time a case asks for one of its keys, by the people the
cases then call the routes as: the org admin, a member who creates things of
their own, and a second member who created nothing. Everything is named
``it-cond-...`` and removed when the run ends, newest first; nothing the run
did not create is touched.

A group that cannot be made fails (or skips) every case that needs it and no
others, and is not tried again.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, quote, urlparse

import requests

from helper.admin_route_conditional_table import (
    ADMIN,
    AGENT_CONNECTOR_AUTH,
    AGENT_CONNECTOR_TYPE,
    CONNECTOR_SERVICE,
    CREATOR,
    FRESH_KEYS,
    INDEXING_SERVICE,
    KEYED_TOOLSET_FIELD,
    KEYED_TOOLSET_TYPE,
    MCP_SERVER_URL,
    MEMBER,
    OAUTH_CONNECTOR_TYPE,
    OAUTH_TOOLSET_TYPE,
    PLAIN_CONNECTOR_TYPE,
    SERVICE_FOR_ADMIN,
    SERVICE_FOR_MEMBER,
    WORLD_GROUPS,
)
from helper import suite_extensions
from helper.connector_service import CONNECTOR_URL_ENV, connector_service_url
from helper.source_credentials import source_unavailable

if TYPE_CHECKING:
    from helper.second_user import SecondUser

logger = logging.getLogger("conditional-admin")

PREFIX = "it-cond"
# Enough of a reply to find an id or a secret in it.
_BODY_LIMIT = 262_144
_CONNECTORS = "/api/v1/connectors"
_TOOLSETS = "/api/v1/toolsets"
_MCP = "/api/v1/mcp-servers/instances"
_AGENTS = "/api/v1/agents"
_SERVICE_ACCOUNTS = "/api/v1/service-accounts"
_USERS = "/api/v1/users"
_SKILLS = "/api/v1/skills"
_TEAMS = "/api/v1/teams"
_GONE_TIMEOUT_SEC = 120.0
_GONE_POLL_SEC = 2.0
MCP_FLAG = "ENABLE_MCP"
SKILLS_FLAG = "ENABLE_SKILLS"
SCOPED_SECRET_ENV = "SCOPED_JWT_SECRET"
# The gateway gives a skills call 30 seconds. Listing skills in a new org loads
# the built-in ones first, which can take longer than that the first time.
_SKILLS_READY_TIMEOUT_SEC = 300.0
_SKILLS_READY_POLL_SEC = 5.0
# Creating a service account publishes an event the connector service
# consumes before the account is in the graph the directory reads.
_DIRECTORY_READY_TIMEOUT_SEC = 180.0
_DIRECTORY_READY_POLL_SEC = 3.0


@dataclass(frozen=True)
class Reply:
    status: int
    text: str


def send(
    method: str,
    url: str,
    token: str,
    *,
    params: dict[str, str] | None = None,
    body: Any = None,
    timeout: float = 60,
) -> Reply:
    """One request as the holder of ``token``; the status and the start of the reply."""
    kwargs: dict[str, Any] = {
        "headers": {"Authorization": f"Bearer {token}"}, "timeout": timeout, "stream": True,
    }
    if params:
        kwargs["params"] = params
    if body is not None:
        kwargs["json"] = body
    with requests.request(method, url, **kwargs) as resp:
        text = resp.raw.read(_BODY_LIMIT, decode_content=True).decode("utf-8", "replace")
        return Reply(resp.status_code, text)


def oauth_state(connector_id: str) -> str:
    """The ``state`` the connector OAuth callback reads its connector id from."""
    raw = json.dumps({"state": "it-state", "connector_id": connector_id})
    return base64.urlsafe_b64encode(raw.encode()).decode()


class _AsUser:
    """What ``KBClient`` needs of a client, for one person's session token."""

    def __init__(self, base_url: str, token: str, timeout: float) -> None:
        self.base_url = base_url
        self.timeout_seconds = timeout
        self._access_token = token

    def _ensure_access_token(self) -> None:
        return

    @property
    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    def request(self, method: str, path: str, *, auth: bool = True, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout_seconds)
        headers = {**(self.auth_headers if auth else {}), **(kwargs.pop("headers", None) or {})}
        return requests.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)


class World:
    def __init__(
        self,
        *,
        gateway_url: str,
        admin_token: Callable[[], str],
        creator: SecondUser,
        member: SecondUser,
        settings_client: Any,
        timeout: float = 60,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.connector_url = connector_service_url(self.gateway_url)
        self._admin_token = admin_token
        self._people = {CREATOR: creator, MEMBER: member}
        self._settings_client = settings_client
        self._timeout = timeout
        self._values: dict[str, str] = {}
        self._failed: dict[str, BaseException] = {}
        self._undo: list[tuple[str, Callable[[], None]]] = []
        # Connectors made for one case each, and every connector whose deletion
        # is still running: (id, who can still see it).
        self._fresh: list[tuple[str, str]] = []
        self._deleting: list[tuple[str, str]] = []
        self._connector_service_checked = False

    # ------------------------------------------------------------------ lookups

    def value(self, key: str) -> str:
        if key in FRESH_KEYS:
            return self._fresh_value(key)
        if key not in self._values:
            group = next((name for name, keys in WORLD_GROUPS.items() if key in keys), None)
            if group is None:
                raise KeyError(f"{key!r} is not something the test world holds")
            if group in self._failed:
                raise self._failed[group]
            try:
                built = getattr(self, f"_build_{group}")()
            except BaseException as exc:  # pytest's skip and fail are not Exceptions
                self._failed[group] = exc
                raise
            if set(built) != set(WORLD_GROUPS[group]):
                raise AssertionError(
                    f"The {group!r} group made {sorted(built)}, not {sorted(WORLD_GROUPS[group])}."
                )
            self._values.update(built)
            logger.info("Made the %r group for the conditional-admin cases", group)
        return self._values[key]

    def token(self, caller: str) -> str:
        if caller == ADMIN:
            return self._admin_token()
        if caller in self._people:
            return self._people[caller].token
        from app.config.constants.service import TokenScopes  # noqa: PLC0415 - backend path is set by conftest

        if caller == INDEXING_SERVICE:
            return self._service_token(TokenScopes.CONNECTOR_SIGNED_URL.value, None)
        if caller == SERVICE_FOR_ADMIN:
            return self._service_token(TokenScopes.RECORD_CONTENT.value, self._admin_claims()["userId"])
        if caller == SERVICE_FOR_MEMBER:
            return self._service_token(TokenScopes.RECORD_CONTENT.value, self._people[MEMBER].user_id)
        raise KeyError(f"unknown caller {caller!r}")

    def url(self, service: str) -> str:
        if service != CONNECTOR_SERVICE:
            return self.gateway_url
        if not self._connector_service_checked:
            try:
                requests.get(f"{self.connector_url}/health", timeout=10)
            except requests.RequestException as exc:
                source_unavailable(
                    f"The connector service is not reachable at {self.connector_url} "
                    f"({type(exc).__name__}), so its own routes cannot be called.",
                    secrets=[CONNECTOR_URL_ENV],
                )
            self._connector_service_checked = True
        return self.connector_url

    # ---------------------------------------------------------------- plumbing

    def _admin_claims(self) -> dict[str, Any]:
        from helper.local_auth import jwt_claims  # noqa: PLC0415 - keeps this module importable without the stack's env

        claims = jwt_claims(self._admin_token())
        if not claims.get("userId") or not claims.get("orgId"):
            raise RuntimeError("The admin's session token carries no userId / orgId.")
        return claims

    def _service_token(self, scope: str, user_id: str | None) -> str:
        from app.utils.jwt import mint_service_token  # noqa: PLC0415 - backend path is set by conftest

        secret = os.getenv(SCOPED_SECRET_ENV, "").strip()
        if not secret:
            source_unavailable(
                f"{SCOPED_SECRET_ENV} is not set, so no service token can be signed the way "
                "the stack's own services sign them.",
                secrets=[SCOPED_SECRET_ENV],
            )
        claims: dict[str, Any] = {"orgId": self._admin_claims()["orgId"], "scopes": [scope]}
        if user_id:
            claims["userId"] = user_id
        return mint_service_token(secret, claims)

    def _unique_name(self, kind: str = "connector") -> str:
        return f"{PREFIX}-{kind}-{uuid.uuid4().hex[:10]}"

    def _json(
        self, caller: str, method: str, path: str, what: str, *, ok: tuple[int, ...] = (200,), **kwargs: Any
    ) -> dict[str, Any]:
        """A setup or teardown call that must work; the parsed reply."""
        kwargs.setdefault("timeout", self._timeout)
        resp = requests.request(
            method, f"{self.gateway_url}{path}",
            headers={"Authorization": f"Bearer {self.token(caller)}"}, **kwargs,
        )
        if resp.status_code not in ok:
            raise RuntimeError(f"{what} returned HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            body = resp.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    def _later(self, what: str, undo: Callable[[], None]) -> None:
        self._undo.append((what, undo))

    # -------------------------------------------------------------- connectors

    def _create_connector(self, caller: str, connector_type: str, scope: str, **extra: str) -> tuple[str, str]:
        name = self._unique_name()
        body = self._json(
            caller, "POST", f"{_CONNECTORS}/", f"Creating a {scope} {connector_type} connector",
            json={"connectorType": connector_type, "instanceName": name, "scope": scope, **extra},
        )
        connector_id = str((body.get("connector") or {}).get("connectorId") or "")
        if not connector_id:
            raise RuntimeError(f"Creating a {scope} {connector_type} connector returned no id.")
        return connector_id, name

    def _delete_connector(self, connector_id: str, visible_to: str) -> None:
        """Ask for the deletion as the admin, who may delete any connector in the org."""
        reply = send("DELETE", f"{self.gateway_url}{_CONNECTORS}/{connector_id}", self.token(ADMIN),
                     timeout=self._timeout)
        # 404: already gone. 409: a case's own delete is still running.
        if reply.status not in (202, 404, 409):
            raise RuntimeError(
                f"Deleting test connector {connector_id} returned HTTP {reply.status}: {reply.text[:300]}"
            )
        self._deleting.append((connector_id, visible_to))

    def _await_deletions(self) -> None:
        """Deleting runs in the background; a member removed before it ends could strand it."""
        deadline = time.monotonic() + _GONE_TIMEOUT_SEC
        pending = list(self._deleting)
        self._deleting.clear()
        while pending:
            pending = [
                (connector_id, caller) for connector_id, caller in pending
                if send("GET", f"{self.gateway_url}{_CONNECTORS}/{connector_id}", self.token(caller),
                        timeout=self._timeout).status != 404
            ]
            if pending and time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Test connectors still there {_GONE_TIMEOUT_SEC:.0f}s after deleting them: "
                    f"{[connector_id for connector_id, _ in pending]}"
                )
            if pending:
                time.sleep(_GONE_POLL_SEC)

    def _connector_pair(self, connector_type: str, **extra: str) -> tuple[tuple[str, str], tuple[str, str]]:
        team = self._create_connector(ADMIN, connector_type, "team", **extra)
        self._later(f"team connector {team[1]}", lambda: self._delete_connector(team[0], ADMIN))
        personal = self._create_connector(CREATOR, connector_type, "personal", **extra)
        self._later(f"personal connector {personal[1]}", lambda: self._delete_connector(personal[0], CREATOR))
        return team, personal

    def _build_identity(self) -> dict[str, str]:
        return {"org": str(self._admin_claims()["orgId"])}

    def _build_connectors(self) -> dict[str, str]:
        team, personal = self._connector_pair(PLAIN_CONNECTOR_TYPE)
        return {"team": team[0], "personal": personal[0]}

    def _build_listed(self) -> dict[str, str]:
        team, personal = self._connector_pair(AGENT_CONNECTOR_TYPE, authType=AGENT_CONNECTOR_AUTH)
        for caller, (connector_id, name) in ((ADMIN, team), (CREATOR, personal)):
            self._json(caller, "POST", f"{_CONNECTORS}/{connector_id}/toggle",
                       f"Switching agents on for {name}", json={"type": "agent"})
        return {
            "listed_team": team[0], "listed_team_name": team[1],
            "listed_personal": personal[0], "listed_personal_name": personal[1],
        }

    def _fresh_value(self, key: str) -> str:
        if key == "unique_name":
            return self._unique_name()
        if key == "fresh_toolset":
            made = self._create_toolset(self._unique_name("toolset"), KEYED_TOOLSET_TYPE, "API_TOKEN", {})
            # A case may have deleted it already.
            self._later(f"toolset {made['instanceName']}", lambda: self._delete_toolset(made["_id"]))
            return str(made["_id"])
        if key == "fresh_personal_toolset":
            return str(self._create_toolset(
                self._unique_name("toolset"), KEYED_TOOLSET_TYPE, "API_TOKEN", {}, caller=CREATOR, scope="personal",
            )["_id"])
        if key == "fresh_creator_team":
            return self._create_team(CREATOR, "a member's test team for one case")
        if key in ("fresh_mcp", "fresh_personal_mcp"):
            self._switch_on(MCP_FLAG)
            if key == "fresh_mcp":
                return self._create_mcp_server(ADMIN, shared=False)
            return self._create_mcp_server(CREATOR, shared=False, scope="personal")
        caller, scope = (ADMIN, "team") if key == "fresh_team" else (CREATOR, "personal")
        connector_id, _ = self._create_connector(caller, PLAIN_CONNECTOR_TYPE, scope)
        self._fresh.append((connector_id, caller))
        return connector_id

    def discard_fresh(self) -> None:
        """Remove the connectors made for the case that just ran."""
        fresh, self._fresh = self._fresh, []
        for connector_id, visible_to in fresh:
            self._delete_connector(connector_id, visible_to)

    # ------------------------------------------------------------ sign-in apps

    def _build_oauth(self) -> dict[str, str]:
        tag = uuid.uuid4().hex[:10]
        made = {
            "oauth_name": f"{PREFIX}-oauth-{tag}",
            "oauth_client_id": f"{PREFIX}-client-{tag}",
            "oauth_secret": f"{PREFIX}-secret-{tag}",
        }
        path = f"/api/v1/oauth/{OAUTH_CONNECTOR_TYPE}"
        body = self._json(
            ADMIN, "POST", path, f"Creating a {OAUTH_CONNECTOR_TYPE} sign-in app",
            json={
                "oauthInstanceName": made["oauth_name"],
                "config": {"clientId": made["oauth_client_id"], "clientSecret": made["oauth_secret"]},
                "baseUrl": "http://localhost",
            },
        )
        config_id = str((body.get("oauthConfig") or {}).get("_id") or "")
        if not config_id:
            raise RuntimeError(f"Creating a {OAUTH_CONNECTOR_TYPE} sign-in app returned no id.")
        self._later(
            f"sign-in app {made['oauth_name']}",
            lambda: self._json(ADMIN, "DELETE", f"{path}/{config_id}", "Deleting the test sign-in app",
                               ok=(200, 404)),
        )
        return {"oauth_config": config_id, **made}

    def _create_toolset(
        self, name: str, toolset_type: str, auth_type: str, auth: dict[str, str], *,
        caller: str = ADMIN, scope: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"instanceName": name, "toolsetType": toolset_type, "authType": auth_type, "authConfig": auth}
        if scope:
            payload["scope"] = scope
        body = self._json(caller, "POST", f"{_TOOLSETS}/instances", f"Creating a {toolset_type} toolset", json=payload)
        instance = body.get("instance") or {}
        if not instance.get("_id"):
            raise RuntimeError(f"Creating a {toolset_type} toolset returned no id.")
        if scope == "personal":
            self._later(f"personal toolset {name}", lambda: self._delete_personal_toolset(instance["_id"], caller))
        return instance

    def _delete_toolset(self, instance_id: str) -> None:
        self._json(ADMIN, "DELETE", f"{_TOOLSETS}/instances/{instance_id}", "Deleting a test toolset",
                   ok=(200, 404))

    def _delete_personal_toolset(self, instance_id: str, owner: str) -> None:
        """Only its owner can delete it. A case may have deleted it already, and a member who is not
        an admin is refused with 403 rather than 404 once it is gone, so look before deleting."""
        path = f"{_TOOLSETS}/instances/{instance_id}"
        reply = send("GET", f"{self.gateway_url}{path}", self.token(owner), timeout=self._timeout)
        if reply.status == 404:
            return
        if reply.status != 200:
            raise RuntimeError(f"Looking up a test personal toolset returned HTTP {reply.status}: {reply.text[:300]}")
        self._json(owner, "DELETE", path, "Deleting a test personal toolset")

    def _build_personal_toolsets(self) -> dict[str, str]:
        name = f"{PREFIX}-personal-toolset-{uuid.uuid4().hex[:10]}"
        instance = self._create_toolset(name, KEYED_TOOLSET_TYPE, "API_TOKEN", {}, caller=CREATOR, scope="personal")
        return {"personal_toolset": str(instance["_id"]), "personal_toolset_name": name}

    def _build_toolsets(self) -> dict[str, str]:
        tag = uuid.uuid4().hex[:10]
        made = {
            "toolset_name": f"{PREFIX}-toolset-{tag}",
            "toolset_key": f"{PREFIX}-key-{tag}",
            "toolset_oauth_name": f"{PREFIX}-toolset-oauth-{tag}",
            "toolset_client_id": f"{PREFIX}-toolset-client-{tag}",
            "toolset_secret": f"{PREFIX}-toolset-secret-{tag}",
        }
        keyed = self._create_toolset(
            made["toolset_name"], KEYED_TOOLSET_TYPE, "API_TOKEN", {KEYED_TOOLSET_FIELD: made["toolset_key"]},
        )
        self._later(f"toolset {made['toolset_name']}", lambda: self._delete_toolset(keyed["_id"]))
        # Creating an OAuth toolset with a client id and secret stores them as
        # a sign-in app named after the toolset.
        signed_in = self._create_toolset(
            made["toolset_oauth_name"], OAUTH_TOOLSET_TYPE, "OAUTH",
            {"clientId": made["toolset_client_id"], "clientSecret": made["toolset_secret"]},
        )
        config_id = signed_in.get("oauthConfigId")
        if config_id:
            self._later(
                f"toolset sign-in app {made['toolset_oauth_name']}",
                lambda: self._json(
                    ADMIN, "DELETE", f"{_TOOLSETS}/oauth-configs/{OAUTH_TOOLSET_TYPE}/{config_id}",
                    "Deleting the test toolset sign-in app", ok=(200, 404),
                ),
            )
        self._later(f"toolset {made['toolset_oauth_name']}", lambda: self._delete_toolset(signed_in["_id"]))
        if not config_id:
            raise RuntimeError("The OAuth toolset was created without a sign-in app to list.")
        return {"toolset": str(keyed["_id"]), **made}

    # ----------------------------------------------------------------- records

    def _note(self, caller: str) -> tuple[str, str, str]:
        """A private knowledge base of ``caller``'s holding one note: (record id, its text, the base's id)."""
        from helper.clients.kb_client import KBClient  # noqa: PLC0415 - needs the helper dir on sys.path
        from messaging.test_e2e_record_pipeline import (  # noqa: PLC0415 - needs the backend on sys.path
            _extract_kb_id,
            _extract_record_id,
        )

        kb_client = KBClient(_AsUser(self.gateway_url, self.token(caller), self._timeout))
        kb_name = self._unique_name("kb")
        kb_id = _extract_kb_id(kb_client.create_kb(name=kb_name))
        if not kb_id:
            raise RuntimeError(f"Creating knowledge base {kb_name} returned no id.")
        self._later(f"knowledge base {kb_name}", lambda: kb_client.delete_kb(kb_id))
        text = f"{PREFIX}-note-{uuid.uuid4().hex}"
        record_id = _extract_record_id(
            kb_client.upload_file(kb_id, f"{text}.md", f"# {text}\n".encode(), mimetype="text/markdown")
        )
        if not record_id:
            raise RuntimeError(f"Uploading a note to {kb_name} returned no record id.")
        return record_id, text, kb_id

    def _build_records(self) -> dict[str, str]:
        admin_record, admin_text, _ = self._note(ADMIN)
        member_record, member_text, member_kb = self._note(CREATOR)
        return {
            "admin_record": admin_record, "admin_record_text": admin_text,
            "member_record": member_record, "member_record_text": member_text,
            "member_kb": member_kb,
        }

    def _build_signed_link(self) -> dict[str, str]:
        claims = self._admin_claims()
        record_id = self.value("admin_record")
        reply = send(
            "GET",
            f"{self.url(CONNECTOR_SERVICE)}/api/v1/{claims['orgId']}/{claims['userId']}"
            f"/knowledgebase/record/{record_id}/signedUrl",
            self.token(ADMIN), timeout=self._timeout,
        )
        if reply.status != 200:
            raise RuntimeError(f"Asking for the admin's download link returned HTTP {reply.status}.")
        token = (parse_qs(urlparse(str(json.loads(reply.text).get("signedUrl") or "")).query).get("token") or [""])[0]
        if not token:
            raise RuntimeError("The admin's download link carries no token.")
        return {"signed_token": token}

    # ---------------------------------------------------------- feature flags

    def _switch_on(self, flag: str) -> None:
        """Turn a platform feature on for this run if it is off, and put it back afterwards."""
        from helper.vector_rebuild import (  # noqa: PLC0415 - imports the backend's redis client
            read_platform_settings,
            write_platform_settings,
        )

        before = read_platform_settings(self._settings_client)
        if before.flag(flag):
            return
        write_platform_settings(self._settings_client, before.with_flag(flag, True))

        def switch_back() -> None:
            now = read_platform_settings(self._settings_client)
            write_platform_settings(self._settings_client, now.with_flag(flag, False))

        self._later(f"platform flag {flag}", switch_back)

    # ------------------------------------------------------------------ skills

    def _create_skill(self) -> str:
        name = self._unique_name("skill")
        self._json(
            CREATOR, "POST", f"{_SKILLS}/", "Creating a member's skill", ok=(201,),
            json={"name": name, "description": "Made by the conditional-admin tests.", "body": "Say hello."},
        )
        self._later(
            f"skill {name}",
            lambda: self._json(CREATOR, "DELETE", f"{_SKILLS}/{name}", "Deleting a test skill", ok=(200, 404)),
        )
        return name

    def _builtin_skills(self) -> list[Any]:
        deadline = time.monotonic() + _SKILLS_READY_TIMEOUT_SEC
        while True:
            resp = requests.get(
                f"{self.gateway_url}{_SKILLS}/", params={"source": "builtin"},
                headers={"Authorization": f"Bearer {self.token(ADMIN)}"}, timeout=self._timeout,
            )
            if resp.status_code == 200:
                return resp.json().get("skills") or []
            if resp.status_code not in (503, 504) or time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Listing built-in skills returned HTTP {resp.status_code}: {resp.text[:300]}"
                )
            time.sleep(_SKILLS_READY_POLL_SEC)

    def _build_skills(self) -> dict[str, str]:
        self._switch_on(SKILLS_FLAG)
        active = [
            str(skill.get("name")) for skill in self._builtin_skills()
            if isinstance(skill, dict) and skill.get("source") == "builtin" and skill.get("status") == "active"
        ]
        if not active:
            raise RuntimeError("The org has no active built-in skill to ask about.")
        custom = self._create_skill()
        disabled = self._create_skill()
        self._json(CREATOR, "POST", f"{_SKILLS}/{disabled}/disable", "Switching the member's own skill off", json={})
        return {"builtin_skill": sorted(active)[0], "custom_skill": custom, "disabled_skill": disabled}

    # --------------------------------------------------------------------- mcp

    def _create_mcp_server(self, caller: str, *, shared: bool, scope: str = "org") -> str:
        name = self._unique_name("mcp")
        # Registered, never contacted (the URL never resolves).
        body = self._json(
            caller, "POST", _MCP, "Registering an MCP server", ok=(201,),
            json={
                "name": name, "transport": "streamable_http", "authMode": "api_token",
                "useAdminAuth": shared, "url": MCP_SERVER_URL, "scope": scope,
            },
        )
        instance_id = str(body.get("_id") or "")
        if not instance_id:
            raise RuntimeError("Registering an MCP server returned no id.")
        # A case may have deleted it already.
        self._later(
            f"MCP server {name}",
            lambda: self._json(caller, "DELETE", f"{_MCP}/{instance_id}", "Removing a test MCP server",
                               ok=(200, 404)),
        )
        return instance_id

    def _build_mcp(self) -> dict[str, str]:
        self._switch_on(MCP_FLAG)
        return {
            "shared_mcp": self._create_mcp_server(ADMIN, shared=True),
            "own_mcp": self._create_mcp_server(ADMIN, shared=False),
            "personal_mcp": self._create_mcp_server(CREATOR, shared=False, scope="personal"),
        }

    # -------------------------------------------------------- service accounts

    def _build_service_accounts(self) -> dict[str, str]:
        slug = f"it-admin-{uuid.uuid4().hex[:8]}"
        account = self._json(
            ADMIN, "POST", _SERVICE_ACCOUNTS, "Creating a test service account",
            ok=(200, 201), json={"slug": slug, "fullName": f"{PREFIX} {slug}"},
        )
        account_id = str(account.get("id") or "")
        email = str(account.get("email") or "")
        if not account_id or not email:
            raise RuntimeError("Creating a test service account returned no id or address.")
        self._later(
            "the test service account",
            lambda: self._json(ADMIN, "DELETE", f"{_SERVICE_ACCOUNTS}/{account_id}",
                               "Deleting the test service account", ok=(200, 204, 404)),
        )
        self._await_in_the_directory(email)
        # The team every account joins on the way in. Its key is built from the
        # org id rather than looked up, which is also what the graph does
        # (`_get_or_create_all_team_and_add_user`).
        return {
            "service_account": account_id,
            "service_account_email": email,
            "all_team": f"all_{self.value('org')}",
        }

    def _await_in_the_directory(self, email: str) -> None:
        """Wait until the new account reaches the graph the directory reads.

        Creating one writes to Mongo and publishes an event the connector
        service consumes, so it is not in `GET /users/graph/list` the moment the
        request returns. The cases below ask whether a member can see it there,
        which says nothing until it is there to see.
        """
        deadline = time.monotonic() + _DIRECTORY_READY_TIMEOUT_SEC
        while True:
            listed = self._json(
                ADMIN, "GET",
                f"{_USERS}/graph/list?includeServiceAccounts=true&limit=100"
                f"&search={quote(email)}",
                "Looking for the test service account", ok=(200,),
            )
            if any(
                isinstance(row, dict) and row.get("email") == email
                for row in (listed.get("users") or [])
            ):
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"The test service account never reached the directory: {email}"
                )
            time.sleep(_DIRECTORY_READY_POLL_SEC)

    # ------------------------------------------------------------------ agents

    def _create_agent(self, caller: str, what: str) -> str:
        body = self._json(caller, "POST", f"{_AGENTS}/create", f"Creating {what}",
                          ok=(200, 201), json={"name": self._unique_name("agent")})
        agent = body.get("agent")
        agent_key = str(agent.get("_key") or "") if isinstance(agent, dict) else ""
        if not agent_key:
            raise RuntimeError(f"Creating {what} returned no _key.")
        self._later(
            what,
            lambda: self._json(caller, "DELETE", f"{_AGENTS}/{agent_key}", f"Deleting {what}",
                               ok=(200, 204, 404)),
        )
        return agent_key

    def _build_agents(self) -> dict[str, str]:
        # Its MCP listing must have an org server in it.
        self.value("shared_mcp")
        return {
            "admin_agent": self._create_agent(ADMIN, "the test agent"),
            # A member's own agent. Editing it is theirs to do, which is what
            # makes a refusal on the binding field mean the binding and not
            # the agent.
            "member_agent": self._create_agent(CREATOR, "the member's test agent"),
        }

    # ------------------------------------------------------------------- teams

    def _create_team(self, caller: str, what: str) -> str:
        body = self._json(caller, "POST", _TEAMS, f"Creating {what}", ok=(200, 201),
                          json={"name": self._unique_name("team")})
        data = body.get("data")
        team_id = str(data.get("id") or "") if isinstance(data, dict) else ""
        if not team_id:
            raise RuntimeError(f"Creating {what} returned no id.")
        # A case may have deleted it already.
        self._later(
            what,
            lambda: self._json(caller, "DELETE", f"{_TEAMS}/{team_id}", f"Deleting {what}",
                               ok=(200, 404)),
        )
        return team_id

    def _build_teams(self) -> dict[str, str]:
        # A team a member made and owns, which the other member has no role on.
        return {"creator_team": self._create_team(CREATOR, "the member's test team")}

    # ---------------------------------------------------------------- teardown

    def close(self) -> None:
        """Remove everything this run made, newest first; say what could not be removed."""
        problems: list[str] = []
        steps = [("connectors made for single cases", self.discard_fresh), *reversed(self._undo)]
        self._undo = []
        for what, undo in [*steps, ("waiting for connector deletions", self._await_deletions)]:
            logger.info("Removing: %s", what)
            try:
                undo()
            except Exception as exc:  # noqa: BLE001 - every step runs; all failures are reported together
                problems.append(f"{what}: {exc}")
        if problems:
            raise RuntimeError(
                "The conditional-admin tests left something behind:\n  " + "\n  ".join(problems)
            )


# How to make the groups an extension adds to WORLD_GROUPS (helper/suite_extensions.py).
# Each builder takes the World, as a ``_build_<group>`` method does.
for _group, _build in suite_extensions.add_items(
    {}, "BUILDERS", suite_extensions.modules("admin_route_conditional_world"),
).items():
    if hasattr(World, f"_build_{_group}"):
        raise ValueError(f"an extension adds a builder for the {_group!r} group, which World already makes")
    setattr(World, f"_build_{_group}", _build)
