"""Self-hosted connector sources for the cleanup scenarios.

MinIO and PostgreSQL run in every integration stack (no outside account), so
the cleanup suite can create, sync and delete real connectors of two types. The
defaults are the ones connectors/minio/conftest.py and connectors/postgres/conftest.py
use, matching deployment/docker-compose/docker-compose.integration.*.yml.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any

from connector_lifecycle import create_connector_and_await_sync
from connectors.minio.minio_storage_helper import MinioStorageHelper
from helper import delete_footprint as fp
from helper.run_folder import folder_filter

if TYPE_CHECKING:
    from connectors.postgres.postgres_source_helper import PostgresSourceHelper

logger = logging.getLogger("cleanup-sources")

MINIO_BUCKET = os.getenv("MINIO_TEST_BUCKET", "pipeshub-connector-test")
MINIO_KEY = os.getenv("MINIO_ROOT_USER", "pipeshubtest")
MINIO_SECRET = os.getenv("MINIO_ROOT_PASSWORD", "pipeshubtest123")
PG_DB = os.getenv("POSTGRES_TEST_DB", "pipeshub_connector_test")
PG_USER = os.getenv("POSTGRES_TEST_USER", "pipeshubtest")
PG_PASSWORD = os.getenv("POSTGRES_TEST_PASSWORD", "pipeshubtest123")


def unique_text(label: str) -> bytes:
    return f"# {label}\n\nOnly {label} holds this sentence ({uuid.uuid4().hex}).\n".encode()


def minio() -> MinioStorageHelper:
    return MinioStorageHelper(
        access_key=MINIO_KEY,
        secret_key=MINIO_SECRET,
        endpoint_url=os.getenv("MINIO_TEST_ENDPOINT", "http://localhost:9000"),
    )


def minio_config(folder: str) -> dict[str, Any]:
    return {
        "auth": {
            "endpointUrl": os.getenv("MINIO_CONNECTOR_ENDPOINT", "http://minio:9000"),
            "accessKey": MINIO_KEY,
            "secretKey": MINIO_SECRET,
            "useSsl": False,
            "verifySsl": False,
        },
        "filters": folder_filter(folder),
    }


def upload_files(storage: MinioStorageHelper, folder: str, files: dict[str, bytes]) -> None:
    for name, body in files.items():
        storage.upload_object(MINIO_BUCKET, f"{folder}{name}", body, content_type="text/markdown")


async def create_minio_connector(
    pipeshub_client, graph_provider, *, name: str, folder: str, expected_records: int
) -> str:
    state: dict[str, Any] = {}
    await create_connector_and_await_sync(
        pipeshub_client, graph_provider, state,
        connector_type="MinIO",
        connector_name=name,
        connector_config=minio_config(folder),
        expected_records=expected_records,
    )
    return state["connector_id"]


def postgres(schema: str) -> "PostgresSourceHelper":
    # Imported here so collecting the suite does not need the psycopg driver.
    from connectors.postgres.postgres_source_helper import PostgresSourceHelper

    return PostgresSourceHelper(
        f"host={os.getenv('POSTGRES_TEST_HOST', 'localhost')} "
        f"port={os.getenv('POSTGRES_TEST_PORT', '5433')} dbname={PG_DB} "
        f"user={PG_USER} password={PG_PASSWORD}",
        schema=schema,
    )


async def create_postgres_connector(
    pipeshub_client, graph_provider, *, name: str, schema: str, expected_records: int
) -> str:
    state: dict[str, Any] = {}
    await create_connector_and_await_sync(
        pipeshub_client, graph_provider, state,
        connector_type="PostgreSQL",
        connector_name=name,
        connector_config={
            "auth": {
                "host": os.getenv("POSTGRES_CONNECTOR_HOST", "postgres-source"),
                "port": int(os.getenv("POSTGRES_CONNECTOR_PORT", "5432")),
                "database": PG_DB,
                "username": PG_USER,
                "password": PG_PASSWORD,
            },
            "filters": {"sync": {"values": {"schemas": {
                "operator": "in", "type": "multiselect", "value": [schema],
            }}}},
        },
        expected_records=expected_records,
        scope="team",
    )
    return state["connector_id"]


def delete_connector(pipeshub_client, connector_id: str) -> None:
    try:
        pipeshub_client.toggle_sync(connector_id, enable=False)
    except Exception as exc:  # noqa: BLE001 - deleting is what matters
        logger.warning("Could not pause %s before deleting it: %s", connector_id, exc)
    pipeshub_client.delete_connector(connector_id)


async def wait_for_connector_delete(
    graph_provider, vector_store, connector_id: str, graph_fp: "fp.GraphFootprint", timeout: int = 300
) -> None:
    """Let a delete finish before survivors are compared; the tests assert the outcome."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        nodes = await graph_provider.count_existing_nodes(graph_fp.handles)
        points = await vector_store.count_for_connector(connector_id)
        if not nodes and not points:
            return
        await asyncio.sleep(fp.POLL)
    logger.warning("Connector %s delete still incomplete after %ss", connector_id, timeout)


# --------------------------------------------------------------------------- #
# Knowledge-base uploads
# --------------------------------------------------------------------------- #

INDEXING_TIMEOUT = 300


async def wait_for_virtual_id(kb_client, record_id: str) -> str:
    """The record's own virtual id, which is what the other stores key on."""
    deadline = asyncio.get_event_loop().time() + INDEXING_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        payload = kb_client.get_record(record_id)
        virtual_id = (payload.get("record") or {}).get("virtualRecordId")
        if virtual_id:
            return str(virtual_id)
        await asyncio.sleep(fp.POLL)
    raise AssertionError(
        f"Record {record_id} never got a virtualRecordId within "
        f"{INDEXING_TIMEOUT}s, so there is nothing to key the other three "
        "stores on."
    )


async def wait_for_embeddings(vector_store, virtual_id: str, record_id: str) -> None:
    """Block until the record is really in the vector database.

    Fails rather than proceeding on an empty result. Every assertion downstream
    would otherwise be checking that nothing is nothing, which passes and means
    nothing — the exact failure these tests exist to rule out.
    """
    deadline = asyncio.get_event_loop().time() + INDEXING_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        if await vector_store.count_for_virtual_record(virtual_id):
            logger.info("Record %s indexed as virtual record %s", record_id, virtual_id)
            return
        await asyncio.sleep(fp.POLL)

    raise AssertionError(
        f"Record {record_id} (virtual {virtual_id}) produced no embeddings "
        f"within {INDEXING_TIMEOUT}s. Check that an embedding model is "
        "configured for the org — with none, indexing fails and the record is "
        "dead-lettered rather than falling back to the local embedder."
    )


def folder_id_of(payload: dict[str, Any]) -> str:
    """The id out of a folder-create reply, whichever key it used."""
    for container in (payload, payload.get("folder") or {}, payload.get("data") or {}):
        if isinstance(container, dict):
            for key in ("id", "folderId", "_key"):
                value = container.get(key)
                if value:
                    return str(value)
    raise AssertionError(f"No folder id in the create response: {payload}")


async def upload_to_kb(
    kb_client, vector_store, kb_id: str, name: str, body: bytes, *, folder_id: str | None = None
) -> "fp.Tracked":
    """Upload one file and wait until it is indexed (or deduplicated onto indexed content)."""
    upload = kb_client.upload_file(kb_id, name, body, folder_id=folder_id, mimetype="text/markdown")
    assert upload["summary"]["failed"] == 0, f"Upload of {name} failed: {upload}"
    record_id = str(upload["records"][0]["recordId"])
    virtual_id = await wait_for_virtual_id(kb_client, record_id)
    await wait_for_embeddings(vector_store, virtual_id, record_id)
    record = kb_client.get_record(record_id).get("record") or {}
    upload_document_id = record.get("externalRecordId")
    assert upload_document_id, f"{name} has no externalRecordId pointing at its uploaded file: {record}"
    return fp.Tracked(
        name=name,
        record_id=record_id,
        virtual_record_id=virtual_id,
        upload_document_id=str(upload_document_id),
        status=record.get("indexingStatus"),
    )
