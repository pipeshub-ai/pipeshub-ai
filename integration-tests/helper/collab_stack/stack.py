"""Boots and owns the whole lane: containers, fake backend, Node API, identities."""

from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import signal
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from pymongo.database import Database

from helper.collab_stack.client import Api
from helper.collab_stack.fake_backend import FakeBackend
from helper.collab_stack.flags import FeatureFlags
from helper.collab_stack.identity import Actor, Directory
from helper.collab_stack.infra import Infra, wait_until
from helper.collab_stack.node_api import DB_NAME, NodeApi
from helper.collab_stack.python_services import PythonServices, graph_backend, graph_env, real_python_enabled

logger = logging.getLogger(__name__)

# Python-owned in production (kb_apps_v1 gates it); the lane has no Python service, so the
# chat-KB-filters migration is marked done up front or it would wait minutes for that flag.
KV_PREFIX = "pipeshub:kv:"
PRESEEDED_MIGRATIONS = {"/migrations/chat_kb_filters_v1": "true"}
CHAT_MIGRATION_FLAGS = (
    "/migrations/chat_sessions_v1",
    "/migrations/acl_version_v1",
    "/migrations/chat_collaborators_v1",
)

# Collections a journey writes; emptied between tests. Users and orgs are kept.
RESETTABLE_COLLECTIONS = (
    "chatSessions",
    "chatSessionMessages",
    "chatSessionReadStates",
    "conversations",
    "agentconversations",
    "projects",
    "notifications",
    "outbox_events",
    "citation",
)
# The cache service prefixes every key (`app:` by default), so match anywhere in the key.
CACHE_KEY_PATTERNS = ("*authz:v1:*", "*teamids:*")


@dataclass(frozen=True)
class Roster:
    """The actors of the collaborative-chats scenarios (60 section 0), all in org ``acme`` unless noted."""

    admin: Actor
    owner: Actor
    write_recipient: Actor
    read_recipient: Actor
    project_viewer: Actor
    project_editor: Actor
    project_team_member: Actor
    team_writer: Actor
    team_reader: Actor
    stranger: Actor
    other_org: Actor
    disabled: Actor


class CollabStack:
    def __init__(self, node_root: Path | None = None, project: str | None = None, node_env: dict[str, str] | None = None) -> None:
        self.node_env = node_env or {}
        self.real_python = real_python_enabled()
        self.graph = graph_backend() if self.real_python else None
        self.python: PythonServices | None = None
        self.infra = Infra(project, graph=self.graph)
        self.fake = FakeBackend()
        self.run_dir = Path(tempfile.mkdtemp(prefix="pcc-e2e-run-", dir=os.environ.get("TMPDIR")))
        self.node_root = node_root
        self.node: NodeApi | None = None
        self.api: Api | None = None
        self.directory: Directory | None = None
        self.roster: Roster | None = None
        self.flags: FeatureFlags | None = None
        self._stopped = False
        self._stop_lock = threading.Lock()

    @property
    def db(self) -> Database:
        return self.infra.mongo[DB_NAME]

    def start(self) -> CollabStack:
        atexit.register(self.stop)
        endpoints = self.infra.up()
        self.fake.start()
        self.infra.wipe(DB_NAME)
        self.preseed_kv()
        node_env = {k: v.replace("{fake_url}", self.fake.url) for k, v in self.node_env.items()}
        if self.real_python:
            # Node writes these into the KV store at boot (the endpoints, the Arango and Qdrant settings); the Python services read them from there.
            self.python = PythonServices(endpoints, self.graph or "neo4j", self.run_dir, os.environ.get("TMPDIR", tempfile.gettempdir()))
            node_env.update(
                {
                    "QUERY_BACKEND": self.python.query_url,
                    "CONNECTOR_BACKEND": self.python.connectors_url,
                    **{k: v for k, v in graph_env(self.graph or "neo4j", endpoints).items() if k.startswith(("ARANGO", "QDRANT", "DATA_STORE", "VECTOR"))},
                }
            )
        self.node = NodeApi(endpoints, self.fake.url, self.run_dir, node_root=self.node_root, extra_env=node_env)
        self.node.start()
        self.api = Api(self.node.base_url)
        self.directory = Directory(self.db)
        self.roster = self._seed_roster()
        if self.python is not None:
            self.publish_node_endpoint()
            self.python.start_connectors()
            self.publish_identity_events()
            self.python.start_query()
            self.seed_ai_models()
            self.warm_up_query()
        self.flags = FeatureFlags(self)
        return self

    def publish_node_endpoint(self) -> None:
        """Python asks Node whether a session is still live (``caller_role``, main #3680) at ``endpoints.nodejs``, which
        defaults to ``localhost:3000``; Node never writes it. Point it at this run's Node."""
        assert self.node is not None
        key = f"{KV_PREFIX}/services/endpoints"
        doc = json.loads(self.infra.redis.get(key) or "{}")
        doc["nodejs"] = {**doc.get("nodejs", {}), "endpoint": self.node.base_url}
        self.infra.redis.set(key, json.dumps(doc))

    def publish_identity_events(self) -> None:
        """Put the orgs and users into the graph the way Node does in production: ``orgCreated`` and ``userAdded`` on the
        ``entity-events`` stream, consumed by the real connectors service."""
        assert self.directory is not None and self.python is not None
        wait_until(lambda: "All message consumers started successfully" in (self.python.connectors.log_tail(400) if self.python.connectors else ""), 120, interval=1, message="the connectors message consumers")
        publish_entity_events(self.infra.redis, self.directory)
        wait_until(lambda: self.graph_has_users(), 120, interval=1, message="the users to reach the graph")

    def seed_ai_models(self) -> None:
        """An LLM and an embedding model, both pointed at the fake's OpenAI-compatible endpoint, through the route the UI uses.
        Node asks the query service to health-check them, so this also proves the query service reaches the model server."""
        assert self.api is not None and self.roster is not None
        for model_type in ("llm", "embedding"):
            resp = self.api.post(
                "/api/v1/configurationManager/ai-models/providers",
                self.roster.admin,
                json_body={
                    "modelType": model_type,
                    "provider": "openAICompatible",
                    "configuration": {"endpoint": f"{self.fake.url}/v1", "apiKey": "pcc-e2e", "model": f"fake-{model_type}"},
                    "isMultimodal": False,
                    "isReasoning": False,
                    "isDefault": True,
                    "contextLength": None,
                },
            )
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"seeding the {model_type} model: {resp.status_code} {resp.text[:400]}\n{self.python.log_tail(30) if self.python else ''}")

    def warm_up_query(self) -> None:
        """One chat before the journeys. The query service's first chat does seconds of blocking setup on its event loop;
        a session check to Node (main #3680, 5 s budget) in flight at that moment times out and the chat answers 503. That
        is a cold-start property of the services, not of any journey, so the lane pays it once here."""
        assert self.api is not None and self.roster is not None
        last = None
        for attempt in range(1, 4):
            last = self.api.post("/api/v1/conversations/create", self.roster.admin, json_body={"query": "warm up", "chatMode": "quick"})
            if last.status_code == 201:
                return
            logging.getLogger(__name__).warning("warm-up chat attempt %d answered %d", attempt, last.status_code)
        raise RuntimeError(f"warm-up chat: {last.status_code if last is not None else '-'} {last.text[:300] if last is not None else ''}")

    def graph_has_users(self, actors: list[Actor] | None = None) -> bool:
        assert self.directory is not None and self.python is not None
        import requests

        for actor in actors if actors is not None else list(self.directory.actors.values()):
            if actor.disabled:
                continue
            token = Directory.service_token(actor, ("team:ids:read",))
            resp = requests.get(f"{self.python.connectors_url}/api/v1/entity/user/team-ids", headers={"Authorization": f"Bearer {token}"}, timeout=10)
            if resp.status_code != 200:
                return False
        return True

    def sync_graph_users(self, *actors: Actor) -> None:
        """Real-Python mode: bring users created after boot into the graph (a no-op on the fake lane)."""
        if self.python is None or not actors:
            return
        publish_entity_events(self.infra.redis, self.directory, only=list(actors), orgs=False)  # type: ignore[arg-type]
        wait_until(lambda: self.graph_has_users(list(actors)), 60, interval=0.5, message="new users to reach the graph")

    def preseed_kv(self) -> None:
        for key, value in PRESEEDED_MIGRATIONS.items():
            self.infra.redis.set(f"{KV_PREFIX}{key}", value)

    def _seed_roster(self) -> Roster:
        d = self.directory
        assert d is not None
        return Roster(
            admin=d.user("Admin", role="admin"),
            owner=d.user("Owner"),
            write_recipient=d.user("Writer"),
            read_recipient=d.user("Reader"),
            project_viewer=d.user("ProjectViewer"),
            project_editor=d.user("ProjectEditor"),
            project_team_member=d.user("ProjectTeamMember"),
            team_writer=d.user("TeamWriter"),
            team_reader=d.user("TeamReader"),
            stranger=d.user("Stranger"),
            other_org=d.user("Outsider", org="globex"),
            disabled=d.user("Disabled", disabled=True),
        )

    # ---- state -----------------------------------------------------------------------------

    def reset_state(self) -> None:
        """Between tests: no chats, projects or scripted replies; users, orgs and flags stay."""
        self.fake.reset()
        for name in RESETTABLE_COLLECTIONS:
            self.db[name].delete_many({})
        self.flush_caches()

    def flush_caches(self) -> None:
        for pattern in CACHE_KEY_PATTERNS:
            for key in self.infra.redis.scan_iter(match=pattern, count=500):
                self.infra.redis.delete(key)

    def kv_get(self, key: str) -> str | None:
        return self.infra.redis.get(f"{KV_PREFIX}{key}")

    def kv_delete(self, *keys: str) -> None:
        for key in keys:
            self.infra.redis.delete(f"{KV_PREFIX}{key}")

    def wait_for_chat_migrations(self, timeout: float = 120) -> None:
        try:
            wait_until(
                lambda: all(self.kv_get(k) for k in CHAT_MIGRATION_FLAGS),
                timeout,
                message="chat migrations to write their completion flags",
            )
        except TimeoutError as exc:
            missing = [k for k in CHAT_MIGRATION_FLAGS if not self.kv_get(k)]
            raise TimeoutError(f"{exc}; missing {missing}\n--- node log tail ---\n{self.node.log_tail(40) if self.node else ''}") from exc

    def forget_chat_migrations(self) -> None:
        """Delete the completion flags so the next boot runs the chat migrations again."""
        self.kv_delete(*CHAT_MIGRATION_FLAGS)

    def clear_chat_data(self) -> None:
        for name in RESETTABLE_COLLECTIONS:
            self.db[name].delete_many({})

    # ---- teardown --------------------------------------------------------------------------

    def stop(self) -> None:
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
        for step in (
            lambda: self.python and self.python.stop(),
            lambda: self.node and self.node.stop(),
            self.fake.stop,
            self.infra.down,
        ):
            try:
                step()
            except Exception:  # noqa: BLE001 - keep tearing down
                logger.exception("teardown step failed")
        leftovers = self.infra.leftovers()
        if leftovers:
            logger.error("containers left behind: %s", leftovers)
        if os.environ.get("PCC_E2E_KEEP_RUN_DIR") != "1" and not (self.node and self.node.proc):
            shutil.rmtree(self.run_dir, ignore_errors=True)


def publish_entity_events(redis_client, directory: Directory, only: list[Actor] | None = None, orgs: bool = True) -> None:  # noqa: ANN001
    """``orgCreated`` for each org (first user as creator), then ``userAdded`` for each user, on the ``entity-events`` stream."""
    import json
    import time

    def send(event_type: str, payload: dict) -> None:
        message = {"eventType": event_type, "payload": payload, "timestamp": int(time.time() * 1000)}
        redis_client.xadd("entity-events", {"value": json.dumps(message)})

    by_org: dict[str, list[Actor]] = {}
    for actor in only if only is not None else directory.actors.values():
        by_org.setdefault(actor.org_id, []).append(actor)
    for org_id, actors in by_org.items() if orgs else []:
        creator = next((a for a in actors if a.role == "admin"), actors[0])
        send("orgCreated", {"orgId": org_id, "accountType": "business", "registeredName": f"Org {org_id[-6:]}", "userId": creator.user_id})
    for actors in by_org.values():
        for actor in actors:
            if actor.disabled:
                continue
            send("userAdded", {"orgId": actor.org_id, "userId": actor.user_id, "email": actor.email, "fullName": f"User {actor.name}", "firstName": actor.name, "lastName": "", "designation": ""})


def install_signal_cleanup(stack: CollabStack) -> None:
    """Make Ctrl-C and SIGTERM run the same teardown as a normal exit."""

    def handler(signum, _frame):  # noqa: ANN001
        stack.stop()
        raise SystemExit(128 + signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handler)
        except ValueError:  # not the main thread
            pass
