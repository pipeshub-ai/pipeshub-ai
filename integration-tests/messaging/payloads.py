"""Canonical PipesHub event payloads for integration testing.

Each factory function returns a realistic message envelope matching the
exact shapes produced / consumed in production.
"""

import time
import uuid


def _ts() -> int:
    return int(time.time() * 1000)


def _uid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# record-events
# ---------------------------------------------------------------------------

def new_record_event(
    org_id: str | None = None,
    record_id: str | None = None,
) -> dict:
    org_id = org_id or _uid()
    record_id = record_id or _uid()
    return {
        "eventType": "newRecord",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "recordId": record_id,
            "virtualRecordId": None,
            "recordName": "quarterly-report.pdf",
            "recordType": "FILE",
            "version": 0,
            "connectorName": "drive",
            "origin": "CONNECTOR",
            "extension": "pdf",
            "mimeType": "application/pdf",
            "body": None,
            "createdAtTimestamp": _ts(),
            "updatedAtTimestamp": _ts(),
            "sourceCreatedAtTimestamp": _ts(),
        },
    }


def update_record_event(
    org_id: str | None = None,
    record_id: str | None = None,
) -> dict:
    org_id = org_id or _uid()
    record_id = record_id or _uid()
    return {
        "eventType": "updateRecord",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "recordId": record_id,
            "virtualRecordId": _uid(),
            "recordName": "quarterly-report-v2.pdf",
            "recordType": "FILE",
            "version": 1,
            "connectorName": "drive",
            "origin": "CONNECTOR",
            "extension": "pdf",
            "mimeType": "application/pdf",
            "body": None,
            "createdAtTimestamp": _ts(),
            "updatedAtTimestamp": _ts(),
            "sourceCreatedAtTimestamp": _ts(),
        },
    }


def delete_record_event(
    org_id: str | None = None,
    record_id: str | None = None,
) -> dict:
    org_id = org_id or _uid()
    record_id = record_id or _uid()
    return {
        "eventType": "deleteRecord",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "recordId": record_id,
            "virtualRecordId": _uid(),
            "recordName": "old-doc.docx",
            "recordType": "FILE",
            "version": 2,
            "connectorName": "onedrive",
            "origin": "CONNECTOR",
            "extension": "docx",
            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "body": None,
            "createdAtTimestamp": _ts(),
            "updatedAtTimestamp": _ts(),
            "sourceCreatedAtTimestamp": _ts(),
        },
    }


def reindex_record_event(
    org_id: str | None = None,
    record_id: str | None = None,
) -> dict:
    org_id = org_id or _uid()
    record_id = record_id or _uid()
    return {
        "eventType": "reindexRecord",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "recordId": record_id,
            "virtualRecordId": _uid(),
            "recordName": "re-indexed-sheet.xlsx",
            "recordType": "FILE",
            "version": 3,
            "connectorName": "drive",
            "origin": "CONNECTOR",
            "extension": "xlsx",
            "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "body": None,
            "createdAtTimestamp": _ts(),
            "updatedAtTimestamp": _ts(),
            "sourceCreatedAtTimestamp": _ts(),
        },
    }


def bulk_delete_records_event(
    org_id: str | None = None,
    connector_id: str | None = None,
    count: int = 5,
) -> dict:
    org_id = org_id or _uid()
    connector_id = connector_id or _uid()
    return {
        "eventType": "bulkDeleteRecords",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "connectorId": connector_id,
            "virtualRecordIds": [_uid() for _ in range(count)],
            "totalRecords": count,
        },
    }


# ---------------------------------------------------------------------------
# entity-events
# ---------------------------------------------------------------------------

def org_created_event(org_id: str | None = None) -> dict:
    org_id = org_id or _uid()
    return {
        "eventType": "orgCreated",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "accountType": "business",
            "registeredName": "Acme Corp",
        },
    }


def org_updated_event(org_id: str | None = None) -> dict:
    org_id = org_id or _uid()
    return {
        "eventType": "orgUpdated",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "registeredName": "Acme Corp International",
        },
    }


def org_deleted_event(org_id: str | None = None) -> dict:
    org_id = org_id or _uid()
    return {
        "eventType": "orgDeleted",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
        },
    }


def user_added_event(
    org_id: str | None = None,
    user_id: str | None = None,
) -> dict:
    org_id = org_id or _uid()
    user_id = user_id or _uid()
    return {
        "eventType": "userAdded",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "userId": user_id,
            "email": "alice@acme.com",
            "fullName": "Alice Johnson",
            "firstName": "Alice",
            "middleName": None,
            "lastName": "Johnson",
            "designation": "Engineer",
            "businessPhones": ["+1-555-0100"],
            "syncAction": "immediate",
        },
    }


def user_updated_event(
    org_id: str | None = None,
    user_id: str | None = None,
) -> dict:
    org_id = org_id or _uid()
    user_id = user_id or _uid()
    return {
        "eventType": "userUpdated",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "userId": user_id,
            "email": "alice@acme.com",
            "fullName": "Alice M. Johnson",
            "firstName": "Alice",
            "middleName": "M.",
            "lastName": "Johnson",
            "designation": "Senior Engineer",
            "businessPhones": ["+1-555-0100", "+1-555-0101"],
        },
    }


def user_deleted_event(
    org_id: str | None = None,
    user_id: str | None = None,
) -> dict:
    org_id = org_id or _uid()
    user_id = user_id or _uid()
    return {
        "eventType": "userDeleted",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "userId": user_id,
            "email": "alice@acme.com",
        },
    }


def app_enabled_event(org_id: str | None = None) -> dict:
    org_id = org_id or _uid()
    return {
        "eventType": "appEnabled",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "appGroup": "Google Workspace",
            "appGroupId": _uid(),
            "apps": ["drive", "gmail"],
            "credentialsRoute": "/api/v1/creds/google",
            "refreshTokenRoute": "/api/v1/refresh/google",
            "syncAction": "immediate",
        },
    }


def app_disabled_event(org_id: str | None = None) -> dict:
    org_id = org_id or _uid()
    return {
        "eventType": "appDisabled",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "appGroup": "Google Workspace",
            "appGroupId": _uid(),
            "apps": ["drive", "gmail"],
            "connectorId": _uid(),
        },
    }


def llm_configured_event(org_id: str | None = None) -> dict:
    org_id = org_id or _uid()
    return {
        "eventType": "llmConfigured",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "provider": "openai",
            "model": "gpt-4",
        },
    }


def embedding_model_configured_event(org_id: str | None = None) -> dict:
    org_id = org_id or _uid()
    return {
        "eventType": "embeddingModelConfigured",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "provider": "openai",
            "model": "text-embedding-3-small",
        },
    }


# ---------------------------------------------------------------------------
# sync-events
# ---------------------------------------------------------------------------

def sync_init_event(
    org_id: str | None = None,
    connector_id: str | None = None,
    connector: str = "drive",
) -> dict:
    org_id = org_id or _uid()
    connector_id = connector_id or _uid()
    return {
        "eventType": f"{connector}.init",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "connectorId": connector_id,
        },
    }


def sync_start_event(
    org_id: str | None = None,
    connector_id: str | None = None,
    connector: str = "drive",
) -> dict:
    org_id = org_id or _uid()
    connector_id = connector_id or _uid()
    return {
        "eventType": f"{connector}.start",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "connectorId": connector_id,
            "connector": connector,
            "fullSync": False,
            "scope": "personal",
        },
    }


def sync_resync_event(
    org_id: str | None = None,
    connector_id: str | None = None,
    connector: str = "gmail",
) -> dict:
    org_id = org_id or _uid()
    connector_id = connector_id or _uid()
    return {
        "eventType": f"{connector}.resync",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "connectorId": connector_id,
            "connector": connector,
            "fullSync": True,
        },
    }


def sync_delete_event(
    org_id: str | None = None,
    connector_id: str | None = None,
    connector: str = "drive",
) -> dict:
    org_id = org_id or _uid()
    connector_id = connector_id or _uid()
    return {
        "eventType": f"{connector}.delete",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "connectorId": connector_id,
            "previousIsActive": True,
        },
    }


def sync_reindex_event(
    org_id: str | None = None,
    connector_id: str | None = None,
    connector: str = "confluence",
) -> dict:
    org_id = org_id or _uid()
    connector_id = connector_id or _uid()
    return {
        "eventType": f"{connector}.reindex",
        "timestamp": _ts(),
        "payload": {
            "orgId": org_id,
            "connectorId": connector_id,
            "recordId": _uid(),
            "recordGroupId": _uid(),
            "depth": 0,
            "statusFilters": ["FAILED"],
            "userKey": _uid(),
        },
    }


def sync_user_event(
    connector: str = "drive",
    email: str = "alice@acme.com",
) -> dict:
    return {
        "eventType": f"{connector}.user",
        "timestamp": _ts(),
        "payload": {
            "email": email,
            "connector": connector,
        },
    }


# ---------------------------------------------------------------------------
# health-check
# ---------------------------------------------------------------------------

def health_check_event() -> dict:
    return {
        "eventType": "HEALTH_CHECK",
        "timestamp": _ts(),
        "payload": {},
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALL_RECORD_EVENTS = [
    new_record_event,
    update_record_event,
    delete_record_event,
    reindex_record_event,
    bulk_delete_records_event,
]

ALL_ENTITY_EVENTS = [
    org_created_event,
    org_updated_event,
    org_deleted_event,
    user_added_event,
    user_updated_event,
    user_deleted_event,
    app_enabled_event,
    app_disabled_event,
    llm_configured_event,
    embedding_model_configured_event,
]

ALL_SYNC_EVENTS = [
    sync_init_event,
    sync_start_event,
    sync_resync_event,
    sync_delete_event,
    sync_reindex_event,
    sync_user_event,
]
