"""Shared fixtures for the strict OpenAPI audit of /api/v1/org."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator

import pytest
from bson import ObjectId
from pymongo import MongoClient
from pymongo.database import Database

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.clients.org_client import OrgClient  # noqa: E402
from helper.config import MONGO_DB_NAME, MONGO_URI  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import second_user  # noqa: E402, F401 - fixture

from org_audit_support import (  # noqa: E402
    LOGO_COLLECTION,
    ORG_COLLECTION,
    ScopedCaller,
    current_org_id,
    forget_access_token,
    mint_narrow_scope_token,
)

# The fields PUT /org can change; everything else on the org is left alone.
_PROFILE_FIELDS = ("contactEmail", "registeredName", "shortName", "permanentAddress")


@pytest.fixture
def org_intact(org_client: OrgClient) -> Iterator[str]:
    """Yield the shared org's id and fail on teardown if the test replaced or deleted it."""
    org_id = current_org_id(org_client)
    yield org_id
    assert current_org_id(org_client) == org_id, "the shared org changed during the test"


@pytest.fixture(scope="session")
def org_db() -> Iterator[Database[Any]]:
    client: MongoClient[Any] = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    try:
        yield client[MONGO_DB_NAME]
    finally:
        client.close()


@pytest.fixture
def narrow_scope(pipeshub_client: PipeshubClient) -> Iterator[ScopedCaller]:
    """Calls org routes with an OAuth token of the suite's client that holds no ``org:*`` scope."""
    token = mint_narrow_scope_token(pipeshub_client.base_url, pipeshub_client.timeout_seconds)
    try:
        yield ScopedCaller(pipeshub_client.base_url, token, pipeshub_client.timeout_seconds)
    finally:
        forget_access_token(token)


@pytest.fixture
def org_profile_restored(org_intact: str, org_db: Database[Any]) -> Iterator[str]:
    """Put the org's profile fields back exactly as they were (PUT /org cannot unset a field)."""
    oid = ObjectId(org_intact)
    before = org_db[ORG_COLLECTION].find_one({"_id": oid}) or {}
    yield org_intact
    present = {f: before[f] for f in _PROFILE_FIELDS if f in before}
    missing = {f: "" for f in _PROFILE_FIELDS if f not in before}
    update: dict[str, Any] = {}
    if present:
        update["$set"] = present
    if missing:
        update["$unset"] = missing
    org_db[ORG_COLLECTION].update_one({"_id": oid}, update)


@pytest.fixture
def onboarding_restored(org_intact: str, org_db: Database[Any]) -> Iterator[str]:
    oid = ObjectId(org_intact)
    before = (org_db[ORG_COLLECTION].find_one({"_id": oid}) or {}).get("onBoardingStatus")
    yield org_intact
    if before is None:
        org_db[ORG_COLLECTION].update_one({"_id": oid}, {"$unset": {"onBoardingStatus": ""}})
    else:
        org_db[ORG_COLLECTION].update_one({"_id": oid}, {"$set": {"onBoardingStatus": before}})


@pytest.fixture
def logo_restored(org_intact: str, org_db: Database[Any]) -> Iterator[str]:
    """The org's logo records come back exactly as they were, whatever the test did to them."""
    oid = ObjectId(org_intact)
    before = list(org_db[LOGO_COLLECTION].find({"orgId": oid}))
    yield org_intact
    org_db[LOGO_COLLECTION].delete_many({"orgId": oid})
    if before:
        org_db[LOGO_COLLECTION].insert_many(before)


@pytest.fixture
def no_logo_record(logo_restored: str, org_db: Database[Any]) -> str:
    """The org has no logo record at all (not even a cleared one)."""
    org_db[LOGO_COLLECTION].delete_many({"orgId": ObjectId(logo_restored)})
    return logo_restored
