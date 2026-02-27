"""
GCS Connector – Full Lifecycle Integration Test
================================================

Tests the complete GCSConnector lifecycle against real Google Cloud Storage and a
real Neo4j graph database, mirroring the S3 connector integration test.

Run from backend/python/ directory:
    source venv/bin/activate
    python -m app.connectors.sources.google_cloud_storage.gcs_integration_test

Required env vars (backend/python/.env):
    GCS_SERVICE_ACCOUNT_JSON_PATH  — Absolute or relative path to a local
                                     service account JSON key file.
    NEO4J_URI                      — Neo4j bolt URI (default: bolt://localhost:7687)
    NEO4J_USERNAME                 — Neo4j username (default: neo4j)
    NEO4J_PASSWORD                 — Neo4j password (required)
    NEO4J_DATABASE                 — Neo4j database name (default: neo4j)

Sample data is read from:
    backend/python/sample-data/
        entities/org/org.json           — org identity used for Neo4j seeding
        entities/users/users.json       — users seeded into Neo4j
        entities/groups/groups.json     — groups seeded into Neo4j
        entities/files/sets/1/set.json  — file-set 1 → bucket + fixture folder
        entities/files/sets/2/set.json  — file-set 2 → bucket + fixture folder
        entities/files/sets/3/set.json  — file-set 3 → bucket + fixture folder

    backend/python/tests/fixtures/
        sample1/            — PDFs, CSV, code files  (used by file-set 1)
        sample2/            — same file types, different content  (file-set 2)
        sample3/            — nested subfolder structure  (file-set 3)
        incremental_sample/ — uploaded only during incremental sync test
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

# ── Load .env ─────────────────────────────────────────────────────────────────
_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".env")
load_dotenv(dotenv_path=_ENV_PATH)

from app.config.configuration_service import ConfigurationService  # noqa: E402, I001
from app.config.constants.arangodb import CollectionNames  # noqa: E402
from app.config.providers.in_memory_store import InMemoryKeyValueStore  # noqa: E402
from app.connectors.core.base.data_store.graph_data_store import (  # noqa: E402
    GraphDataStore,
)
from app.connectors.sources.google_cloud_storage.connector import (  # noqa: E402
    GCSConnector,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider  # noqa: E402
from app.sources.client.gcs.gcs import (  # noqa: E402
    GCSClient,
    GCSServiceAccountConfig,
)
from app.sources.external.gcs.gcs import GCSDataSource  # noqa: E402
from app.utils.logger import create_logger  # noqa: E402

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = create_logger("gcs-lifecycle-test")

# ── Credentials / Paths ───────────────────────────────────────────────────────
# Path to the service account JSON key is read from backend/python/.env
SERVICE_ACCOUNT_JSON_PATH: str = os.getenv("GCS_SERVICE_ACCOUNT_JSON_PATH", "")

# ── Sample-data roots ─────────────────────────────────────────────────────────
_BACKEND_PYTHON_ROOT = Path(__file__).resolve().parents[4]
SAMPLE_DATA_ROOT = _BACKEND_PYTHON_ROOT / "sample-data" / "entities"
SETS_ROOT = SAMPLE_DATA_ROOT / "files" / "sets"

# ── Key-index constants ───────────────────────────────────────────────────────
_MOVE_KEY_INDEX = 2


def _load_json(path: Path) -> dict[str, Any] | list[Any]:
    """Load and return parsed JSON from *path*."""
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _load_file_sets() -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
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


# ── Load entity data from sample-data ─────────────────────────────────────────
_ORG_DATA: Dict[str, Any] = _load_json(SAMPLE_DATA_ROOT / "org" / "org.json")
_USERS_DATA: List[Dict[str, Any]] = _load_json(
    SAMPLE_DATA_ROOT / "users" / "users.json"
)
_GROUPS_DATA: List[Dict[str, Any]] = _load_json(
    SAMPLE_DATA_ROOT / "groups" / "groups.json"
)
_FILE_SETS, _INCREMENTAL_SET = _load_file_sets()

# ── Runtime constants derived from sample-data ────────────────────────────────
_RUN_ID = uuid.uuid4().hex[:8]
CONNECTOR_ID = f"gcs-test-{_RUN_ID}"

# Org and primary user come from sample-data (suffixed with run-id for isolation)
ORG_ID = f"{_ORG_DATA['id']}-{_RUN_ID}"
USER_EMAIL = _USERS_DATA[0]["email"]

# One bucket per regular file-set, suffixed with run-id for isolation
TEST_BUCKETS: List[str] = [
    f"gcs-test-{_RUN_ID}-{fs['bucket_suffix']}" for fs in _FILE_SETS
]

# Convenience aliases kept for backward compat with existing test functions
BUCKET_SAMPLE1 = TEST_BUCKETS[0]
BUCKET_SAMPLE2 = TEST_BUCKETS[1]
BUCKET_SAMPLE3 = TEST_BUCKETS[2]

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

_results: list[dict] = []

# Tracks every GCS key uploaded so cleanup can delete them
_uploaded_keys: Dict[str, List[str]] = {b: [] for b in TEST_BUCKETS}


# ── Result helpers ─────────────────────────────────────────────────────────────


def _record(name: str, status: str, detail: str = "") -> None:
    icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "⊘"}[status]
    _results.append({"name": name, "status": status, "detail": detail})
    suffix = f"  —  {detail}" if detail else ""
    logger.info(f"{icon}  [{status}]  {name}{suffix}")


def _require_credentials() -> dict[str, Any]:
    if not SERVICE_ACCOUNT_JSON_PATH:
        raise RuntimeError(
            "Missing GCS service account JSON path. "
            "Set GCS_SERVICE_ACCOUNT_JSON_PATH in backend/python/.env"
        )
    json_path = Path(SERVICE_ACCOUNT_JSON_PATH)
    if not json_path.is_absolute():
        json_path = _BACKEND_PYTHON_ROOT / json_path
    if not json_path.exists():
        raise RuntimeError(
            f"GCS service account JSON file not found at: {json_path}"
        )
    raw = json_path.read_text(encoding="utf-8")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"GCS service account JSON is not valid JSON: {exc}"
        ) from exc
    return {"raw": raw, "info": info, "path": str(json_path)}


def _ds(service_account_info: dict[str, Any]) -> GCSDataSource:
    """
    Build a GCSDataSource directly from service account info for test bucket
    setup and direct object operations.
    """
    config = GCSServiceAccountConfig(service_account_info=service_account_info)
    client = GCSClient.build_with_config(config)
    return GCSDataSource(client)


# ══════════════════════════════════════════════════════════════════════════════
# GRAPH VALIDATION HELPERS
# ══════════════════════════════════════════════════════════════════════════════


async def _validate_graph_after_sync(
    graph_provider: Neo4jProvider,
    connector_id: str,
    expected_buckets: List[str],
    spot_check_key: Optional[str] = None,
    spot_check_bucket: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Full graph validation after run_sync / run_incremental_sync.

    Checks:
      1. A RecordGroup exists for each expected bucket.
      2. At least one Record exists for the connector.
      3. If spot_check_key is given, a Record with that external_record_id exists
         and has correct path, record_name, and mime_type fields.
      4. Permission relationships exist for the connector's record groups.

    Returns (ok: bool, detail: str).
    """
    failures = []

    # 1. RecordGroup per bucket
    for bucket in expected_buckets:
        rg = await graph_provider.get_record_group_by_external_id(
            connector_id=connector_id, external_id=bucket
        )
        if rg is None:
            failures.append(f"RecordGroup missing for bucket '{bucket}'")

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
    if spot_check_key and spot_check_bucket:
        external_record_id = f"{spot_check_bucket}/{spot_check_key}"
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

    # 4. Permission relationships for the first record group
    if expected_buckets:
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
        f"{record_count} record(s) found across {len(expected_buckets)} bucket(s)"
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
      - Old external_record_id no longer exists (connector must delete stale node).
      - record_name matches expected_record_name.
    """
    new_rec = await graph_provider.get_record_by_external_id(
        connector_id=connector_id, external_id=new_external_id
    )
    old_rec = await graph_provider.get_record_by_external_id(
        connector_id=connector_id, external_id=old_external_id
    )

    failures = []

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
    Validates that a file overwrite (new content → new ETag) was reflected in Neo4j.
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

    failures = []

    if new_revision_id is None:
        failures.append("externalRevisionId is NULL on updated record — connector did not set it")
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
    failures = []

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
        "name": _ORG_DATA.get("name", "GCS Test Org"),
        "isActive": _ORG_DATA.get("isActive", True),
        "createdAtTimestamp": ts,
        "updatedAtTimestamp": ts,
    }
    await graph_provider.batch_upsert_nodes(
        [org_node], CollectionNames.ORGS.value
    )
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
    await graph_provider.batch_upsert_nodes(
        user_nodes, CollectionNames.USERS.value
    )
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
        await graph_provider.batch_upsert_nodes(
            group_nodes, CollectionNames.GROUPS.value
        )
        logger.info(f"Seeded {len(group_nodes)} group(s) from sample-data")

    # App document (connector instance node)
    existing_app = await graph_provider.get_document(
        connector_id, CollectionNames.APPS.value
    )
    if not existing_app:
        app_doc = {
            "id": connector_id,
            "name": "GCS Test Connector",
            "type": "GCS",
            "appGroup": "GCS",
            "authType": "SERVICE_ACCOUNT",
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
        await graph_provider.batch_upsert_nodes(
            [app_doc], CollectionNames.APPS.value
        )
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


async def create_buckets(ds: GCSDataSource) -> None:
    """Create the test GCS buckets for this run."""
    client = ds.get_storage_client()
    for bucket in TEST_BUCKETS:
        try:
            bucket_obj = client.bucket(bucket)
            client.create_bucket(bucket_obj)
            logger.info(f"Created bucket: {bucket}")
        except Exception as exc:  # pragma: no cover - best-effort logging
            # If the bucket already exists for some reason, treat as non-fatal.
            msg = str(exc)
            if "You already own this bucket" in msg or "Already exists" in msg:
                logger.info(f"Bucket already exists: {bucket}")
            else:
                raise RuntimeError(
                    f"Failed to create bucket {bucket}: {exc}"
                ) from exc


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


async def upload_set(
    ds: GCSDataSource,
    bucket: str,
    set_id: str,
    key_prefix: str = "",
) -> List[str]:
    """
    Upload all files from sample-data/entities/files/sets/<set_id>/ to
    gs://<bucket>/<key_prefix><rel_path>.
    Returns the list of object keys uploaded.
    """
    client = ds.get_storage_client()
    bucket_obj = client.bucket(bucket)
    set_dir = SETS_ROOT / set_id
    file_map = _load_set_files(set_dir)
    uploaded: List[str] = []
    for rel_path, content in file_map.items():
        key = f"{key_prefix}{rel_path}" if key_prefix else rel_path
        blob = bucket_obj.blob(key)
        blob.upload_from_string(content)
        uploaded.append(key)
        _uploaded_keys.setdefault(bucket, []).append(key)
        logger.debug(f"Uploaded gs://{bucket}/{key} ({len(content)} bytes)")
    logger.info(
        f"Uploaded {len(uploaded)} file(s) to gs://{bucket}/ "
        f"(from sample-data/entities/files/sets/{set_id}/)"
    )
    return uploaded


# ══════════════════════════════════════════════════════════════════════════════
# TEST CASES
# ══════════════════════════════════════════════════════════════════════════════


async def test_init(connector: GCSConnector) -> None:
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


async def test_connection(connector: GCSConnector) -> None:
    """TC-INIT-002: connector.test_connection_and_access() returns True."""
    name = "TC-INIT-002: connector.test_connection_and_access()"
    try:
        result = await connector.test_connection_and_access()
        if result:
            _record(name, PASS, "test_connection_and_access() returned True")
        else:
            _record(name, FAIL, "test_connection_and_access() returned False")
    except NotImplementedError:
        _record(name, SKIP, "test_connection_and_access() not implemented for GCSConnector")
    except Exception as exc:
        _record(name, FAIL, f"Exception: {exc}")


async def test_run_sync(
    connector: GCSConnector,
    graph_provider: Neo4jProvider,
    spot_check_bucket: str,
    spot_check_key: str,
) -> None:
    """TC-SYNC-001: run_sync() and full graph validation."""
    name = "TC-SYNC-001: connector.run_sync() + graph validation"
    try:
        await connector.run_sync()
        ok, detail = await _validate_graph_after_sync(
            graph_provider,
            connector_id=CONNECTOR_ID,
            expected_buckets=TEST_BUCKETS,
            spot_check_key=spot_check_key,
            spot_check_bucket=spot_check_bucket,
        )
        _record(name, PASS if ok else FAIL, detail)
    except Exception as exc:
        _record(name, FAIL, f"Exception: {exc}")


async def test_incremental_sync(
    connector: GCSConnector,
    graph_provider: Neo4jProvider,
    ds: GCSDataSource,
) -> None:
    """
    TC-INCR-001: Upload incremental_sample/ files, run run_incremental_sync(),
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

        incr_bucket = f"gcs-test-{_RUN_ID}-{_INCREMENTAL_SET['bucket_suffix']}"
        if incr_bucket not in _uploaded_keys:
            _uploaded_keys[incr_bucket] = []
        client = ds.get_storage_client()
        bucket_obj = client.bucket(incr_bucket)
        try:
            client.create_bucket(bucket_obj)
            logger.info(f"Created incremental bucket: {incr_bucket}")
        except Exception:
            logger.info(f"Incremental bucket may already exist: {incr_bucket}")

        incr_prefix = _INCREMENTAL_SET.get("key_prefix", "")
        incr_keys = await upload_set(
            ds, incr_bucket, "incremental", key_prefix=incr_prefix
        )

        await asyncio.sleep(2)

        await connector.run_incremental_sync()

        _after = await graph_provider.client.execute_query(
            _count_query, parameters={"connector_id": CONNECTOR_ID}
        )
        count_after = _after[0]["cnt"] if _after else 0

        if incr_keys:
            spot_external_id = f"{incr_bucket}/{incr_keys[0]}"
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
    connector: GCSConnector,
    graph_provider: Neo4jProvider,
    ds: GCSDataSource,
    src_bucket: str,
    src_key: str,
) -> None:
    """
    TC-RENAME-001: Simulate a rename (copy + delete within same bucket).
    """
    name = "TC-RENAME-001: rename detection + graph validation"
    filename = src_key.rstrip("/").split("/")[-1]
    base, ext = (filename.rsplit(".", 1) + [""])[:2]
    ext = f".{ext}" if ext else ""
    dst_key = src_key.replace(filename, f"{base}_renamed{ext}")

    try:
        client = ds.get_storage_client()
        bucket = client.bucket(src_bucket)
        src_blob = bucket.blob(src_key)
        if not src_blob.exists():
            _record(
                name,
                SKIP,
                f"Source object does not exist in GCS: gs://{src_bucket}/{src_key}",
            )
            return

        new_blob = bucket.blob(dst_key)
        token, bytes_rewritten, total_bytes = new_blob.rewrite(src_blob)
        while token is not None and bytes_rewritten < total_bytes:
            token, bytes_rewritten, total_bytes = new_blob.rewrite(src_blob, token=token)

        _uploaded_keys.setdefault(src_bucket, []).append(dst_key)

        src_blob.delete()
        if src_key in _uploaded_keys.get(src_bucket, []):
            _uploaded_keys[src_bucket].remove(src_key)

        await asyncio.sleep(1)
        await connector.run_incremental_sync()

        new_external_id = f"{src_bucket}/{dst_key}"
        old_external_id = f"{src_bucket}/{src_key}"
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
    connector: GCSConnector,
    graph_provider: Neo4jProvider,
    ds: GCSDataSource,
    bucket: str,
    src_key: str,
    dst_key: str,
) -> None:
    """
    TC-MOVE-001: Simulate an intra-bucket move (copy + delete to different folder).
    """
    name = "TC-MOVE-001: move detection + graph validation"
    try:
        client = ds.get_storage_client()
        bucket_obj = client.bucket(bucket)
        src_blob = bucket_obj.blob(src_key)
        if not src_blob.exists():
            _record(
                name,
                SKIP,
                f"Source object does not exist in GCS: gs://{bucket}/{src_key}",
            )
            return

        dst_blob = bucket_obj.blob(dst_key)
        token, bytes_rewritten, total_bytes = dst_blob.rewrite(src_blob)
        while token is not None and bytes_rewritten < total_bytes:
            token, bytes_rewritten, total_bytes = dst_blob.rewrite(src_blob, token=token)

        _uploaded_keys.setdefault(bucket, []).append(dst_key)

        src_blob.delete()
        if src_key in _uploaded_keys.get(bucket, []):
            _uploaded_keys[bucket].remove(src_key)

        await asyncio.sleep(1)
        await connector.run_incremental_sync()

        new_external_id = f"{bucket}/{dst_key}"
        old_external_id = f"{bucket}/{src_key}"
        expected_name = dst_key.rstrip("/").split("/")[-1]

        ok, detail = await _validate_record_updated(
            graph_provider,
            connector_id=CONNECTOR_ID,
            new_external_id=new_external_id,
            old_external_id=old_external_id,
            expected_record_name=expected_name,
            operation="intra-bucket move",
        )
        _record(name, PASS if ok else FAIL, detail)
    except Exception as exc:
        _record(name, FAIL, f"Exception: {exc}")


async def test_content_change(
    connector: GCSConnector,
    graph_provider: Neo4jProvider,
    ds: GCSDataSource,
    bucket: str,
    key: str,
) -> None:
    """
    TC-UPDATE-001: Overwrite an existing file with new content.
    """
    name = "TC-UPDATE-001: content change detection + graph validation"
    external_id = f"{bucket}/{key}"
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
            b"This file was overwritten by TC-UPDATE-001 at integration test time.\n"
        )

        client = ds.get_storage_client()
        bucket_obj = client.bucket(bucket)
        blob = bucket_obj.blob(key)
        if not blob.exists():
            _record(
                name,
                SKIP,
                f"Source object does not exist in GCS: gs://{bucket}/{key}",
            )
            return

        blob.upload_from_string(new_content)

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


async def test_disable(connector: GCSConnector) -> None:
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
            _record(
                name,
                FAIL,
                "cleanup() ran but data_source is still set",
            )
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


async def cleanup_gcs(ds: GCSDataSource) -> None:
    """Delete all uploaded objects and the test buckets."""
    logger.info("── GCS Cleanup ──")
    client = ds.get_storage_client()
    for bucket_name, keys in _uploaded_keys.items():
        bucket = client.bucket(bucket_name)
        for key in keys:
            try:
                blob = bucket.blob(key)
                blob.delete()
                logger.debug(f"Deleted gs://{bucket_name}/{key}")
            except Exception as exc:
                logger.warning(
                    f"Could not delete gs://{bucket_name}/{key}: {exc}"
                )

        try:
            # Ensure any remaining objects are removed
            blobs_iter = client.list_blobs(bucket_name)
            for blob in blobs_iter:
                try:
                    blob.delete()
                except Exception:
                    logger.warning(
                        f"Could not delete remaining blob "
                        f"gs://{bucket_name}/{blob.name}"
                    )
        except Exception:
            logger.warning(
                f"Could not list remaining blobs for bucket {bucket_name}"
            )

        try:
            bucket.delete()
            logger.info(f"Deleted bucket: {bucket_name}")
        except Exception as exc:
            logger.warning(
                f"Could not delete bucket {bucket_name}: {exc}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# MAIN RUNNER
# ══════════════════════════════════════════════════════════════════════════════


async def run_all() -> None:
    creds = _require_credentials()
    raw_sa_json = creds["raw"]
    sa_info = creds["info"]

    logger.info("=" * 72)
    logger.info("GCS Connector – Full Lifecycle Integration Test")
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
    logger.info(f"Buckets     : {TEST_BUCKETS}")
    logger.info(f"Sample-data : {SETS_ROOT}")
    logger.info("=" * 72)

    ds = _ds(sa_info)
    graph_provider: Optional[Neo4jProvider] = None
    connector: Optional[GCSConnector] = None

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

        # Phase 0b: GCS bucket & fixture setup
        logger.info(
            "\n── Phase 0b: Creating GCS buckets & uploading sample data ──"
        )
        logger.info(
            "File sets from sample-data: "
            + ", ".join(
                f"set {fs['set_id']} → {TEST_BUCKETS[i]}"
                for i, fs in enumerate(_FILE_SETS)
            )
        )
        await create_buckets(ds)

        all_set_keys: List[List[str]] = []
        for i, fs in enumerate(_FILE_SETS):
            keys = await upload_set(ds, TEST_BUCKETS[i], fs["set_id"])
            all_set_keys.append(keys)

        sample1_keys = all_set_keys[0]

        # Phase 1: Connector lifecycle setup
        logger.info("\n── Phase 1: Creating GCSConnector ──")
        gcs_config = {
            "auth": {
                "authType": "ACCESS_KEY",
                "serviceAccountJson": raw_sa_json,
            },
            "scope": "team",
            "created_by": USER_EMAIL,
        }
        await config_service.set_config(
            f"/services/connectors/{CONNECTOR_ID}/config", gcs_config
        )

        # Store bucket filter in config (let connector sync all three test buckets)
        await config_service.set_config(
            f"/services/connectors/gcs/{CONNECTOR_ID}/filters",
            {
                "sync": {
                    "buckets": {
                        "value": TEST_BUCKETS,
                        "operator": "IN",
                    }
                }
            },
        )

        connector = await GCSConnector.create_connector(
            logger, data_store_provider, config_service, CONNECTOR_ID
        )

        # Test cases
        logger.info("\n── Initialization ──")
        await test_init(connector)
        await test_connection(connector)

        logger.info("\n── Sync Operations ──")
        spot_key = sample1_keys[0] if sample1_keys else "report.pdf"
        await test_run_sync(connector, graph_provider, BUCKET_SAMPLE1, spot_key)

        logger.info("\n── Incremental Sync ──")
        await test_incremental_sync(connector, graph_provider, ds)

        logger.info("\n── Content Change Detection ──")
        content_change_key = (
            sample1_keys[1] if len(sample1_keys) > 1 else sample1_keys[0]
        )
        await test_content_change(
            connector, graph_provider, ds, BUCKET_SAMPLE1, content_change_key
        )

        logger.info("\n── Rename Detection ──")
        rename_src_key = sample1_keys[0] if sample1_keys else "report.pdf"
        await test_rename(
            connector, graph_provider, ds, BUCKET_SAMPLE1, rename_src_key
        )

        logger.info("\n── Move Detection ──")
        if len(sample1_keys) > _MOVE_KEY_INDEX:
            move_src_key = sample1_keys[_MOVE_KEY_INDEX]
        elif len(sample1_keys) > 1:
            move_src_key = sample1_keys[1]
        else:
            move_src_key = "data.xlsx"
        move_dst_key = f"moved/{move_src_key.split('/')[-1]}"
        await test_move(
            connector,
            graph_provider,
            ds,
            bucket=BUCKET_SAMPLE1,
            src_key=move_src_key,
            dst_key=move_dst_key,
        )

        logger.info("\n── Connector Disable ──")
        await test_disable(connector)

        logger.info("\n── Connector Delete ──")
        await test_delete(graph_provider, CONNECTOR_ID)

        # ── Messaging producer cleanup ────────────────────────────────────────
        # The DataSourceEntitiesProcessor lazily initializes a Kafka-based
        # messaging producer (AIOKafkaProducer under the hood). Explicitly
        # clean it up here to avoid asyncio "Unclosed AIOKafkaProducer"
        # warnings at shutdown.
        if connector is not None:
            data_entities_processor = getattr(connector, "data_entities_processor", None)
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
            await cleanup_gcs(ds)
        except Exception as exc:
            logger.warning(f"GCS cleanup error (non-fatal): {exc}")

        if graph_provider is not None:
            try:
                await graph_provider.disconnect()
                logger.info("Neo4j disconnected")
            except Exception as exc:
                logger.warning(f"Neo4j disconnect error (non-fatal): {exc}")

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

