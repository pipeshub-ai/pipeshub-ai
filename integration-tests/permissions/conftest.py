"""Seeded graph fixture for the ``authenticatedAs`` suite.

The feature under test is permission resolution, not a connector sync, so the graph is
seeded directly through the provider: a connector reached only through a link, one the
user owns outright, and one where only the source account has access. Driving it through
a real Jira/GitLab sync would need a fixture account whose source email differs from the
PipesHub login, which none of the shared tenants can offer.

Everything is keyed ``it-aa-`` and removed in teardown, so a failed run cannot poison the
next one and nothing collides with other suites sharing the database.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator

import pytest_asyncio

from app.config.constants.arangodb import CollectionNames, Connectors
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol

logger = logging.getLogger(__name__)

_NOW = get_epoch_timestamp_in_ms()
_CONNECTOR = Connectors.GITLAB.value

P = "it-aa-"
ORG = f"{P}org"

CREATOR = f"{P}creator"
SOURCE = f"{P}source"
STRANGER = f"{P}stranger"

CREATOR_UID = f"{P}uid-creator"
STRANGER_UID = f"{P}uid-stranger"
# The source row carries the source system's own id, never the email
SOURCE_UID = f"{P}src-42"

APP_LINKED = f"{P}app-linked"
APP_OWN = f"{P}app-own"
APP_OTHER = f"{P}app-other"

# rg -> (app, creator role, source role)
GROUPS: dict[str, tuple[str, str | None, str | None]] = {
    f"{P}rg-src-stronger": (APP_LINKED, "READER", "OWNER"),
    f"{P}rg-own-stronger": (APP_LINKED, "OWNER", "READER"),
    f"{P}rg-src-only": (APP_LINKED, None, "OWNER"),
    f"{P}rg-own": (APP_OWN, "WRITER", None),
    f"{P}rg-other": (APP_OTHER, None, "OWNER"),
}
RECORD_OF = {rg: rg.replace("rg-", "rec-") for rg in GROUPS}
APPS = (APP_LINKED, APP_OWN, APP_OTHER)


def _user(key: str, user_id: str, email: str, *, active: bool) -> dict[str, Any]:
    return {
        "id": key, "userId": user_id, "email": email, "fullName": key,
        "orgId": ORG, "isActive": active,
    }


def _app(key: str) -> dict[str, Any]:
    return {
        "id": key, "name": key, "type": _CONNECTOR, "appGroup": "GitLab",
        "scope": "team", "orgId": ORG, "permissionModel": "RECORD_LEVEL",
        "isActive": True, "isConfigured": True, "isAuthenticated": True,
        "createdBy": CREATOR_UID, "authenticatedBy": CREATOR_UID,
        "createdAtTimestamp": _NOW, "updatedAtTimestamp": _NOW,
    }


def _edge(frm: str, frm_col: str, to: str, to_col: str, **extra: Any) -> dict[str, Any]:
    return {"from_id": frm, "from_collection": frm_col, "to_id": to,
            "to_collection": to_col, **extra}


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def seeded_graph(graph_provider: "GraphProviderProtocol") -> AsyncGenerator[dict[str, Any], None]:
    """Seed the fixture graph, hand back the ids, then delete everything it wrote."""
    try:
        # A database that predates this PR has no authenticatedAs storage yet
        await graph_provider.ensure_schema()
    except Exception as e:  # pragma: no cover - only bites on a fresh database
        logger.warning("ensure_schema failed, continuing: %s", e)

    users = CollectionNames.USERS.value
    apps = CollectionNames.APPS.value
    rgs = CollectionNames.RECORD_GROUPS.value
    recs = CollectionNames.RECORDS.value
    perm = CollectionNames.PERMISSION.value
    belongs = CollectionNames.BELONGS_TO.value
    inherit = CollectionNames.INHERIT_PERMISSIONS.value
    user_app = CollectionNames.USER_APP_RELATION.value

    await graph_provider.batch_upsert_nodes(
        [{"id": ORG, "name": ORG, "accountType": "enterprise", "isActive": True}],
        CollectionNames.ORGS.value,
    )
    await graph_provider.batch_upsert_nodes([
        _user(CREATOR, CREATOR_UID, f"{CREATOR}@pipeshub.test", active=True),
        _user(SOURCE, SOURCE_UID, f"{SOURCE}@source.test", active=False),
        _user(STRANGER, STRANGER_UID, f"{STRANGER}@pipeshub.test", active=True),
    ], users)
    await graph_provider.batch_upsert_nodes([_app(a) for a in APPS], apps)
    await graph_provider.batch_upsert_nodes([
        {"id": rg, "groupName": rg, "connectorId": app, "connectorName": _CONNECTOR,
         "orgId": ORG, "groupType": "PROJECT", "externalGroupId": rg,
         "createdAtTimestamp": _NOW, "updatedAtTimestamp": _NOW}
        for rg, (app, _, _) in GROUPS.items()
    ], rgs)
    await graph_provider.batch_upsert_nodes([
        {"id": RECORD_OF[rg], "recordName": RECORD_OF[rg], "recordType": "FILE",
         "externalRecordId": RECORD_OF[rg], "origin": "CONNECTOR", "connectorId": app,
         "connectorName": _CONNECTOR, "orgId": ORG, "indexingStatus": "COMPLETED",
         "isDeleted": False, "virtualRecordId": f"v-{RECORD_OF[rg]}", "version": 1,
         "createdAtTimestamp": _NOW, "updatedAtTimestamp": _NOW}
        for rg, (app, _, _) in GROUPS.items()
    ], recs)
    # A record is only listed once it has its typed sibling, the way a sync writes it
    await graph_provider.batch_upsert_nodes([
        {"id": RECORD_OF[rg], "name": RECORD_OF[rg], "isFile": True,
         "extension": "txt", "mimeType": "text/plain", "orgId": ORG}
        for rg in GROUPS
    ], CollectionNames.FILES.value)

    try:
        edges: list[tuple[list[dict[str, Any]], str]] = []
        edges.append(([_edge(u, users, ORG, CollectionNames.ORGS.value, entityType="ORGANIZATION", createdAtTimestamp=_NOW)
                       for u in (CREATOR, SOURCE, STRANGER)], belongs))
        # The creator owns `own` and is a plain member of `other`; the link alone must reach `linked`
        relation = {"syncState": "COMPLETED", "lastSyncUpdate": _NOW,
                    "createdAtTimestamp": _NOW, "updatedAtTimestamp": _NOW}
        edges.append(([_edge(CREATOR, users, APP_OWN, apps, sourceUserId=CREATOR_UID, **relation),
                       _edge(CREATOR, users, APP_OTHER, apps, sourceUserId=CREATOR_UID, **relation)]
                      + [_edge(SOURCE, users, a, apps, sourceUserId=SOURCE_UID, **relation) for a in APPS], user_app))
        edges.append(([_edge(rg, rgs, app, apps, createdAtTimestamp=_NOW) for rg, (app, _, _) in GROUPS.items()], belongs))
        edges.append(([_edge(RECORD_OF[rg], recs, rg, rgs, createdAtTimestamp=_NOW) for rg in GROUPS], belongs))
        edges.append(([_edge(RECORD_OF[rg], recs, rg, rgs, createdAtTimestamp=_NOW) for rg in GROUPS], inherit))
        edges.append(([_edge(RECORD_OF[rg], recs, RECORD_OF[rg], CollectionNames.FILES.value,
                             createdAtTimestamp=_NOW) for rg in GROUPS], CollectionNames.IS_OF_TYPE.value))
        grants = [_edge(who, users, rg, rgs, type="USER", role=role, createdAtTimestamp=_NOW)
                  for rg, (_, c_role, s_role) in GROUPS.items()
                  for who, role in ((CREATOR, c_role), (SOURCE, s_role)) if role]
        edges.append((grants, perm))
        for payload, collection in edges:
            await graph_provider.batch_create_edges(payload, collection)

        await graph_provider.upsert_authenticated_as(CREATOR, SOURCE, APP_LINKED, ORG)

        yield {
            "org": ORG, "creator": CREATOR, "creator_uid": CREATOR_UID,
            "source": SOURCE, "source_uid": SOURCE_UID,
            "stranger": STRANGER, "stranger_uid": STRANGER_UID,
            "app_linked": APP_LINKED, "app_own": APP_OWN, "app_other": APP_OTHER,
            "groups": GROUPS, "record_of": RECORD_OF,
        }

    finally:
        for app in APPS:
            try:
                await graph_provider.remove_authenticated_as(app)
            except Exception as e:
                logger.warning("link cleanup failed for %s: %s", app, e)
        for payload, collection in edges:
            for e in payload:
                try:
                    await graph_provider.delete_edge(
                        e["from_id"], e["from_collection"], e["to_id"], e["to_collection"], collection
                    )
                except Exception as err:
                    logger.warning("edge cleanup failed: %s", err)
        for keys, collection in (
            (list(RECORD_OF.values()), CollectionNames.FILES.value),
            (list(RECORD_OF.values()), recs),
            (list(GROUPS), rgs),
            (list(APPS), apps),
            ([CREATOR, SOURCE, STRANGER], users),
            ([ORG], CollectionNames.ORGS.value),
        ):
            try:
                await graph_provider.delete_nodes(keys, collection)
            except Exception as e:
                logger.warning("node cleanup failed for %s: %s", collection, e)
