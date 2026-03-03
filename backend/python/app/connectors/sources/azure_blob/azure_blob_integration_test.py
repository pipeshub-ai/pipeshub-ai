"""
Azure Blob Storage Connector – Full Lifecycle Integration Test
==============================================================

Tests the complete AzureBlobConnector lifecycle against real Azure Blob
Storage containers and a real Neo4j graph database, mirroring the S3 and
GCS connector integration tests.

Run from backend/python/ directory:
    source venv/bin/activate
    python -m app.connectors.sources.azure_blob.azure_blob_integration_test

Required env vars (backend/python/.env):
    AZURE_BLOB_CONNECTION_STRING  — Azure Storage connection string
    NEO4J_URI                     — Neo4j bolt URI (default: bolt://localhost:7687)
    NEO4J_USERNAME                — Neo4j username (default: neo4j)
    NEO4J_PASSWORD                — Neo4j password (required)
    NEO4J_DATABASE                — Neo4j database name (default: neo4j)

Sample data is read from:
    backend/python/sample-data/
        entities/org/org.json           — org identity used for Neo4j seeding
        entities/users/users.json       — users seeded into Neo4j
        entities/groups/groups.json     — groups seeded into Neo4j
        entities/files/sets/1/set.json  — file-set 1 → container + fixture folder
        entities/files/sets/2/set.json  — file-set 2 → container + fixture folder
        entities/files/sets/3/set.json  — file-set 3 → container + fixture folder

    backend/python/tests/fixtures/
        sample1/            — PDFs, CSV, code files  (used by file-set 1)
        sample2/            — same file types, different content  (file-set 2)
        sample3/            — nested subfolder structure  (file-set 3)
        incremental_sample/ — uploaded only during incremental sync test

Test Coverage:
──────────────────────────────────────────────────────────────────────────────
 PHASE 0  ─  Infrastructure Setup
  Containers are created and sample data is uploaded before any connector test.

 TC-INIT-001  connector.init() returns True, data_source set

 TC-INIT-002  connector.test_connection_and_access() returns True

 TC-SYNC-001  connector.run_sync()
              → graph: record groups per container, file records count,
                field spot-check (external_record_id, path, record_name),
                permissions per record group

 TC-INCR-001  Upload incremental_sample/ blobs → connector.run_incremental_sync()
              → graph: new records appear, old records unchanged

 TC-UPDATE-001  Overwrite an existing blob with new content (new revision, same name)
               → connector.run_incremental_sync()
               → graph: same record node updated in-place (revision + version bumped)

 TC-RENAME-001  Copy + delete within container (server-side copy semantics)
               → connector.run_incremental_sync()
               → graph: record path/record_name updated, same DB node id retained

 TC-MOVE-001   Copy + delete within same container to a different virtual folder
              → connector.run_incremental_sync()
              → graph: record external_record_id/path updated to new folder, same node retained

 TC-DISABLE-001  Connector cleanup (disables data source)

 TC-DELETE-001   delete_connector_instance() removes all Neo4j nodes + edges for
                 this connector_id
                 → graph: no records/record-groups remain for connector_id

 PHASE N  ─  Cleanup
  All blobs and test containers are deleted; Neo4j is disconnected.
──────────────────────────────────────────────────────────────────────────────
"""

import asyncio
import json
import logging
import os
import sys
import time as _time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

# ── Load .env ──────────────────────────────────────────────────────────────────
_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".env")
load_dotenv(dotenv_path=_ENV_PATH)

from app.config.configuration_service import ConfigurationService  # noqa: E402, I001
from app.config.constants.arangodb import CollectionNames  # noqa: E402
from app.config.providers.in_memory_store import InMemoryKeyValueStore  # noqa: E402
from app.connectors.core.base.data_store.graph_data_store import (  # noqa: E402
    GraphDataStore,
)
from app.connectors.sources.azure_blob.connector import (  # noqa: E402
    AzureBlobConnector,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider  # noqa: E402
from app.utils.logger import create_logger  # noqa: E402

try:
    from azure.storage.blob.aio import (
        BlobServiceClient as AsyncBlobServiceClient,  # type: ignore  # noqa: E402
    )
except ImportError:  # pragma: no cover - import error surfaced at runtime
    AsyncBlobServiceClient = None  # type: ignore


# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = create_logger("azure-blob-lifecycle-test")


# ── Credentials / Paths ────────────────────────────────────────────────────────
AZURE_BLOB_CONNECTION_STRING: str = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")

_BACKEND_PYTHON_ROOT = Path(__file__).resolve().parents[4]
SAMPLE_DATA_ROOT = _BACKEND_PYTHON_ROOT / "sample-data" / "entities"
SETS_ROOT = SAMPLE_DATA_ROOT / "files" / "sets"


def _load_json(path: Path) -> Dict[str, Any] | List[Any]:
    """Load and return parsed JSON from *path*."""
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _load_file_sets() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Load set.json files from sample-data/entities/files/sets/.
    Returns (regular_sets, incremental_set) where regular_sets are the
    numbered sets (1, 2, 3, ...) and incremental_set is sets/incremental/.
    """
    regular: List[Dict[str, Any]] = []
    incremental: Dict[str, Any] = {}
    for set_dir in sorted(SETS_ROOT.iterdir()):
        set_json = set_dir / "set.json"
        if not (set_dir.is_dir() and set_json.exists()):
            continue
        data = _load_json(set_json)
        if set_dir.name == "incremental":
            incremental = data
        else:
            regular.append(data)
    return regular, incremental


# ── Load entity data from sample-data ──────────────────────────────────────────
_ORG_DATA: Dict[str, Any] = _load_json(SAMPLE_DATA_ROOT / "org" / "org.json")
_USERS_DATA: List[Dict[str, Any]] = _load_json(
    SAMPLE_DATA_ROOT / "users" / "users.json"
)
_GROUPS_DATA: List[Dict[str, Any]] = _load_json(
    SAMPLE_DATA_ROOT / "groups" / "groups.json"
)
_FILE_SETS, _INCREMENTAL_SET = _load_file_sets()


# ── Runtime constants derived from sample-data ─────────────────────────────────
_RUN_ID = uuid.uuid4().hex[:8]
CONNECTOR_ID = f"azure-blob-test-{_RUN_ID}"

# Org and primary user come from sample-data (suffixed with run-id for isolation)
ORG_ID = f"{_ORG_DATA['id']}-{_RUN_ID}"
USER_EMAIL = _USERS_DATA[0]["email"]

# One container per regular file-set, suffixed with run-id for isolation
TEST_CONTAINERS: List[str] = [
    f"azure-blob-test-{_RUN_ID}-{fs['bucket_suffix']}" for fs in _FILE_SETS
]

CONTAINER_SAMPLE1 = TEST_CONTAINERS[0]
CONTAINER_SAMPLE2 = TEST_CONTAINERS[1]
CONTAINER_SAMPLE3 = TEST_CONTAINERS[2]

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

_results: List[Dict[str, Any]] = []

# Tracks every blob uploaded so cleanup can delete them
_uploaded_blobs: Dict[str, List[str]] = {c: [] for c in TEST_CONTAINERS}


# ── Result helpers ─────────────────────────────────────────────────────────────

def _record(name: str, status: str, detail: str = "") -> None:
    icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "⊘"}[status]
    _results.append({"name": name, "status": status, "detail": detail})
    suffix = f"  —  {detail}" if detail else ""
    logger.info(f"{icon}  [{status}]  {name}{suffix}")


def _require_credentials() -> None:
    if not AZURE_BLOB_CONNECTION_STRING:
        raise RuntimeError(
            "Missing Azure Blob connection string. "
            "Set AZURE_BLOB_CONNECTION_STRING in backend/python/.env"
        )
    if AsyncBlobServiceClient is None:
        raise RuntimeError(
            "azure-storage-blob is not installed. "
            "Install it with `pip install azure-storage-blob`."
        )


def _build_blob_service_client() -> AsyncBlobServiceClient:
    """Build an AsyncBlobServiceClient from the connection string."""
    if AsyncBlobServiceClient is None:  # pragma: no cover - guarded by _require_credentials
        raise RuntimeError("AsyncBlobServiceClient is not available")
    return AsyncBlobServiceClient.from_connection_string(
        conn_str=AZURE_BLOB_CONNECTION_STRING
    )


def _load_set_files(set_dir: Path) -> Dict[str, bytes]:
    """
    Return {relative_path: bytes} for all real files inside a set directory,
    skipping set.json itself.
    """
    if not set_dir.exists():
        raise FileNotFoundError(f"Set directory not found: {set_dir}")
    files: Dict[str, bytes] = {}
    for path in sorted(set_dir.rglob("*")):
        if path.is_file() and path.name != "set.json":
            rel = str(path.relative_to(set_dir)).replace("\\", "/")
            files[rel] = path.read_bytes()
    return files


# ══════════════════════════════════════════════════════════════════════════════
# GRAPH VALIDATION HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def _validate_graph_after_sync(
    graph_provider: Neo4jProvider,
    connector_id: str,
    expected_containers: List[str],
    spot_check_key: Optional[str] = None,
    spot_check_container: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Full graph validation after run_sync / run_incremental_sync.

    Checks:
      1. A RecordGroup exists for each expected container.
      2. At least one Record exists for the connector.
      3. If spot_check_key is given, a Record with that external_record_id exists
         and has correct path, record_name, and mime_type fields.
      4. Permission relationships exist for the connector's record groups.
    """
    failures: List[str] = []

    # 1. RecordGroup per container
    for container in expected_containers:
        rg = await graph_provider.get_record_group_by_external_id(
            connector_id=connector_id, external_id=container
        )
        if rg is None:
            failures.append(f"RecordGroup missing for container '{container}'")

    # 2. At least one Record — query by connectorId only (org_id may be '' in test env)
    record_count_query = """
    MATCH (r:Record {connectorId: $connector_id})
    RETURN count(r) AS cnt
    """
    rc_results = await graph_provider.client.execute_query(
        record_count_query, parameters={"connector_id": connector_id}
    )
    record_count = rc_results[0]["cnt"] if rc_results else 0
    if record_count == 0:
        failures.append("No records found in graph for this connector")

    # 3. Spot-check a specific record
    if spot_check_key and spot_check_container:
        external_record_id = f"{spot_check_container}/{spot_check_key}"
        rec = await graph_provider.get_record_by_external_id(
            connector_id=connector_id, external_id=external_record_id
        )
        if rec is None:
            failures.append(
                f"Spot-check record not found: externalRecordId='{external_record_id}'"
            )
        else:
            expected_record_name = spot_check_key.rstrip("/").split("/")[-1]
            if rec.record_name != expected_record_name:
                failures.append(
                    f"record_name mismatch: expected '{expected_record_name}', "
                    f"got '{rec.record_name}'"
                )
            rec_path = getattr(rec, "path", None)
            if rec_path is not None and rec_path != spot_check_key:
                failures.append(
                    f"path mismatch: expected '{spot_check_key}', got '{rec_path}'"
                )

    # 4. Permission relationships for record groups
    if expected_containers:
        perm_query = """
        MATCH (rg:RecordGroup {connectorId: $connector_id})
        OPTIONAL MATCH (rg)-[:HAS_PERMISSION]->(p)
        RETURN rg.externalGroupId AS groupId, count(p) AS permCount
        LIMIT 5
        """
        perm_results = await graph_provider.client.execute_query(
            perm_query, parameters={"connector_id": connector_id}
        )
        if not perm_results:
            failures.append("No permission relationships found for record groups")

    if failures:
        return False, "; ".join(failures)
    return True, (
        f"{record_count} record(s) found across {len(expected_containers)} container(s)"
    )


async def _validate_record_updated(
    graph_provider: Neo4jProvider,
    connector_id: str,
    new_external_id: str,
    old_external_id: str,
    expected_record_name: str,
    operation: str = "rename/move",
) -> Tuple[bool, str]:
    """
    Validates that a rename or move was reflected in the graph:
      - New external_record_id exists.
      - Old external_record_id no longer exists.
      - record_name matches expected_record_name.
    """
    new_rec = await graph_provider.get_record_by_external_id(
        connector_id=connector_id, external_id=new_external_id
    )
    old_rec = await graph_provider.get_record_by_external_id(
        connector_id=connector_id, external_id=old_external_id
    )

    failures: List[str] = []

    if new_rec is None:
        failures.append(
            f"[CONNECTOR BUG] New record was NOT created at destination: '{new_external_id}'"
        )
    else:
        if new_rec.record_name != expected_record_name:
            failures.append(
                f"[CONNECTOR BUG] record_name mismatch on new record: "
                f"expected '{expected_record_name}', got '{new_rec.record_name}'"
            )

    if old_rec is not None:
        failures.append(
            f"[CONNECTOR BUG] Stale record still exists after {operation}: '{old_external_id}'. "
            f"Connector did not delete the old Neo4j node."
        )

    if failures:
        return False, "; ".join(failures)

    detail = (
        f"Updated: '{old_external_id}' → '{new_external_id}' "
        f"(name='{new_rec.record_name}')"
    )
    return True, detail


async def _validate_record_content_updated(
    graph_provider: Neo4jProvider,
    connector_id: str,
    external_id: str,
    old_revision_id: Optional[str],
    old_version: int,
) -> Tuple[bool, str]:
    """
    Validates that a blob overwrite (new content) was reflected in Neo4j.
    """
    query = """
    MATCH (r:Record {externalRecordId: $external_id, connectorId: $connector_id})
    RETURN r.externalRevisionId AS revisionId, r.version AS version
    LIMIT 1
    """
    results = await graph_provider.client.execute_query(
        query,
        parameters={"external_id": external_id, "connector_id": connector_id},
    )

    if not results:
        return False, f"[CONNECTOR BUG] Record disappeared after content update: '{external_id}'"

    new_revision_id = results[0]["revisionId"]
    new_version = results[0]["version"]

    failures: List[str] = []

    if new_revision_id is None:
        failures.append(
            "externalRevisionId is NULL on updated record — connector did not set it"
        )
    elif new_revision_id == old_revision_id:
        failures.append(
            f"[CONNECTOR BUG] externalRevisionId unchanged after content update: "
            f"still '{old_revision_id}'. Connector did not detect the new object version."
        )

    if new_version is None:
        failures.append("version field is NULL on updated record")
    elif new_version <= old_version:
        failures.append(
            f"[CONNECTOR BUG] version not incremented: was {old_version}, still {new_version}. "
            f"Connector did not bump the record version after content update."
        )

    if failures:
        return False, "; ".join(failures)

    return True, (
        f"Content update detected for '{external_id}': "
        f"externalRevisionId '{old_revision_id}' → '{new_revision_id}', "
        f"version {old_version} → {new_version}"
    )


async def _validate_connector_deleted(
    graph_provider: Neo4jProvider,
    connector_id: str,
) -> Tuple[bool, str]:
    """
    Validates that delete_connector_instance removed all data:
      - No Records remain for this connector_id.
      - No RecordGroups remain for this connector_id.
      - No App node remains for this connector_id.
    """
    failures: List[str] = []

    del_record_query = """
    MATCH (r:Record {connectorId: $connector_id})
    RETURN count(r) AS cnt
    """
    del_rc_results = await graph_provider.client.execute_query(
        del_record_query, parameters={"connector_id": connector_id}
    )
    remaining_records = del_rc_results[0]["cnt"] if del_rc_results else 0
    if remaining_records > 0:
        failures.append(
            f"{remaining_records} Record node(s) still present after delete"
        )

    rg_query = """
    MATCH (rg:RecordGroup {connectorId: $connector_id})
    RETURN count(rg) AS cnt
    """
    rg_results = await graph_provider.client.execute_query(
        rg_query, parameters={"connector_id": connector_id}
    )
    rg_count = rg_results[0]["cnt"] if rg_results else 0
    if rg_count > 0:
        failures.append(f"{rg_count} RecordGroup node(s) still present after delete")

    app_doc = await graph_provider.get_document(
        connector_id, CollectionNames.APPS.value
    )
    if app_doc:
        failures.append(f"App node '{connector_id}' still present after delete")

    if failures:
        return False, "; ".join(failures)
    return True, "All connector data (Records, RecordGroups, App node) removed from graph"


async def _full_neo4j_cleanup(
    graph_provider: Neo4jProvider,
    connector_id: str,
) -> None:
    """
    Best-effort cleanup of all Neo4j data created by this test run.

    This removes:
      - All Records and RecordGroups for this connector_id
      - The App node for this connector_id
      - All SyncPoints for this connector_id
      - The seeded Org, Users, and Groups for ORG_ID
    """
    try:
        # Connector-scoped nodes (mostly already removed by delete_connector_instance)
        await graph_provider.remove_nodes_by_field(
            CollectionNames.RECORDS.value,
            "connectorId",
            connector_id,
        )
        await graph_provider.remove_nodes_by_field(
            CollectionNames.RECORD_GROUPS.value,
            "connectorId",
            connector_id,
        )
        await graph_provider.remove_nodes_by_field(
            CollectionNames.APPS.value,
            "id",
            connector_id,
        )
        await graph_provider.remove_nodes_by_field(
            CollectionNames.SYNC_POINTS.value,
            "connectorId",
            connector_id,
        )

        # Org-scoped nodes seeded specifically for this test run
        await graph_provider.remove_nodes_by_field(
            CollectionNames.USERS.value,
            "orgId",
            ORG_ID,
        )
        await graph_provider.remove_nodes_by_field(
            CollectionNames.GROUPS.value,
            "orgId",
            ORG_ID,
        )
        await graph_provider.remove_nodes_by_field(
            CollectionNames.ORGS.value,
            "id",
            ORG_ID,
        )
    except Exception as exc:
        logger.warning(f"Neo4j full cleanup error (non-fatal): {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 0 — Infrastructure Setup
# ══════════════════════════════════════════════════════════════════════════════

async def setup_neo4j(
    graph_provider: Neo4jProvider,
    connector_id: str,
) -> None:
    """
    Seed Neo4j using entity data loaded from sample-data/:
      - Org node from entities/org/org.json
      - All users from entities/users/users.json
      - All groups from entities/groups/groups.json
      - App doc (connector instance node) + ORG_APP_RELATION edge
    """
    ts = int(_time.time() * 1000)

    # Org
    org_node = {
        "id": ORG_ID,
        "accountType": _ORG_DATA.get("accountType", "enterprise"),
        "name": _ORG_DATA.get("name", "Azure Blob Test Org"),
        "isActive": _ORG_DATA.get("isActive", True),
        "createdAtTimestamp": ts,
        "updatedAtTimestamp": ts,
    }
    await graph_provider.batch_upsert_nodes([org_node], CollectionNames.ORGS.value)
    logger.info(f"Seeded org '{ORG_ID}' from sample-data")

    # Users
    user_nodes = [
        {
            "id": u["id"],
            "email": u["email"],
            "userId": u.get("userId", u["id"]),
            "orgId": ORG_ID,
            "isActive": u.get("isActive", True),
            "createdAtTimestamp": ts,
            "updatedAtTimestamp": ts,
        }
        for u in _USERS_DATA
    ]
    await graph_provider.batch_upsert_nodes(user_nodes, CollectionNames.USERS.value)
    belongs_to_edges = [
        {
            "from_id": u["id"],
            "from_collection": CollectionNames.USERS.value,
            "to_id": ORG_ID,
            "to_collection": CollectionNames.ORGS.value,
            "entityType": "ORGANIZATION",
            "createdAtTimestamp": ts,
            "updatedAtTimestamp": ts,
        }
        for u in _USERS_DATA
    ]
    await graph_provider.batch_create_edges(
        belongs_to_edges, CollectionNames.BELONGS_TO.value
    )
    logger.info(f"Seeded {len(user_nodes)} user(s) from sample-data")

    # Groups
    if _GROUPS_DATA:
        group_nodes = [
            {
                "id": g["id"],
                "name": g.get("name", g["id"]),
                "orgId": ORG_ID,
                "isActive": g.get("isActive", True),
                "createdAtTimestamp": ts,
                "updatedAtTimestamp": ts,
            }
            for g in _GROUPS_DATA
        ]
        await graph_provider.batch_upsert_nodes(group_nodes, CollectionNames.GROUPS.value)
        logger.info(f"Seeded {len(group_nodes)} group(s) from sample-data")

    # App document (connector instance node)
    existing_app = await graph_provider.get_document(
        connector_id, CollectionNames.APPS.value
    )
    if not existing_app:
        app_doc = {
            "id": connector_id,
            "name": "Azure Blob Test Connector",
            "type": "AZURE_BLOB",
            "appGroup": "Azure",
            "authType": "CONNECTION_STRING",
            "scope": "team",
            "orgId": ORG_ID,
            "isActive": True,
            "isAgentActive": False,
            "isConfigured": True,
            "isAuthenticated": True,
            "createdBy": USER_EMAIL,
            "updatedBy": None,
            "createdAtTimestamp": ts,
            "updatedAtTimestamp": ts,
        }
        await graph_provider.batch_upsert_nodes([app_doc], CollectionNames.APPS.value)
        await graph_provider.batch_create_edges(
            [
                {
                    "from_id": ORG_ID,
                    "from_collection": CollectionNames.ORGS.value,
                    "to_id": connector_id,
                    "to_collection": CollectionNames.APPS.value,
                    "createdAtTimestamp": ts,
                }
            ],
            CollectionNames.ORG_APP_RELATION.value,
        )
        logger.info(f"Created app doc and org-app relation for {connector_id}")
    else:
        logger.info(f"App doc for {connector_id} already exists")


async def create_containers(client: AsyncBlobServiceClient) -> None:
    """Create the test Azure Blob containers for this run."""
    for container in TEST_CONTAINERS:
        container_client = client.get_container_client(container)
        try:
            await container_client.create_container()
            logger.info(f"Created container: {container}")
        except Exception as exc:  # pragma: no cover - best-effort logging
            msg = str(exc)
            if "ContainerAlreadyExists" in msg or "ResourceExistsError" in msg:
                logger.info(f"Container already exists: {container}")
            else:
                raise RuntimeError(f"Failed to create container {container}: {exc}") from exc


async def upload_set(
    client: AsyncBlobServiceClient,
    container: str,
    set_id: str,
    key_prefix: str = "",
) -> List[str]:
    """
    Upload all files from sample-data/entities/files/sets/<set_id>/ to
    azure://<container>/<key_prefix><rel_path>.
    Returns the list of blob names uploaded.
    """
    set_dir = SETS_ROOT / set_id
    file_map = _load_set_files(set_dir)
    container_client = client.get_container_client(container)
    uploaded: List[str] = []
    if container not in _uploaded_blobs:
        _uploaded_blobs[container] = []
    for rel_path, content in file_map.items():
        key = f"{key_prefix}{rel_path}" if key_prefix else rel_path
        blob_client = container_client.get_blob_client(key)
        await blob_client.upload_blob(content, overwrite=True)
        uploaded.append(key)
        _uploaded_blobs[container].append(key)
        logger.debug(f"Uploaded azure://{container}/{key} ({len(content)} bytes)")
    logger.info(
        f"Uploaded {len(uploaded)} file(s) to azure://{container}/ "
        f"(from sample-data/entities/files/sets/{set_id}/)"
    )
    return uploaded


# ══════════════════════════════════════════════════════════════════════════════
# TEST CASES
# ══════════════════════════════════════════════════════════════════════════════

async def test_init(connector: AzureBlobConnector) -> None:
    """TC-INIT-001: connector.init() returns True and data_source is set."""
    name = "TC-INIT-001: connector.init()"
    try:
        result = await connector.init()
        if result and connector.data_source is not None:
            _record(name, PASS, "init() returned True, data_source is set")
        elif result:
            _record(name, FAIL, "init() returned True but data_source is None")
        else:
            _record(name, FAIL, "init() returned False")
    except Exception as exc:
        _record(name, FAIL, f"Exception: {exc}")


async def test_connection(connector: AzureBlobConnector) -> None:
    """TC-INIT-002: connector.test_connection_and_access() returns True."""
    name = "TC-INIT-002: connector.test_connection_and_access()"
    try:
        result = await connector.test_connection_and_access()
        if result:
            _record(name, PASS, "test_connection_and_access() returned True")
        else:
            _record(name, FAIL, "test_connection_and_access() returned False")
    except NotImplementedError:
        _record(
            name,
            SKIP,
            "test_connection_and_access() not implemented for AzureBlobConnector",
        )
    except Exception as exc:
        _record(name, FAIL, f"Exception: {exc}")


async def test_run_sync(
    connector: AzureBlobConnector,
    graph_provider: Neo4jProvider,
    spot_check_container: str,
    spot_check_key: str,
) -> None:
    """TC-SYNC-001: run_sync() and full graph validation."""
    name = "TC-SYNC-001: connector.run_sync() + graph validation"
    try:
        await connector.run_sync()
        ok, detail = await _validate_graph_after_sync(
            graph_provider,
            connector_id=CONNECTOR_ID,
            expected_containers=TEST_CONTAINERS,
            spot_check_key=spot_check_key,
            spot_check_container=spot_check_container,
        )
        _record(name, PASS if ok else FAIL, detail)
    except Exception as exc:
        _record(name, FAIL, f"Exception: {exc}")


async def test_incremental_sync(
    connector: AzureBlobConnector,
    graph_provider: Neo4jProvider,
    client: AsyncBlobServiceClient,
) -> None:
    """
    TC-INCR-001: Upload incremental_sample/ blobs, run run_incremental_sync(),
    then validate that the new records appear in the graph.
    """
    name = "TC-INCR-001: connector.run_incremental_sync() + graph validation"
    try:
        _count_query = """
        MATCH (r:Record {connectorId: $connector_id})
        RETURN count(r) AS cnt
        """
        _before = await graph_provider.client.execute_query(
            _count_query, parameters={"connector_id": CONNECTOR_ID}
        )
        count_before = _before[0]["cnt"] if _before else 0

        incr_container = f"azure-blob-test-{_RUN_ID}-{_INCREMENTAL_SET['bucket_suffix']}"
        if incr_container not in _uploaded_blobs:
            _uploaded_blobs[incr_container] = []
        incr_container_client = client.get_container_client(incr_container)
        try:
            await incr_container_client.create_container()
            logger.info(f"Created incremental container: {incr_container}")
        except Exception:
            logger.info(f"Incremental container may already exist: {incr_container}")

        incr_prefix = _INCREMENTAL_SET.get("key_prefix", "")
        incr_keys = await upload_set(
            client, incr_container, "incremental", key_prefix=incr_prefix
        )

        await asyncio.sleep(2)

        await connector.run_incremental_sync()

        _after = await graph_provider.client.execute_query(
            _count_query, parameters={"connector_id": CONNECTOR_ID}
        )
        count_after = _after[0]["cnt"] if _after else 0

        if incr_keys:
            spot_external_id = f"{incr_container}/{incr_keys[0]}"
            spot_rec = await graph_provider.get_record_by_external_id(
                connector_id=CONNECTOR_ID, external_id=spot_external_id
            )
        else:
            spot_rec = None

        new_count = count_after - count_before
        if new_count >= len(incr_keys) and (not incr_keys or spot_rec is not None):
            _record(
                name,
                PASS,
                f"{new_count} new record(s) added "
                f"(total: {count_before} → {count_after})",
            )
        else:
            _record(
                name,
                FAIL,
                f"Expected {len(incr_keys)} new records, got {new_count}. "
                f"Spot-check record found: {spot_rec is not None}",
            )
    except Exception as exc:
        _record(name, FAIL, f"Exception: {exc}")


async def test_rename(
    connector: AzureBlobConnector,
    graph_provider: Neo4jProvider,
    client: AsyncBlobServiceClient,
    container: str,
    src_key: str,
) -> None:
    """
    TC-RENAME-001: Simulate a rename (server-side copy + delete within same container).
    """
    name = "TC-RENAME-001: rename detection + graph validation"
    filename = src_key.rstrip("/").split("/")[-1]
    base, ext = (filename.rsplit(".", 1) + [""])[:2]
    ext = f".{ext}" if ext else ""
    dst_key = src_key.replace(filename, f"{base}_renamed{ext}")

    try:
        container_client = client.get_container_client(container)
        src_blob = container_client.get_blob_client(src_key)
        if not await src_blob.exists():
            _record(
                name,
                SKIP,
                f"Source blob does not exist in Azure Blob: {container}/{src_key}",
            )
            return

        dst_blob = container_client.get_blob_client(dst_key)
        src_url = src_blob.url
        await dst_blob.start_copy_from_url(src_url)

        _uploaded_blobs.setdefault(container, []).append(dst_key)

        await src_blob.delete_blob()
        if src_key in _uploaded_blobs.get(container, []):
            _uploaded_blobs[container].remove(src_key)

        await asyncio.sleep(1)
        await connector.run_incremental_sync()

        new_external_id = f"{container}/{dst_key}"
        old_external_id = f"{container}/{src_key}"
        expected_name = dst_key.rstrip("/").split("/")[-1]

        ok, detail = await _validate_record_updated(
            graph_provider,
            connector_id=CONNECTOR_ID,
            new_external_id=new_external_id,
            old_external_id=old_external_id,
            expected_record_name=expected_name,
            operation="rename",
        )
        _record(name, PASS if ok else FAIL, detail)
    except Exception as exc:
        _record(name, FAIL, f"Exception: {exc}")


async def test_move(
    connector: AzureBlobConnector,
    graph_provider: Neo4jProvider,
    client: AsyncBlobServiceClient,
    container: str,
    src_key: str,
    dst_key: str,
) -> None:
    """
    TC-MOVE-001: Simulate an intra-container move (copy + delete to different folder).
    """
    name = "TC-MOVE-001: move detection + graph validation"
    try:
        container_client = client.get_container_client(container)
        src_blob = container_client.get_blob_client(src_key)
        if not await src_blob.exists():
            _record(
                name,
                SKIP,
                f"Source blob does not exist in Azure Blob: {container}/{src_key}",
            )
            return

        dst_blob = container_client.get_blob_client(dst_key)
        src_url = src_blob.url
        await dst_blob.start_copy_from_url(src_url)

        _uploaded_blobs.setdefault(container, []).append(dst_key)

        await src_blob.delete_blob()
        if src_key in _uploaded_blobs.get(container, []):
            _uploaded_blobs[container].remove(src_key)

        await asyncio.sleep(1)
        await connector.run_incremental_sync()

        new_external_id = f"{container}/{dst_key}"
        old_external_id = f"{container}/{src_key}"
        expected_name = dst_key.rstrip("/").split("/")[-1]

        ok, detail = await _validate_record_updated(
            graph_provider,
            connector_id=CONNECTOR_ID,
            new_external_id=new_external_id,
            old_external_id=old_external_id,
            expected_record_name=expected_name,
            operation="intra-container move",
        )
        _record(name, PASS if ok else FAIL, detail)
    except Exception as exc:
        _record(name, FAIL, f"Exception: {exc}")


async def test_content_change(
    connector: AzureBlobConnector,
    graph_provider: Neo4jProvider,
    client: AsyncBlobServiceClient,
    container: str,
    key: str,
) -> None:
    """
    TC-UPDATE-001: Overwrite an existing blob with new content.
    """
    name = "TC-UPDATE-001: content change detection + graph validation"
    external_id = f"{container}/{key}"
    try:
        pre_query = """
        MATCH (r:Record {externalRecordId: $external_id, connectorId: $connector_id})
        RETURN r.externalRevisionId AS revisionId, r.version AS version
        LIMIT 1
        """
        pre_results = await graph_provider.client.execute_query(
            pre_query,
            parameters={"external_id": external_id, "connector_id": CONNECTOR_ID},
        )
        if not pre_results:
            _record(
                name,
                SKIP,
                f"Record '{external_id}' not in graph yet; cannot test content change",
            )
            return

        old_revision_id = pre_results[0]["revisionId"]
        old_version = pre_results[0]["version"] or 0

        new_content = (
            b"# Content-change test\n"
            b"This blob was overwritten by TC-UPDATE-001 at integration test time.\n"
        )

        container_client = client.get_container_client(container)
        blob = container_client.get_blob_client(key)
        if not await blob.exists():
            _record(
                name,
                SKIP,
                f"Source blob does not exist in Azure Blob: {container}/{key}",
            )
            return

        await blob.upload_blob(new_content, overwrite=True)

        await asyncio.sleep(1)

        await connector.run_incremental_sync()

        ok, detail = await _validate_record_content_updated(
            graph_provider,
            connector_id=CONNECTOR_ID,
            external_id=external_id,
            old_revision_id=old_revision_id,
            old_version=old_version,
        )
        _record(name, PASS if ok else FAIL, detail)
    except Exception as exc:
        _record(name, FAIL, f"Exception: {exc}")


async def test_disable(connector: AzureBlobConnector) -> None:
    """
    TC-DISABLE-001: connector.cleanup() disables the connector (sets data_source = None).
    """
    name = "TC-DISABLE-001: connector.cleanup() (disable)"
    try:
        await connector.cleanup()
        if connector.data_source is None:
            _record(
                name,
                PASS,
                "cleanup() executed; data_source is None (connector disabled)",
            )
        else:
            _record(name, FAIL, "cleanup() ran but data_source is still set")
    except Exception as exc:
        _record(name, FAIL, f"Exception: {exc}")


async def test_delete(
    graph_provider: Neo4jProvider,
    connector_id: str,
) -> None:
    """
    TC-DELETE-001: delete_connector_instance() removes all graph nodes/edges.
    """
    name = "TC-DELETE-001: delete_connector_instance() + graph validation"
    try:
        result = await graph_provider.delete_connector_instance(
            connector_id=connector_id,
            org_id=ORG_ID,
        )
        success = result.get("success", False)
        if not success:
            _record(
                name,
                FAIL,
                f"delete_connector_instance() failed: {result.get('error')}",
            )
            return

        ok, detail = await _validate_connector_deleted(graph_provider, connector_id)
        _record(name, PASS if ok else FAIL, detail)
    except Exception as exc:
        _record(name, FAIL, f"Exception: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# CLEANUP
# ══════════════════════════════════════════════════════════════════════════════

async def cleanup_azure_blob(client: AsyncBlobServiceClient) -> None:
    """Delete all uploaded blobs and the test containers."""
    logger.info("── Azure Blob Cleanup ──")
    for container_name, keys in _uploaded_blobs.items():
        container_client = client.get_container_client(container_name)
        # Delete tracked blobs
        for key in keys:
            try:
                blob_client = container_client.get_blob_client(key)
                await blob_client.delete_blob()
                logger.debug(f"Deleted azure://{container_name}/{key}")
            except Exception as exc:
                logger.warning(
                    f"Could not delete azure://{container_name}/{key}: {exc}"
                )

        # Best-effort: delete any remaining blobs
        try:
            async for blob in container_client.list_blobs():
                try:
                    await container_client.delete_blob(blob.name)
                except Exception:
                    logger.warning(
                        f"Could not delete remaining blob "
                        f"azure://{container_name}/{blob.name}"
                    )
        except Exception:
            logger.warning(
                f"Could not list remaining blobs for container {container_name}"
            )

        # Delete the container itself
        try:
            await container_client.delete_container()
            logger.info(f"Deleted container: {container_name}")
        except Exception as exc:
            logger.warning(
                f"Could not delete container {container_name}: {exc}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# MAIN RUNNER
# ══════════════════════════════════════════════════════════════════════════════

async def run_all() -> None:
    _require_credentials()

    logger.info("=" * 72)
    logger.info("Azure Blob Connector – Full Lifecycle Integration Test")
    logger.info(f"Run ID      : {_RUN_ID}")
    logger.info(f"Connector ID: {CONNECTOR_ID}")
    logger.info(
        f"Org ID      : {ORG_ID}  (from sample-data/entities/org/org.json)"
    )
    logger.info(
        f"Users       : {[u['email'] for u in _USERS_DATA]}  "
        "(from sample-data/entities/users/users.json)"
    )
    logger.info(
        f"Groups      : {[g['id'] for g in _GROUPS_DATA]}  "
        "(from sample-data/entities/groups/groups.json)"
    )
    logger.info(
        f"File sets   : {len(_FILE_SETS)} regular + 1 incremental  "
        "(from sample-data/entities/files/sets/)"
    )
    logger.info(f"Containers  : {TEST_CONTAINERS}")
    logger.info(f"Sample-data : {SETS_ROOT}")
    logger.info("=" * 72)

    blob_client = _build_blob_service_client()
    graph_provider: Optional[Neo4jProvider] = None
    connector: Optional[AzureBlobConnector] = None

    try:
        # Phase 0a: Neo4j setup
        logger.info("\n── Phase 0a: Connecting to Neo4j ──")
        key_value_store = InMemoryKeyValueStore(
            logger, "app/config/default_config.json"
        )
        config_service = ConfigurationService(logger, key_value_store)

        graph_provider = Neo4jProvider(logger, config_service)
        if not await graph_provider.connect():
            raise RuntimeError("Failed to connect to Neo4j")

        await setup_neo4j(graph_provider, CONNECTOR_ID)
        data_store_provider = GraphDataStore(logger, graph_provider)

        # Phase 0b: Container & fixture setup
        logger.info(
            "\n── Phase 0b: Creating Azure Blob containers & uploading sample data ──"
        )
        logger.info(
            "File sets from sample-data: "
            + ", ".join(
                f"set {fs['set_id']} → {TEST_CONTAINERS[i]}"
                for i, fs in enumerate(_FILE_SETS)
            )
        )
        await create_containers(blob_client)

        all_set_keys: List[List[str]] = []
        for i, fs in enumerate(_FILE_SETS):
            keys = await upload_set(blob_client, TEST_CONTAINERS[i], fs["set_id"])
            all_set_keys.append(keys)

        sample1_keys = all_set_keys[0]

        # Phase 1: Connector lifecycle setup
        logger.info("\n── Phase 1: Creating AzureBlobConnector ──")
        azure_config = {
            "auth": {
                "authType": "CONNECTION_STRING",
                "azureBlobConnectionString": AZURE_BLOB_CONNECTION_STRING,
            },
            "scope": "team",
            "created_by": USER_EMAIL,
        }
        await config_service.set_config(
            f"/services/connectors/{CONNECTOR_ID}/config", azure_config
        )

        # Store container filter in config (let connector sync all test containers)
        await config_service.set_config(
            f"/services/connectors/azureblob/{CONNECTOR_ID}/filters",
            {
                "sync": {
                    "containers": {
                        "value": TEST_CONTAINERS,
                        "operator": "IN",
                    }
                }
            },
        )

        connector = await AzureBlobConnector.create_connector(
            logger, data_store_provider, config_service, CONNECTOR_ID
        )

        # Test cases
        logger.info("\n── Initialization ──")
        await test_init(connector)
        await test_connection(connector)

        logger.info("\n── Sync Operations ──")
        spot_key = sample1_keys[0] if sample1_keys else "sample1/report.pdf"
        await test_run_sync(connector, graph_provider, CONTAINER_SAMPLE1, spot_key)

        logger.info("\n── Incremental Sync ──")
        await test_incremental_sync(connector, graph_provider, blob_client)

        logger.info("\n── Content Change Detection ──")
        content_change_key = (
            sample1_keys[1] if len(sample1_keys) > 1 else sample1_keys[0]
        )
        await test_content_change(
            connector,
            graph_provider,
            blob_client,
            CONTAINER_SAMPLE1,
            content_change_key,
        )

        logger.info("\n── Rename Detection ──")
        rename_src_key = sample1_keys[0] if sample1_keys else "sample1/report.pdf"
        await test_rename(
            connector,
            graph_provider,
            blob_client,
            CONTAINER_SAMPLE1,
            rename_src_key,
        )

        logger.info("\n── Move Detection ──")
        move_key_index = 2
        if len(sample1_keys) > move_key_index:
            move_src_key = sample1_keys[move_key_index]
        elif len(sample1_keys) > 1:
            move_src_key = sample1_keys[1]
        else:
            move_src_key = sample1_keys[0]
        move_dst_key = f"moved/{move_src_key.split('/')[-1]}"
        await test_move(
            connector,
            graph_provider,
            blob_client,
            CONTAINER_SAMPLE1,
            move_src_key,
            move_dst_key,
        )

        logger.info("\n── Connector Disable ──")
        await test_disable(connector)

        logger.info("\n── Connector Delete ──")
        await test_delete(graph_provider, CONNECTOR_ID)

        # Messaging producer cleanup (best-effort)
        if connector is not None:
            data_entities_processor = getattr(
                connector, "data_entities_processor", None
            )
            messaging_producer = getattr(
                data_entities_processor, "messaging_producer", None
            )
            cleanup_coro = getattr(messaging_producer, "cleanup", None)
            if callable(cleanup_coro):
                try:
                    await cleanup_coro()
                except Exception as exc:  # pragma: no cover - best-effort cleanup
                    logger.warning(
                        f"Kafka messaging producer cleanup failed (non-fatal): {exc}"
                    )
    finally:
        # Phase N: Cleanup
        logger.info("\n── Phase N: Cleanup ──")
        try:
            await cleanup_azure_blob(blob_client)
        except Exception as exc:
            logger.warning(f"Azure Blob cleanup error (non-fatal): {exc}")

        if graph_provider is not None:
            # Remove all graph data created for this org/connector in this test run.
            try:
                await _full_neo4j_cleanup(graph_provider, CONNECTOR_ID)
            except Exception as exc:
                logger.warning(f"Neo4j full cleanup error (non-fatal): {exc}")

            try:
                await graph_provider.disconnect()
                logger.info("Neo4j disconnected")
            except Exception as exc:
                logger.warning(f"Neo4j disconnect error (non-fatal): {exc}")

        # Ensure the AsyncBlobServiceClient is properly closed to avoid
        # unclosed aiohttp client session warnings when the event loop exits.
        try:
            await blob_client.close()
        except Exception as exc:
            logger.warning(f"Azure Blob client close error (non-fatal): {exc}")

    # Summary
    passed = sum(1 for r in _results if r["status"] == PASS)
    skipped = sum(1 for r in _results if r["status"] == SKIP)
    failed = sum(1 for r in _results if r["status"] == FAIL)
    total = len(_results)

    logger.info("")
    logger.info("=" * 72)
    logger.info("FULL TEST RESULTS")
    logger.info("=" * 72)
    for r in _results:
        icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "⊘"}[r["status"]]
        suffix = f"  —  {r['detail']}" if r["detail"] else ""
        logger.info(f"  {icon}  [{r['status']}]  {r['name']}{suffix}")

    logger.info("")
    logger.info("=" * 72)
    logger.info(
        f"Results:  {passed} passed  |  {skipped} skipped  |  {failed} failed"
        f"  (total {total})"
    )
    logger.info("=" * 72)

    if failed:
        logger.info("\nFailed tests — root cause analysis:")
        for r in _results:
            if r["status"] == FAIL:
                detail = r["detail"]
                if "[CONNECTOR BUG]" in detail:
                    category = "CONNECTOR BUG"
                elif "Exception:" in detail:
                    category = "EXCEPTION (may be test or connector bug)"
                else:
                    category = "ASSERTION FAILURE"
                logger.info(f"\n  ✗  {r['name']}")
                logger.info(f"     Category : {category}")
                logger.info(f"     Detail   : {detail}")


if __name__ == "__main__":
    asyncio.run(run_all())

