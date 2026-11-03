"""Seed and remove the ServiceNow objects one test run uses.

Everything is created through the Table and Attachment APIs as the admin account, and named
``PHIT-<run>-...`` (users ``phit-<run>-...``, roles ``phit_<run>_...``), so a run only ever touches
its own objects. What REST cannot do on a versioned instance goes through a background script run
in an admin UI session: publishing (writing ``workflow_state`` directly is reverted to draft),
checkout of a new version, revert, retire, and bulk deletes. A published row is read-only, even to
admin, so every edit of a published article is a checkout, a change on the draft, and a publish.

Knowledge bases cannot be deleted through REST: teardown removes their articles, attachments,
feedback, categories and criteria, then deactivates them. A run that died before teardown is
swept by the next run once its objects are ``STALE_HOURS`` old.

Run on its own: ``python connectors/servicenow/servicenow_seed.py smoke`` seeds a small set,
checks each primitive against the instance, and removes it; ``... sweep`` removes stale objects.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import secrets
import struct
import sys
import time
import uuid
import zlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import requests

if __package__ in (None, ""):
    # Run as a script: import the suite's constants the way pytest does.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from connectors.servicenow.constants import (
    CRITERIA_SETTLE_SEC,
    ENV_CLIENT_ID,
    ENV_CLIENT_SECRET,
    ENV_INSTANCE_URL,
    ENV_PASSWORD,
    ENV_USERNAME,
    PREFIX,
    STALE_HOURS,
)

logger = logging.getLogger("servicenow-seed")

_SYS_ID = re.compile(r"[0-9a-f]{32}")
_MTOM = {
    "read": "kb_uc_can_read_mtom",
    "cannot": "kb_uc_cannot_read_mtom",
    "contribute": "kb_uc_can_contribute_mtom",
    "cannot_contribute": "kb_uc_cannot_contribute_mtom",  # the platform puts Guest User here too
}
PUBLISH_TIMEOUT = 120


class ServiceNowError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"ServiceNow answered HTTP {status}: {message}")
        self.status = status


def _ids(values: Iterable[str]) -> list[str]:
    out = [str(v) for v in values]
    for v in out:
        if not _SYS_ID.fullmatch(v):
            raise ValueError(f"not a sys_id: {v!r}")
    return out


class ServiceNowRest:
    """Table and Attachment API calls as one user, with basic auth. Never logs credentials or bodies."""

    def __init__(self, base_url: str, username: str, password: str, *, timeout: int = 60) -> None:
        if not base_url.startswith("https://"):
            raise ValueError(f"{ENV_INSTANCE_URL} must be an https URL")
        self.base_url = base_url.rstrip("/")
        self.username = username
        self._password = password
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.headers.update({"Accept": "application/json"})
        self._ui: requests.Session | None = None

    def as_user(self, username: str, password: str) -> ServiceNowRest:
        return ServiceNowRest(self.base_url, username, password, timeout=self.timeout)

    def _call(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        for attempt in range(4):
            resp = self.session.request(method, self.base_url + path, timeout=self.timeout, **kwargs)
            if resp.status_code in (429, 502, 503) and attempt < 3:
                retry_after = resp.headers.get("Retry-After", "")
                # An HTTP-date (or anything else) falls back to 5 s.
                time.sleep(min(int(retry_after) if retry_after.isdigit() else 5, 30))
                continue
            return resp
        return resp

    @staticmethod
    def _error(resp: requests.Response) -> ServiceNowError:
        try:
            err = resp.json().get("error") or {}
            message = f"{err.get('message', '')}: {err.get('detail', '')}".strip(": ")
        except ValueError:
            message = resp.text[:200]
        return ServiceNowError(resp.status_code, message or resp.reason)

    def get(self, table: str, query: str = "", fields: str = "sys_id", *, page: int = 500) -> list[dict[str, Any]]:
        """Every row matching ``query``, paged by ``X-Total-Count`` (a short page does not mean the end)."""
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            resp = self._call("GET", f"/api/now/table/{table}", params={
                "sysparm_query": query, "sysparm_fields": fields, "sysparm_limit": page,
                "sysparm_offset": offset, "sysparm_exclude_reference_link": "true",
            })
            if resp.status_code != 200:
                raise self._error(resp)
            rows.extend(resp.json().get("result") or [])
            total = int(resp.headers.get("X-Total-Count", "0") or 0)
            offset += page
            if offset >= total:
                return rows

    def one(self, table: str, query: str, fields: str = "sys_id") -> dict[str, Any] | None:
        rows = self.get(table, query, fields, page=2)
        return rows[0] if rows else None

    def row(self, table: str, sys_id: str, fields: str = "sys_id") -> dict[str, Any] | None:
        resp = self._call("GET", f"/api/now/table/{table}/{sys_id}", params={
            "sysparm_fields": fields, "sysparm_exclude_reference_link": "true"})
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise self._error(resp)
        return resp.json()["result"]

    def create(self, table: str, body: dict[str, Any], fields: str = "sys_id") -> dict[str, Any]:
        resp = self._call("POST", f"/api/now/table/{table}", json=body, params={
            "sysparm_fields": fields, "sysparm_input_display_value": "false",
            "sysparm_exclude_reference_link": "true"})
        if resp.status_code not in (200, 201):
            raise self._error(resp)
        return resp.json()["result"]

    def patch(self, table: str, sys_id: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = self._call("PATCH", f"/api/now/table/{table}/{_ids([sys_id])[0]}", json=body,
                          params={"sysparm_fields": "sys_id", "sysparm_exclude_reference_link": "true"})
        if resp.status_code != 200:
            raise self._error(resp)
        return resp.json()["result"]

    def delete(self, table: str, sys_id: str) -> None:
        resp = self._call("DELETE", f"/api/now/table/{table}/{_ids([sys_id])[0]}")
        if resp.status_code not in (200, 204, 404):
            raise self._error(resp)

    def upload(self, table: str, sys_id: str, name: str, data: bytes, content_type: str) -> str:
        resp = self._call("POST", "/api/now/attachment/file", data=data,
                          params={"table_name": table, "table_sys_id": _ids([sys_id])[0], "file_name": name},
                          headers={"Content-Type": content_type})
        if resp.status_code not in (200, 201):
            raise self._error(resp)
        return resp.json()["result"]["sys_id"]

    def delete_attachment(self, sys_id: str) -> None:
        resp = self._call("DELETE", f"/api/now/attachment/{_ids([sys_id])[0]}")
        if resp.status_code not in (200, 204, 404):
            raise self._error(resp)

    def count(self, table: str, query: str) -> int:
        resp = self._call("GET", f"/api/now/stats/{table}", params={"sysparm_query": query, "sysparm_count": "true"})
        if resp.status_code != 200:
            raise self._error(resp)
        return int(resp.json()["result"]["stats"]["count"])

    # -- background scripts (admin UI session) ------------------------------------------------

    def _ui_session(self) -> requests.Session:
        if self._ui is not None:
            return self._ui
        ui = requests.Session()
        ui.get(self.base_url + "/login.do", timeout=self.timeout)
        ui.post(self.base_url + "/login.do", timeout=self.timeout, data={
            "user_name": self.username, "user_password": self._password,
            "sys_action": "sysverb_login", "sysparm_login_url": "welcome.do",
        })
        self._ui = ui
        return ui

    def run_script(self, script: str) -> list[dict[str, Any]]:
        """Run a server-side script as this (admin) user; return every ``PHIT>{json}`` line it printed."""
        for attempt in range(2):
            ui = self._ui_session()
            page = ui.get(self.base_url + "/sys.scripts.do", timeout=self.timeout).text
            match = re.search(r'name="sysparm_ck"[^>]*value="([^"]+)"', page)
            if not match:
                self._ui = None  # the session expired or the login was refused
                if attempt:
                    raise ServiceNowError(401, "could not open an admin UI session to run a background script")
                continue
            resp = ui.post(self.base_url + "/sys.scripts.do", timeout=600, data={
                "script": script, "sysparm_ck": match.group(1), "runscript": "Run script",
                "quota_managed_transaction": "on",
            })
            text = html.unescape(re.sub(r"<[^>]+>", "\n", re.sub(r"<br\s*/?>", "\n", resp.text, flags=re.IGNORECASE)))
            if resp.status_code != 200:
                raise ServiceNowError(resp.status_code, "background script refused")
            out = [json.loads(m) for m in re.findall(r"PHIT>(\{.*\})", text)]
            if not out:
                errors = [line for line in text.splitlines() if "Evaluator" in line or "error" in line.lower()]
                raise ServiceNowError(500, "background script printed nothing: " + " | ".join(errors[:3])[:300])
            return out
        raise AssertionError("unreachable")


def _js(value: Any) -> str:
    return json.dumps(value)


def _script(template: str, value: Any) -> str:
    """A background script with ``value`` (JSON) in place of ``@P@``; JS braces stay as they are."""
    return template.replace("@P@", _js(value))


def tiny_png() -> bytes:
    """A 1x1 PNG, for inline images and image attachments."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


def mint_token(base_url: str, client_id: str, client_secret: str, username: str, password: str) -> tuple[str, str]:
    """An access and refresh token for the admin account (password grant on ``/oauth_token.do``)."""
    resp = requests.post(f"{base_url.rstrip('/')}/oauth_token.do", timeout=30, data={
        "grant_type": "password", "client_id": client_id, "client_secret": client_secret,
        "username": username, "password": password,
    })
    if resp.status_code != 200:
        try:
            reason = resp.json().get("error_description") or resp.json().get("error") or ""
        except ValueError:
            reason = ""
        raise ServiceNowError(resp.status_code, f"token request refused {reason}".strip())
    body = resp.json()
    if not body.get("access_token") or not body.get("refresh_token"):
        raise ServiceNowError(200, "token response has no access or refresh token")
    return body["access_token"], body["refresh_token"]


def rest_from_env() -> ServiceNowRest:
    return ServiceNowRest(os.environ[ENV_INSTANCE_URL].strip(), os.environ[ENV_USERNAME], os.environ[ENV_PASSWORD])


def token_from_env() -> tuple[str, str]:
    return mint_token(os.environ[ENV_INSTANCE_URL].strip(), os.environ[ENV_CLIENT_ID], os.environ[ENV_CLIENT_SECRET],
                      os.environ[ENV_USERNAME], os.environ[ENV_PASSWORD])


@dataclass
class Article:
    key: str
    sys_id: str
    number: str
    title: str
    kb: str
    category: str | None = None
    token: str = ""
    attachments: dict[str, str] = field(default_factory=dict)  # file name -> sys_id


@dataclass
class SeedUser:
    key: str
    sys_id: str
    user_name: str
    email: str
    password: str = field(repr=False)  # never in a failure report


class ServiceNowSeed:
    """The objects of one run. Each method creates one object and remembers it by its key."""

    def __init__(self, rest: ServiceNowRest, run: str | None = None) -> None:
        self.rest = rest
        self.run = run or uuid.uuid4().hex[:8]
        self.kbs: dict[str, str] = {}
        self.categories: dict[str, str] = {}
        self.criteria: dict[str, str] = {}
        self.groups: dict[str, str] = {}
        self.roles: dict[str, str] = {}
        self.departments: dict[str, str] = {}
        self.users: dict[str, SeedUser] = {}
        self.articles: dict[str, Article] = {}
        self.unpublished: list[str] = []

    # names ------------------------------------------------------------------------------------
    @property
    def prefix(self) -> str:
        return f"{PREFIX}-{self.run}-"

    def name(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def token(self, key: str) -> str:
        """A word that is in this one article's body and nowhere else."""
        return f"phit{self.run}{re.sub(r'[^a-z0-9]', '', key.lower())}"

    # identity ---------------------------------------------------------------------------------
    def persona(self, user_name: str, fields: str = "sys_id,email,department,location,company,active") -> dict[str, Any]:
        row = self.rest.one("sys_user", f"user_name={user_name}", fields)
        if row is None:
            raise LookupError(f"the instance has no user {user_name!r} (see the suite README, 'Instance fixture')")
        return row

    def department(self, key: str) -> str:
        self.departments[key] = self.rest.create("cmn_department", {"name": self.name(key)})["sys_id"]
        return self.departments[key]

    def group(self, key: str, members: Iterable[str] = (), parent: str | None = None) -> str:
        body = {"name": self.name(key), "active": "true", **({"parent": parent} if parent else {})}
        gid = self.rest.create("sys_user_group", body)["sys_id"]
        self.groups[key] = gid
        for user in members:
            self.add_member(gid, user)
        return gid

    def add_member(self, group: str, user: str) -> str:
        return self.rest.create("sys_user_grmember", {"group": group, "user": user})["sys_id"]

    def remove_member(self, group: str, user: str) -> None:
        for row in self.rest.get("sys_user_grmember", f"group={group}^user={user}"):
            self.rest.delete("sys_user_grmember", row["sys_id"])

    def role(self, key: str, contains: Iterable[str] = ()) -> str:
        rid = self.rest.create("sys_user_role", {"name": f"{PREFIX.lower()}_{self.run}_{key}"})["sys_id"]
        self.roles[key] = rid
        for inner in contains:
            self.rest.create("sys_user_role_contains", {"role": rid, "contains": inner})
        return rid

    def grant_role(self, user: str, role: str) -> None:
        self.rest.create("sys_user_has_role", {"user": user, "role": role})

    def revoke_role(self, user: str, role: str) -> None:
        for row in self.rest.get("sys_user_has_role", f"user={user}^role={role}^inherited=false"):
            self.rest.delete("sys_user_has_role", row["sys_id"])

    def user(self, key: str, **fields: Any) -> SeedUser:
        """A throw-away user with an email and a password, so the suite can read the instance as it."""
        user_name = f"{PREFIX.lower()}-{self.run}-{key}"
        email = f"{user_name}@example.com"
        password = f"Ph1t!{secrets.token_hex(12)}Zq"
        row = self.rest.create("sys_user", {
            "user_name": user_name, "first_name": "PHIT", "last_name": f"{self.run} {key}", "email": email,
            "active": "true", "password_needs_reset": "false", **fields,
        })
        # The password only saves as a display value.
        resp = self.rest._call("PATCH", f"/api/now/table/sys_user/{row['sys_id']}",
                               params={"sysparm_input_display_value": "true", "sysparm_fields": "sys_id"},
                               json={"user_password": password})
        if resp.status_code != 200:
            raise self.rest._error(resp)
        # A new password can be refused for a few seconds; wait until the user can sign in.
        as_user = self.rest.as_user(user_name, password)
        deadline = time.monotonic() + 30
        while as_user._call("GET", "/api/now/table/sys_user", params={"sysparm_limit": "1"}).status_code == 401:
            if time.monotonic() > deadline:
                raise ServiceNowError(401, f"{user_name} cannot sign in 30 s after its password was set")
            time.sleep(2)
        self.users[key] = SeedUser(key, row["sys_id"], user_name, email, password)
        return self.users[key]

    # knowledge --------------------------------------------------------------------------------
    def criterion(self, key: str, **fields: Any) -> str:
        body = {"name": self.name(key), "active": "true",
                **{k: (",".join(v) if isinstance(v, (list, tuple)) else v) for k, v in fields.items()}}
        self.criteria[key] = self.rest.create("user_criteria", body)["sys_id"]
        return self.criteria[key]

    def kb(self, key: str, *, read: Iterable[str] = (), cannot: Iterable[str] = (), contribute: Iterable[str] = (),
           keep_defaults: bool = False, owner: str | None = None, managers: Iterable[str] = ()) -> str:
        """A knowledge base. Inserting one adds the platform's default criteria ("Any User" can read,
        "Has atleast one role" can contribute, "Guest User" cannot read or contribute); they are removed unless asked."""
        body: dict[str, Any] = {"title": self.name(key), "description": "PipesHub integration test", "active": "true"}
        if owner:
            body["owner"] = owner
        if managers:
            body["kb_managers"] = ",".join(managers)
        kid = self.rest.create("kb_knowledge_base", body)["sys_id"]
        self.kbs[key] = kid
        if not keep_defaults:
            for table in _MTOM.values():
                for row in self.rest.get(table, f"kb_knowledge_base={kid}"):
                    self.rest.delete(table, row["sys_id"])
        for kind, crit in (("read", read), ("cannot", cannot), ("contribute", contribute)):
            for c in crit:
                self.link(kid, kind, c)
        return kid

    def link(self, kb: str, kind: str, criterion: str) -> str:
        return self.rest.create(_MTOM[kind], {"kb_knowledge_base": kb, "user_criteria": criterion})["sys_id"]

    def unlink(self, kb: str, kind: str, criterion: str) -> None:
        for row in self.rest.get(_MTOM[kind], f"kb_knowledge_base={kb}^user_criteria={criterion}"):
            self.rest.delete(_MTOM[kind], row["sys_id"])

    def category(self, key: str, parent: str, *, under_category: bool = False) -> str:
        label = self.name(key)
        cid = self.rest.create("kb_category", {
            "label": label, "value": re.sub(r"[^a-z0-9]+", "_", label.lower()), "active": "true",
            "parent_table": "kb_category" if under_category else "kb_knowledge_base", "parent_id": parent,
        })["sys_id"]
        self.categories[key] = cid
        return cid

    def article(self, key: str, kb: str, *, category: str | None = None, text: str | None = None,
                title: str | None = None, publish: bool = True, **fields: Any) -> Article:
        """A draft article; ``publish`` queues it for ``publish_pending``."""
        token = self.token(key)
        title = title or self.name(key)
        body = {
            "short_description": title, "kb_knowledge_base": kb, "workflow_state": "draft",
            "text": f"<p>{title}. The {token} note covers the quarterly budget and the launch plan.</p>"
            if text is None else text, **fields,
        }
        if category:
            body["kb_category"] = category
        row = self.rest.create("kb_knowledge", body, "sys_id,number")
        art = Article(key, row["sys_id"], row["number"], title, kb, category, token)
        self.articles[key] = art
        if publish:
            self.unpublished.append(row["sys_id"])
        return art

    def attach(self, art: Article, name: str, data: bytes, content_type: str, *, inline: bool = False) -> str:
        """Attach a file to a draft (a published row refuses it). ``inline`` stores it the way the
        editor stores a pasted image (``ZZ_YYkb_knowledge``), which is not an article attachment."""
        sid = self.rest.upload("ZZ_YYkb_knowledge" if inline else "kb_knowledge", art.sys_id, name, data, content_type)
        if not inline:
            art.attachments[name] = sid
        return sid

    def feedback(self, art: Article, user: str, comments: str, rating: int = 4) -> str:
        return self.rest.create("kb_feedback", {
            "article": art.sys_id, "user": user, "comments": comments, "rating": str(rating), "useful": "yes",
        })["sys_id"]

    # platform actions (background scripts) -----------------------------------------------------
    def publish_pending(self) -> None:
        ids, self.unpublished = self.unpublished, []
        self.publish(ids)

    def publish(self, sys_ids: Iterable[str]) -> None:
        ids = _ids(sys_ids)
        if not ids:
            return
        self.rest.run_script(_script(
            "(function(){var ids=@P@,ui=new global.KnowledgeUIAction(),out={};"
            "ids.forEach(function(id){var g=new GlideRecord('kb_knowledge');if(!g.get(id)){out[id]='missing';return;}"
            "if(g.workflow_state!='published')ui.publish(g);out[id]=String(g.workflow_state);});"
            "gs.print('PHIT>'+JSON.stringify(out));})();", ids))
        self.wait_state(ids, "published")

    def wait_state(self, sys_ids: Iterable[str], state: str, timeout: float = PUBLISH_TIMEOUT) -> None:
        """Publishing runs a flow, so the state can change a few seconds after the script returns."""
        ids = _ids(sys_ids)
        deadline = time.monotonic() + timeout
        while True:
            rows = []
            for i in range(0, len(ids), 50):
                rows += self.rest.get("kb_knowledge", "sys_idIN" + ",".join(ids[i:i + 50]), "sys_id,workflow_state")
            pending = [r["sys_id"] for r in rows if r["workflow_state"] != state]
            missing = set(ids) - {r["sys_id"] for r in rows}
            if not pending and not missing:
                return
            if time.monotonic() > deadline:
                raise AssertionError(f"{len(pending) + len(missing)} article(s) did not reach {state!r} "
                                     f"in {timeout:.0f}s: {sorted(pending + list(missing))[:5]}")
            time.sleep(3)

    def checkout(self, art: Article, *, publish: bool = True, **changes: Any) -> str:
        """Check out a new version of a published article, apply ``changes`` to it, and publish it
        (or leave it a draft). Returns the new version's sys_id; ``art`` follows it when published."""
        out = self.rest.run_script(_script(
            "(function(){var s=@P@,g=new GlideRecord('kb_knowledge');if(!g.get(s.id)){gs.print('PHIT>{}');return;}"
            "var nv=new KBVersioning().checkout(g);for(var f in s.set)nv.setValue(f,s.set[f]);nv.update();"
            "gs.print('PHIT>'+JSON.stringify({id:String(nv.sys_id)}));})();", {"id": _ids([art.sys_id])[0], "set": changes}))
        new_id = out[0].get("id")
        if not new_id:
            raise AssertionError(f"checkout of {art.number} returned no new version")
        if publish:
            # Publishing in the checkout's own script leaves the draft a draft.
            self.publish([new_id])
            art.sys_id = new_id
            art.title = changes.get("short_description", art.title)
            art.kb = changes.get("kb_knowledge_base", art.kb)
            art.category = changes.get("kb_category", art.category)
        return new_id

    def revert(self, art: Article, version_sys_id: str) -> str:
        """Make an older version of ``art`` current again (a new draft with its content), publish it,
        and point ``art`` at it."""
        out = self.rest.run_script(_script(
            "(function(){var g=new GlideRecord('kb_knowledge');g.get(@P@);var nv=new KBVersioning().makeThisCurrent(g);"
            "gs.print('PHIT>'+JSON.stringify({id:String(nv.sys_id)}));})();", _ids([version_sys_id])[0]))
        self.publish([out[0]["id"]])
        art.sys_id = out[0]["id"]
        return art.sys_id

    def retire(self, art: Article) -> None:
        self.rest.run_script(_script(
            "(function(){var g=new GlideRecord('kb_knowledge');g.get(@P@);new global.KnowledgeUIAction()"
            ".retireKnowledgeRecord(g);gs.print('PHIT>{}');})();", _ids([art.sys_id])[0]))
        self.wait_state([art.sys_id], "retired")

    def force(self, sys_id: str, **fields: Any) -> None:
        """Write fields of a published row with business rules off: the only way to move an article
        straight back to draft or outdated, as an import or a script would."""
        self.rest.run_script(_script(
            "(function(){var s=@P@,g=new GlideRecord('kb_knowledge');g.get(s.id);g.setWorkflow(false);"
            "for(var f in s.set)g.setValue(f,s.set[f]);g.update();gs.print('PHIT>{}');})();", {"id": _ids([sys_id])[0], "set": fields}))

    def delete_row(self, table: str, sys_id: str) -> None:
        """Delete a row REST refuses to delete (a category that still has articles)."""
        self.rest.run_script(_script(
            "(function(){var s=@P@,g=new GlideRecord(s.t);gs.print('PHIT>'+JSON.stringify({gone:g.get(s.id)&&g.deleteRecord()}));})();",
            {"t": table, "id": _ids([sys_id])[0]}))

    def bulk_articles(self, kb: str, count: int, key: str = "page") -> list[str]:
        """``count`` published articles in one server-side script (REST would take a call each)."""
        out = self.rest.run_script(_script(
            "(function(){var p=@P@,ui=new global.KnowledgeUIAction(),ids=[];for(var i=0;i<p.n;i++){"
            "var g=new GlideRecord('kb_knowledge');g.initialize();g.kb_knowledge_base=p.kb;"
            "g.short_description=p.name+i;g.text='<p>'+p.name+i+' paging article.</p>';g.workflow_state='draft';"
            "var id=g.insert();var r=new GlideRecord('kb_knowledge');r.get(id);ui.publish(r);ids.push(String(id));}"
            "gs.print('PHIT>'+JSON.stringify({ids:ids}));})();", {"kb": _ids([kb])[0], "n": int(count), "name": self.name(f"{key}-")}))
        ids = out[0]["ids"]
        self.wait_state(ids, "published", timeout=PUBLISH_TIMEOUT * 3)
        return ids

    # teardown ---------------------------------------------------------------------------------
    def teardown(self) -> dict[str, Any]:
        return remove_objects(self.rest, self.run)


def remove_objects(rest: ServiceNowRest, run: str = "", *, stale_hours: float | None = None) -> dict[str, Any]:
    """Delete what run ``run`` made (every run, with ``stale_hours``, but only objects that old).

    Articles and everything under them are matched through their knowledge base, so an article a
    test moved or retired is found too. Knowledge bases are deactivated, not deleted.
    """
    if stale_hours is None and not run:
        raise ValueError("refusing to remove every run's objects without an age limit")
    params = {
        "up": f"{PREFIX}-{run}-" if run else f"{PREFIX}-",
        "low": f"{PREFIX.lower()}-{run}-" if run else f"{PREFIX.lower()}-",
        "role": f"{PREFIX.lower()}_{run}_" if run else f"{PREFIX.lower()}_",
        "stale": stale_hours is not None,
        "hours": stale_hours or 0,
    }
    script = _script("""(function(){
var P=@P@,out={},cut=new GlideDateTime();cut.addSeconds(-3600*P.hours);
function old(g,f){if(P.stale)g.addQuery(f,'<',cut);}
function drop(t,build){var g=new GlideRecord(t);build(g);g.query();var n=0;while(g.next()){if(g.deleteRecord())n++;}out[t]=(out[t]||0)+n;}
function inKb(g){g.addQuery('kb_knowledge_base.title','STARTSWITH',P.up);old(g,'kb_knowledge_base.sys_created_on');}
var arts=[],a=new GlideRecord('kb_knowledge');inKb(a);a.query();while(a.next())arts.push(a.getUniqueValue());
for(var i=0;i<arts.length;i+=100){var c=arts.slice(i,i+100).join(',');
 drop('kb_feedback',function(g){g.addQuery('article','IN',c);});
 drop('sys_attachment',function(g){g.addQuery('table_sys_id','IN',c);});}
drop('kb_knowledge',inKb);
['kb_uc_can_read_mtom','kb_uc_cannot_read_mtom','kb_uc_can_contribute_mtom','kb_uc_cannot_contribute_mtom'].forEach(function(t){
 drop(t,inKb);drop(t,function(g){g.addQuery('user_criteria.name','STARTSWITH',P.up);old(g,'user_criteria.sys_created_on');});});
drop('kb_category',function(g){g.addQuery('label','STARTSWITH',P.up);old(g,'sys_created_on');});
drop('user_criteria',function(g){g.addQuery('name','STARTSWITH',P.up);old(g,'sys_created_on');});
drop('sys_user_grmember',function(g){g.addQuery('group.name','STARTSWITH',P.up);old(g,'group.sys_created_on');});
drop('sys_user_grmember',function(g){g.addQuery('user.user_name','STARTSWITH',P.low);old(g,'user.sys_created_on');});
drop('sys_user_has_role',function(g){g.addQuery('role.name','STARTSWITH',P.role);g.addQuery('inherited',false);old(g,'role.sys_created_on');});
drop('sys_user_has_role',function(g){g.addQuery('user.user_name','STARTSWITH',P.low);g.addQuery('inherited',false);old(g,'user.sys_created_on');});
drop('sys_user_role_contains',function(g){g.addQuery('role.name','STARTSWITH',P.role);old(g,'role.sys_created_on');});
drop('sys_user_group',function(g){g.addQuery('name','STARTSWITH',P.up);old(g,'sys_created_on');});
drop('sys_user_role',function(g){g.addQuery('name','STARTSWITH',P.role);old(g,'sys_created_on');});
drop('sys_user',function(g){g.addQuery('user_name','STARTSWITH',P.low);old(g,'sys_created_on');});
drop('cmn_department',function(g){g.addQuery('name','STARTSWITH',P.up);old(g,'sys_created_on');});
var k=new GlideRecord('kb_knowledge_base'),n=0;k.addQuery('title','STARTSWITH',P.up);k.addActiveQuery();old(k,'sys_created_on');
k.query();while(k.next()){k.active=false;k.update();n++;}out.kb_deactivated=n;
gs.print('PHIT>'+JSON.stringify(out));})();""", params)
    out = rest.run_script(script)[0]
    logger.info("Removed ServiceNow test objects (%s): %s", run or f"older than {stale_hours} h", out)
    return out


def sweep_stale(rest: ServiceNowRest) -> dict[str, Any]:
    return remove_objects(rest, stale_hours=STALE_HOURS)


def readable_numbers(rest: ServiceNowRest, kb_ids: Iterable[str]) -> set[str]:
    """Article numbers this user may read in these knowledge bases, by ServiceNow's own read rule.

    The Table API applies the same ``KBKnowledge.canRead`` ACL as the portal, so a call as the user
    is the oracle. Scoped to what is indexed: published, active, in an active KB, not expired. A user
    who cannot log in (inactive) reads nothing.
    """
    ids = _ids(kb_ids)
    query = (f"kb_knowledge_baseIN{','.join(ids)}^workflow_state=published^active=true^kb_knowledge_base.active=true"
             "^valid_toISEMPTY^ORvalid_to>=javascript:gs.beginningOfToday()")
    def read() -> set[str]:
        try:
            return {row["number"] for row in rest.get("kb_knowledge", query, "number")}
        except ServiceNowError as e:
            if e.status == 401:
                return set()
            raise

    # ServiceNow caches user criteria: a change can read back stale for a few seconds, so the
    # answer counts once two reads that far apart agree.
    last = read()
    for _ in range(5):
        time.sleep(CRITERIA_SETTLE_SEC)
        now = read()
        if now == last:
            return now
        last = now
    return last


# -- standalone ---------------------------------------------------------------------------------

def _smoke(rest: ServiceNowRest) -> None:
    """Seed one of each object, check each primitive on the instance, and remove it all."""
    seed = ServiceNowSeed(rest)
    print(f"run {seed.run}")
    try:
        reader = seed.user("reader")
        group = seed.group("readers", [reader.sys_id])
        crit = seed.criterion("uc-readers", group=group)
        kb = seed.kb("kb", read=[crit])
        defaults = {t: rest.count(t, f"kb_knowledge_base={kb}") for t in _MTOM.values()}
        assert defaults == {"kb_uc_can_read_mtom": 1, "kb_uc_cannot_read_mtom": 0, "kb_uc_can_contribute_mtom": 0,
                            "kb_uc_cannot_contribute_mtom": 0}, defaults
        cat = seed.category("cat", kb)
        art = seed.article("art", kb, category=cat)
        seed.attach(art, "note.txt", b"phit smoke attachment", "text/plain")
        seed.attach(art, "pixel.png", tiny_png(), "image/png", inline=True)
        seed.publish_pending()
        print("published", art.number)
        reader_rest = rest.as_user(reader.user_name, reader.password)
        time.sleep(10)  # past the criteria cache, which a new criterion can take ~5 s to leave
        assert readable_numbers(reader_rest, [kb]) == {art.number}, "reader cannot read the published article"
        old = art.sys_id
        seed.checkout(art, text="<p>edited body</p>")
        assert art.sys_id != old
        states = {r["sys_id"]: r["workflow_state"] for r in rest.get("kb_knowledge", f"number={art.number}", "sys_id,workflow_state")}
        print("versions", states)
        assert states[art.sys_id] == "published" and states[old] == "outdated", states
        assert readable_numbers(reader_rest, [kb]) == {art.number}
        seed.revert(art, old)
        print("reverted", rest.get("kb_knowledge", f"number={art.number}^workflow_state=published", "text")[0]["text"][:40])
        bulk = seed.bulk_articles(kb, 3)
        retired, drafted = seed.articles["art"], seed.article("draft-me", kb)
        seed.publish_pending()
        seed.retire(retired)
        seed.force(drafted.sys_id, workflow_state="draft")
        states = {r["number"]: r["workflow_state"] for r in rest.get(
            "kb_knowledge", f"sys_idIN{','.join(bulk + [drafted.sys_id])}", "number,workflow_state")}
        print("bulk + forced", states)
        assert list(states.values()).count("published") == 3 and "draft" in states.values(), states
        token, refresh = token_from_env()
        assert token and refresh
        print("token minted")
    finally:
        print("teardown", seed.teardown())
    assert rest.count("kb_knowledge", f"kb_knowledge_base.titleSTARTSWITH{seed.prefix}") == 0
    assert rest.count("sys_user", f"user_nameSTARTSWITHphit-{seed.run}-") == 0
    assert rest.count("kb_knowledge_base", f"titleSTARTSWITH{seed.prefix}^active=true") == 0
    print("smoke ok: nothing of the run is left but its deactivated knowledge base")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    command = sys.argv[1] if len(sys.argv) > 1 else "smoke"
    client = rest_from_env()
    if command == "sweep":
        print(sweep_stale(client))
    else:
        _smoke(client)
