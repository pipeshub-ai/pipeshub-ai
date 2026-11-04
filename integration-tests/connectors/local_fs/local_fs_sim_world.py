# pyright: ignore-file

"""Local FS against a headless desktop: a real folder the test owns, a desktop that watches it, and the stack.

Local FS has no remote API to simulate: its source is a folder on the user's computer, read by the PipesHub
desktop app, which the connector pulls from through Node's socket relay. These tests run that desktop
headless: the desktop agent of connectors-simulator (``harness/local_fs/node/desktop_agent.ts``) runs this
checkout's own ``frontend/electron/local-sync`` code (watcher, journal, pull responder, content streamer,
socket client) on a folder under ``integration-tests/local_fs_root``, connected to the stack under test as a
signed-in user. Its control API (``/ctl/...`` on a local port) starts and stops watchers, injects faults
on the socket and reports every pull.

Each test makes a fresh owner (a logged-in, non-admin account), a second member, and a personal Local FS
connector owned by the owner and claimed by the headless desktop's device id, exactly as the desktop app
claims it on first enable. After each sync the folder itself is the oracle: every file and folder the
desktop's rules put in scope must have one live record with the right parent and revision (the file's
sha256), nothing else may, and only the owner may open the records:

  MISSING    a file or folder in scope has no live record
  STALE      a live record of a path that is gone or out of scope
  DUPLICATE  two live records for one path
  REVISION   a file's record does not carry the sha256 of the file on disk
  PARENT     a record is not under the folder it is in
  LEAK       someone other than the owner opens a record of this connector
  DENIED     the owner cannot open a record of a file in scope
  UNSETTLED  opening a record answered neither 200 nor a refusal: nothing is proven

Needs: ``LOCAL_FS_DESKTOP_AGENT`` (path to desktop_agent.ts) and Node 22 with this checkout's
``backend/nodejs/apps`` and ``frontend`` dependencies installed (``npm ci`` in each).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import stat
import subprocess
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import requests
from connectors.local_fs.local_fs_source_helper import DEFAULT_HOST_ROOT, external_record_id
from helper.record_access import record_access_status
from helper.second_user import NO_ACCESS_STATUSES, SecondUser, create_second_user, delete_second_user
from helper.source_credentials import source_unavailable
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from app.config.constants.arangodb import AppStatus

logger = logging.getLogger("local-fs-sim")

CONNECTOR_TYPE = "Local FS"
AGENT_ENV = "LOCAL_FS_DESKTOP_AGENT"
REPO_ROOT = Path(__file__).resolve().parents[3]
SYNC_TIMEOUT_SEC = int(os.getenv("LOCAL_FS_SIM_SYNC_TIMEOUT_SEC", "600"))
SYNC_POLL_SEC = 2
SETTLE_TIMEOUT_SEC = int(os.getenv("LOCAL_FS_SIM_SETTLE_TIMEOUT_SEC", "90"))

# frontend/electron/local-sync/watcher/ignored-patterns.ts, verbatim.
IGNORED = [re.compile(p) for p in (
    r"(?:^|[/\\])\.DS_Store$", r"(?:^|[/\\])Thumbs\.db$", r"(?:^|[/\\])desktop\.ini$",
    r"\.swp$", r"\.swo$", r"~$", r"(?:^|[/\\])\.#", r"#$", r"(?:^|[/\\])___jb_\w+___$", r"\.crswap$",
    r"\.tmp$", r"(?:^|[/\\])\.git(?:[/\\]|$)", r"(?:^|[/\\])node_modules(?:[/\\]|$)",
    r"(?:^|[/\\])__pycache__(?:[/\\]|$)", r"(?:^|[/\\])\.venv(?:[/\\]|$)",
)]

TREE: dict[str, str | bytes] = {
    "readme.txt": "top level file\n",
    "docs/plan.md": "# plan\n",
    "docs/specs/api.txt": "api spec v1\n",
    "docs/specs/deep/one/two/three.txt": "deep\n",
    "photos/cat.png": b"\x89PNG\r\n\x1a\n fake png bytes",
    "reports/q1.pdf": b"%PDF-1.4 q1",
    "reports/q2.pdf": b"%PDF-1.4 q2",
}


def succeeded(outcome: str | None) -> bool:
    return str(outcome).lower() == "succeeded"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class DesktopAgent:
    """One machine running the PipesHub desktop app, headless, on this checkout's desktop code."""

    def __init__(self, work: Path, device_id: str) -> None:
        script = os.getenv(AGENT_ENV, "")
        if not script or not Path(script).is_file():
            source_unavailable(f"{AGENT_ENV} must point at connectors-simulator's harness/local_fs/node/desktop_agent.ts")
        self.script = Path(script)
        self.device_id = device_id
        self.port = _free_port()
        self.user_data = work / f"userdata-{device_id}"
        self.log_path = work / f"desktop-{device_id}-{self.port}.log"
        self.proc: subprocess.Popen | None = None

    def start(self, timeout: float = 120) -> None:
        apps = REPO_ROOT / "backend/nodejs/apps"
        env = {**os.environ, "PIPESHUB_ROOT": str(REPO_ROOT), "AGENT_PORT": str(self.port),
               "USER_DATA": str(self.user_data), "DEVICE_ID": self.device_id,
               "DEVICE_NAME": f"IT laptop {self.device_id}",
               "TS_NODE_PROJECT": str(REPO_ROOT / "frontend/electron/tsconfig.json"),
               "TS_NODE_TRANSPILE_ONLY": "true"}
        self.user_data.mkdir(parents=True, exist_ok=True)
        log = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            ["node", "-r", str(apps / "node_modules/ts-node/register"), str(self.script)],
            cwd=REPO_ROOT / "frontend", env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"desktop agent exited ({self.proc.returncode}); see {self.log_path}")
            if f"AGENT_READY {self.port}" in self.log_path.read_text(errors="replace"):
                return
            time.sleep(0.2)
        raise TimeoutError(f"desktop agent did not start; see {self.log_path}")

    def ctl(self, route: str, body: dict[str, Any] | None = None, timeout: float = 180) -> dict[str, Any]:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/ctl/{route}", method="POST",
                                     data=json.dumps(body or {}).encode(), headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return json.loads(exc.read() or b"{}") | {"httpStatus": exc.code}

    def quit(self) -> None:
        """What app quit does: drain the watchers, then exit."""
        if self.proc and self.proc.poll() is None:
            self.ctl("shutdown")
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.kill()
        self.proc = None

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, 9)
            self.proc.wait(timeout=20)
        self.proc = None


class LocalFsWorld:
    """One owner, one other member, one personal Local FS connector, one folder, one desktop."""

    def __init__(self, client: PipeshubClient, graph: Any, owner: SecondUser, other: SecondUser, work: Path) -> None:
        self.client = client
        self.graph = graph
        self.owner = owner
        self.other = other
        self.work = work
        self.folder = work / "folder"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.device_id = f"it-device-{uuid.uuid4().hex[:8]}"
        self.desktop: DesktopAgent | None = None
        self.connector_id = ""
        self.filters: dict[str, Any] = {}
        self.unreadable: set[str] = set()

    # ---- the folder -----------------------------------------------------------------------------------

    def path(self, rel: str) -> Path:
        return self.folder / rel

    def write(self, rel: str, content: str | bytes) -> None:
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content.encode() if isinstance(content, str) else content)

    def remove(self, rel: str) -> None:
        p = self.path(rel)
        shutil.rmtree(p) if p.is_dir() else p.unlink()

    def move(self, src: str, dst: str) -> None:
        self.path(dst).parent.mkdir(parents=True, exist_ok=True)
        os.rename(self.path(src), self.path(dst))

    # ---- the desktop ----------------------------------------------------------------------------------

    def launch_desktop(self) -> DesktopAgent:
        agent = DesktopAgent(self.work, self.device_id)
        agent.start()
        token = agent.ctl("token", {"accessToken": self.owner.token, "apiBaseUrl": self.client.base_url})
        assert token.get("ok") and token.get("connected"), f"the desktop could not connect to the stack: {token}"
        self.desktop = agent
        if self.connector_id:
            self.start_watcher()
        return agent

    def start_watcher(self, include_subfolders: bool = True) -> None:
        assert self.desktop
        result = self.desktop.ctl("start", {
            "connectorId": self.connector_id, "connectorName": "IT folder", "rootPath": str(self.folder),
            "includeSubfolders": include_subfolders, "connectorDisplayType": CONNECTOR_TYPE,
            "syncStrategy": "MANUAL"})
        assert result.get("ok"), f"watcher did not start: {result}"

    def settle(self) -> None:
        if self.desktop and self.desktop.proc:
            result = self.desktop.ctl("settle", {"quietMs": 2500, "timeoutMs": 90000})
            assert result.get("ok"), f"the desktop watcher did not go quiet: {result}"

    # ---- PipesHub -------------------------------------------------------------------------------------

    def _owner(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        return requests.request(method, f"{self.client.base_url}{path}", headers=self.owner.headers,
                                timeout=self.client.timeout_seconds, **kwargs)

    async def create_connector(self, *, include_subfolders: bool = True) -> None:
        resp = self._owner("POST", "/api/v1/connectors/", json={
            "connectorType": CONNECTOR_TYPE, "instanceName": f"local-fs-sim-{uuid.uuid4().hex[:8]}",
            "scope": "personal",
            "config": {"sync": {"sync_root_path": str(self.folder), "include_subfolders": include_subfolders,
                                "batchSize": "50"}, "filters": self.filters}})
        assert resp.status_code in (200, 201), f"create connector: {resp.status_code} {resp.text[:300]}"
        body = resp.json()
        connector = body.get("connector") or {}
        self.connector_id = connector.get("connectorId") or connector.get("_key")
        assert self.connector_id, f"no connector id in {body}"
        if self.desktop is None:
            self.launch_desktop()
        else:
            self.start_watcher(include_subfolders)

    def connector(self) -> dict[str, Any]:
        resp = self._owner("GET", f"/api/v1/connectors/{self.connector_id}")
        resp.raise_for_status()
        body = resp.json()
        return body.get("connector") or body

    async def _wait_idle(self) -> None:
        running = {AppStatus.SYNCING.value, AppStatus.FULL_SYNCING.value, AppStatus.QUEUED.value}
        deadline = time.monotonic() + SYNC_TIMEOUT_SEC
        while self.connector().get("status") in running:
            assert time.monotonic() < deadline, f"connector {self.connector_id} never went idle"
            await asyncio.sleep(SYNC_POLL_SEC)

    async def _run(self, trigger: Any) -> str | None:
        self.settle()
        await self._wait_idle()
        before = self.connector().get("lastSyncFinishedAtTimestamp") or 0
        response = trigger()
        assert response.status_code < 300, f"starting the sync was refused: {response.status_code} {response.text[:300]}"
        deadline = time.monotonic() + SYNC_TIMEOUT_SEC
        while True:
            doc = self.connector()
            if (doc.get("lastSyncFinishedAtTimestamp") or 0) > before and doc.get("status") == AppStatus.IDLE.value:
                return doc.get("lastSyncOutcome")
            assert time.monotonic() < deadline, f"no sync ended within {SYNC_TIMEOUT_SEC}s: {doc.get('status')}"
            await asyncio.sleep(SYNC_POLL_SEC)

    async def enable(self) -> str | None:
        """First enable from the desktop app: claims this device as the owner and syncs."""
        return await self._run(lambda: self._owner("POST", f"/api/v1/connectors/{self.connector_id}/toggle", json={
            "type": "sync", "deviceId": self.device_id, "deviceName": f"IT laptop {self.device_id}"}))

    async def sync(self, *, full: bool = False) -> str | None:
        return await self._run(lambda: self._owner("POST", f"/api/v1/connectors/{self.connector_id}/resync",
                                                   json={"connectorName": CONNECTOR_TYPE, "fullSync": full}))

    def resync_response(self, *, full: bool = False) -> requests.Response:
        return self._owner("POST", f"/api/v1/connectors/{self.connector_id}/resync",
                           json={"connectorName": CONNECTOR_TYPE, "fullSync": full})

    async def records(self) -> list[dict[str, Any]]:
        params = {"c": self.connector_id}
        if os.getenv("TEST_GRAPH_DB_TYPE", "neo4j").lower() == "arango":
            rows = await self.graph.execute_query(_RECORDS_AQL, bind_vars=params)
        else:
            rows = await self.graph.client.execute_query(_RECORDS_CYPHER, params)
        return [r for r in (rows or []) if not r.get("isDeleted") and not r.get("deletedAt")]

    # ---- the check ------------------------------------------------------------------------------------

    def expected(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}

        def visit(d: Path) -> None:
            for ent in os.scandir(d):
                abs_p = Path(ent.path)
                rel = unicodedata.normalize("NFC", abs_p.relative_to(self.folder).as_posix())
                if any(p.search(rel) or p.search(str(abs_p)) for p in IGNORED):
                    continue
                st = os.lstat(abs_p)
                parent = rel.rsplit("/", 1)[0] if "/" in rel else None
                if stat.S_ISDIR(st.st_mode):
                    out[rel] = {"dir": True, "parent": parent}
                    visit(abs_p)
                elif stat.S_ISREG(st.st_mode):
                    try:
                        digest = hashlib.sha256(abs_p.read_bytes()).hexdigest()
                    except OSError:
                        digest = None
                    out[rel] = {"dir": False, "parent": parent, "sha256": digest}

        visit(self.folder)
        return out

    async def problems(self) -> list[dict[str, Any]]:
        expected = self.expected()
        by_ext = {external_record_id(self.connector_id, rel): rel for rel in expected}
        records = await self.records()
        held = Counter(r["externalRecordId"] for r in records)
        found: list[dict[str, Any]] = []
        for rel, info in expected.items():
            eid = external_record_id(self.connector_id, rel)
            rows = [r for r in records if r["externalRecordId"] == eid]
            if not rows:
                if rel not in self.unreadable:
                    found.append({"kind": "MISSING", "path": rel})
                continue
            if held[eid] > 1:
                found.append({"kind": "DUPLICATE", "path": rel})
            r = rows[0]
            want_parent = external_record_id(self.connector_id, info["parent"]) if info["parent"] else None
            if r.get("externalParentId") != want_parent:
                found.append({"kind": "PARENT", "path": rel})
            if not info["dir"] and info["sha256"] and r.get("revision") != info["sha256"]:
                found.append({"kind": "REVISION", "path": rel})
        for r in records:
            if r["externalRecordId"] not in by_ext:
                found.append({"kind": "STALE", "name": r.get("recordName")})
        for r in records:
            rel = by_ext.get(r["externalRecordId"])
            if rel is None or expected[rel]["dir"]:
                continue
            owner_status = record_access_status(self.owner, r["id"])
            other_status = record_access_status(self.other, r["id"])
            if owner_status in NO_ACCESS_STATUSES:
                found.append({"kind": "DENIED", "path": rel, "status": owner_status})
            elif owner_status != 200:
                found.append({"kind": "UNSETTLED", "path": rel, "status": owner_status})
            if other_status == 200:
                found.append({"kind": "LEAK", "path": rel, "user": self.other.email})
            elif other_status not in NO_ACCESS_STATUSES:
                found.append({"kind": "UNSETTLED", "path": rel, "status": other_status})
        return found

    async def assert_matches(self, what: str) -> None:
        """Poll: graph writes land a little after the run's end is recorded."""
        deadline = time.monotonic() + SETTLE_TIMEOUT_SEC
        while True:
            found = await self.problems()
            if not found:
                return
            if time.monotonic() >= deadline:
                pytest.fail(f"{what}: PipesHub does not match the folder: {found[:20]}")
            await asyncio.sleep(10)

    def stream(self, record_id: str) -> requests.Response:
        return self.owner.get(f"/api/v1/knowledgeBase/stream/record/{record_id}")


_RECORDS_CYPHER = (
    "MATCH (r:Record {connectorId: $c}) RETURN r.id AS id, r.externalRecordId AS externalRecordId, "
    "r.externalRevisionId AS revision, r.recordName AS recordName, r.externalParentId AS externalParentId, "
    "r.isDeleted AS isDeleted, r.deletedAt AS deletedAt"
)
_RECORDS_AQL = (
    "FOR r IN records FILTER r.connectorId == @c RETURN {id: r._key, externalRecordId: r.externalRecordId, "
    "revision: r.externalRevisionId, recordName: r.recordName, externalParentId: r.externalParentId, "
    "isDeleted: r.isDeleted, deletedAt: r.deletedAt}"
)


@pytest_asyncio.fixture(loop_scope="session")
async def local_fs_world(pipeshub_client: PipeshubClient, graph_provider: Any) -> Any:
    if not os.getenv(AGENT_ENV):
        source_unavailable(f"{AGENT_ENV} is not set: the Local FS simulator suite needs the headless desktop")
    work = DEFAULT_HOST_ROOT / f"sim-{uuid.uuid4().hex[:10]}"
    work.mkdir(parents=True)
    owner = create_second_user(pipeshub_client)
    other = create_second_user(pipeshub_client)
    w = LocalFsWorld(pipeshub_client, graph_provider, owner, other, work)
    try:
        yield w
    finally:
        if w.desktop:
            w.desktop.kill()
        if w.connector_id:
            try:
                w._owner("DELETE", f"/api/v1/connectors/{w.connector_id}")
            except requests.RequestException as exc:
                logger.warning("could not delete connector %s: %s", w.connector_id, exc)
        for dirpath, dirnames, _ in os.walk(work):
            for name in dirnames:
                os.chmod(os.path.join(dirpath, name), 0o755)
        shutil.rmtree(work, ignore_errors=True)
        delete_second_user(pipeshub_client, owner)
        delete_second_user(pipeshub_client, other)
