# pyright: ignore-file

"""What the Dropbox suites share: setting up a connector on the simulator, driving its syncs and filters, and
reading PipesHub's answer to compare it with Dropbox's.

``ctx`` is what the ``dropbox_team`` / ``dropbox_personal`` fixtures yield: ``sim``, ``connector_id`` and, for a
team, ``users`` (``{key: {"email", "id"}}``, every Dropbox member and outside collaborator).
"""

from __future__ import annotations

import contextlib
import logging
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from connectors.dropbox_sim.dropbox_sim import (
    CLIENT_ID_ENV,
    CLIENT_SECRET_ENV,
    REFRESH_TOKEN_ENV,
    SYNC_TIMEOUT_SEC,
    DropboxSim,
    app,
)
from connectors.google_drive_workspace.drive_workspace_test_utils import (  # type: ignore[import-not-found]
    connector_filters,
    sync_and_wait,
)
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import async_poll_until, wait_for_sync_completion  # type: ignore[import-not-found]
from helper.oauth_token_helper import authenticate_connector_with_refresh_token  # type: ignore[import-not-found]
from helper.pipeshub_users import ensure_pipeshub_user_exists  # type: ignore[import-not-found]
from helper.record_access import wait_for_record_access  # type: ignore[import-not-found]
from helper.second_user import log_in_existing_user, log_out_existing_user  # type: ignore[import-not-found]
from helper.source_credentials import secrets_required, source_unavailable  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("dropbox-sim")
RECORD_TIMEOUT_SEC = 180


def simulator() -> DropboxSim:
    sim = DropboxSim()
    # A run that loaded dropbox_sim.env (CI does on the shard that runs this suite) pointed the connectors at the
    # simulator, so it meant to run the suite: a simulator that is not up fails it, on a pull request too.
    required = secrets_required() or bool(os.getenv("DROPBOX_API_BASE_URL"))
    if not sim.reachable():
        source_unavailable(f"The Dropbox simulator is not reachable at {sim.base_url} (compose profile dropbox-sim)",
                           required=required)
    for name in (CLIENT_ID_ENV, CLIENT_SECRET_ENV, REFRESH_TOKEN_ENV):
        if not os.getenv(name):
            source_unavailable(f"{name} is not set: load integration-tests/connectors/dropbox_sim.env",
                               required=required)
    return sim


async def wait_for_graph_user(graph_provider: GraphProviderProtocol, email: str) -> None:
    """A grant to an email PipesHub does not know yet is skipped, not deferred: wait until the user is active."""

    async def _active() -> dict[str, Any] | None:
        user = await graph_provider.graph_find_user_by_email(email)
        return user if user and user.get("isActive") is not False else None

    await async_poll_until(_active, timeout=120, interval=2, description=f"active PipesHub graph user for {email}")


async def pipeshub_people(users_client: Any, graph_provider: GraphProviderProtocol,
                          people: dict[str, str]) -> dict[str, dict[str, str]]:
    """``{key: email}`` → ``{key: {"email", "id"}}``, each a PipesHub user active in the graph."""
    users = {}
    for key, email in people.items():
        users[key] = {"email": email, "id": ensure_pipeshub_user_exists(users_client, email)[0]}
        await wait_for_graph_user(graph_provider, email)
    return users


@contextlib.asynccontextmanager
async def dropbox_connector(pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol, sim: DropboxSim,
                            connector_type: str, scope: str, *, auto_index: bool = False,
                            sync_values: dict[str, Any] | None = None) -> AsyncIterator[str]:
    """A connector on the simulator, synced once; deleted (and its graph checked gone) on the way out."""
    instance = pipeshub_client.create_connector(
        connector_type=connector_type,
        instance_name=f"dropbox-{scope}-{uuid.uuid4().hex[:8]}",
        scope=scope,
        config={"auth": {"clientId": app()["key"], "clientSecret": app()["secret"]},
                "filters": connector_filters(sync_values=sync_values, manual_indexing=not auto_index)},
        auth_type="OAUTH",
    )
    connector_id = instance.connector_id
    assert connector_id, "the connector has an id"
    try:
        try:
            await authenticate_connector_with_refresh_token(
                connector_id=connector_id, refresh_token_env_var=REFRESH_TOKEN_ENV, token_url=sim.token_url,
                client_id_env_var=CLIENT_ID_ENV, client_secret_env_var=CLIENT_SECRET_ENV,
            )
        except Exception as e:  # noqa: BLE001 - reported as the suite's set-up failure
            pytest.fail(f"Could not authenticate the Dropbox connector at the simulator: {e}")
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=SYNC_TIMEOUT_SEC)
        yield connector_id
    finally:
        logger.info("TEARDOWN: Dropbox connector %s", connector_id)
        try:
            pipeshub_client.toggle_sync(connector_id, enable=False)
        except Exception as e:  # noqa: BLE001 - teardown carries on
            logger.warning("TEARDOWN: disable failed: %s", e)
        pipeshub_client.delete_connector(connector_id)
        await graph_provider.assert_connector_graph_fully_cleaned(connector_id, timeout=180)


async def record(graph: GraphProviderProtocol, cid: str, external_id: str, what: str, present: bool = True) -> Any:
    """Wait until the record is in the graph (``present``) or gone; return it (``None`` when gone).

    A record in the trash (``isDeleted``) counts as gone: nobody can open or find it."""
    found: dict[str, Any] = {}

    async def check() -> bool | None:
        rec = await graph.get_record_by_external_id(cid, external_id)
        live = rec is not None and not getattr(rec, "is_deleted", False)
        found["record"] = rec if live else None
        return live is present or None

    await async_poll_until(check, timeout=RECORD_TIMEOUT_SEC, interval=3,
                           description=f"{what} {'present' if present else 'absent'}")
    return found.get("record")


async def records(graph: GraphProviderProtocol, ctx: dict[str, Any], keys: list[str], present: bool = True) -> dict[str, Any]:
    sim, cid = ctx["sim"], ctx["connector_id"]
    return {key: await record(graph, cid, sim.item_id(key), key, present) for key in keys}


def access(client: PipeshubClient, ctx: dict[str, Any], who: str, record_id: str, expect: bool, what: str) -> None:
    user = log_in_existing_user(client, ctx["users"][who]["id"], ctx["users"][who]["email"])
    try:
        wait_for_record_access(user, record_id, expect_access=expect, description=f"{who}: {what}")
    finally:
        log_out_existing_user(client, user)


async def assert_access_matches_source(client: PipeshubClient, graph: GraphProviderProtocol, ctx: dict[str, Any],
                                       keys: list[str], label: str) -> None:
    """For every file in ``keys`` and every person in ``ctx["users"]``: PipesHub opens it exactly when Dropbox
    lets that person read it. A file nobody may read (deleted, or in an archived team folder) has no record."""
    sim, cid = ctx["sim"], ctx["connector_id"]
    expected = {item["key"]: item for item in sim.expected()["items"]}
    live: dict[str, tuple[str, set[str]]] = {}
    for key in keys:
        item = expected[key]
        readers = set() if item["deleted"] else set(item["readers"])
        rec = await record(graph, cid, item["id"], f"{label}: {key}", present=bool(readers))
        if rec is not None:
            live[key] = (rec.id, readers)
    for who, person in ctx["users"].items():
        user = log_in_existing_user(client, person["id"], person["email"])
        try:
            for key, (record_id, readers) in live.items():
                wait_for_record_access(user, record_id, expect_access=person["email"] in readers,
                                       description=f"{label}: {who} on {key}")
        finally:
            log_out_existing_user(client, user)


def outcome(client: PipeshubClient, cid: str) -> str:
    return str(client.get_connector(cid).get("lastSyncOutcome") or "").lower()


def assert_succeeded(client: PipeshubClient, cid: str, what: str) -> None:
    assert outcome(client, cid) in ("", "succeeded"), f"{what}: the sync reports {outcome(client, cid)!r}"


async def sync(client: PipeshubClient, graph: GraphProviderProtocol, ctx: dict[str, Any]) -> None:
    """An incremental sync, as the schedule would start one."""
    await sync_and_wait(client, graph, ctx["connector_id"])


async def full_sync(client: PipeshubClient, graph: GraphProviderProtocol, ctx: dict[str, Any]) -> None:
    client.resync_connector(ctx["connector_id"], full_sync=True)
    await wait_for_sync_completion(client, graph, ctx["connector_id"], timeout=SYNC_TIMEOUT_SEC)


async def apply_filters(client: PipeshubClient, graph: GraphProviderProtocol, ctx: dict[str, Any], *,
                        sync_values: dict[str, Any] | None = None,
                        indexing_values: dict[str, Any] | None = None, auto_index: bool = False) -> None:
    """Save the filters as the UI does (connector off, save, on again). A sync filter change makes that sync a
    full one. Both blocks are always sent, so a save never leaves an older value behind."""
    indexing = {"enable_manual_sync": {"operator": "is", "type": "boolean", "value": not auto_index}}
    indexing.update(indexing_values or {})
    client.update_connector_filters_sync_safe(
        ctx["connector_id"], filters={"sync": {"values": sync_values or {}}, "indexing": {"values": indexing}})
    await wait_for_sync_completion(client, graph, ctx["connector_id"], timeout=SYNC_TIMEOUT_SEC)


def assert_no_simulator_problems(sim: Any) -> None:
    problems = sim.problems()
    assert not problems, f"requests the simulator does not model or answers off the spec: {problems}"
