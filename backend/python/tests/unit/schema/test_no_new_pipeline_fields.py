"""The stage-state schema relaxation is exactly the forward-compatible step, and adds no field.

Every stored-schema change in the indexing programme is expand-then-contract across two releases: this release
accepts what a later one writes, and writes nothing new itself. So the stage-state schema may differ from the
previous release only in what makes it accept unknown properties and values, and the record schema stays closed.
"""

import copy
import hashlib
import json

from app.config.constants.arangodb import CollectionNames
from app.schema.arango.documents import (
    PROGRESS_STATUS_VALUES,
    STAGE_PRIORITIES,
    STAGE_TIERS,
    record_schema,
    stage_state_schema,
)
from app.schema.node_schema_registry import NODE_SCHEMA_REGISTRY, adapt_schema

#: sha256 of the canonical JSON of the stage-state schema as the previous release defined it.
PREVIOUS_STAGE_STATE_SCHEMA_SHA256 = "cf32c610803bcada4347487fc27402fcf6fcd555378e2f2b2d3e30dc8854dfcd"

STAGE_STATE_PROPERTIES = frozenset({
    "attempt", "blocksDigest", "connectorId", "fingerprint", "finishedAtMs", "force", "hasImages", "hasTables",
    "mimeType", "orgId", "output", "priority", "publishedAtMs", "reason", "recordIds", "rev", "stage",
    "stageVersion", "startedAtMs", "status", "textChars", "textDigest", "tier", "trigger", "updatedAtMs",
    "virtualRecordId", "workerId",
})

#: The record schema's property names as the previous release defined them.
RECORD_PROPERTIES = frozenset({
    "accessRule", "connectorId", "connectorName", "contentRev", "createdAtTimestamp", "definition",
    "deleteBatchId", "deleteSource", "deletedAtTimestamp", "deletedByUserId", "duplicateReconcileAttempts",
    "duplicateReconcileDueAt", "duplicateReconcilePending", "embedDeliveryId", "embedDigest", "embedDone",
    "embedKey", "embedRev", "embedSeconds", "embedStartedMs", "embedTotal", "embedUncounted", "embedVrid",
    "entityExtractionStatus", "entityExtractionStrategy", "entityExtractorVersion", "externalGroupId",
    "externalParentId", "externalRecordId", "externalRevisionId", "externalRootGroupId", "extractionDeferred",
    "extractionStatus", "hasIndexedCopy", "hideWeburl", "indexingNote", "indexingStatus", "isArchived", "isDeleted",
    "isDependentNode", "isDirty", "isInternal", "isLatestVersion", "isPlaceholder", "isShared",
    "isVLMOcrProcessed", "lastEntityExtractionTimestamp", "lastExtractionTimestamp", "lastIndexTimestamp",
    "lastRepublishedAt", "lastSyncTimestamp", "md5Checksum", "mimeType", "orgId", "origin", "parentNodeId",
    "parseNowAt", "parseNowReason", "parseNowRev", "parseNowStatus", "parsingStatus", "previewRenderable",
    "processingStartedAt", "propsGen", "propsSet", "propsSrc", "propsVer", "purgeAttempts", "purgeLastError",
    "pushOrigin", "queuedAtTimestamp", "rateLimitedSince", "reason", "recordGroupId", "recordName",
    "recordProps", "recordType", "republishCount", "restoredAtTimestamp", "retryNotBefore",
    "rootRecordGroupId", "rowCount", "secretsRedacted", "sizeInBytes", "sourceCreatedAtTimestamp",
    "sourceLastModifiedTimestamp", "sourceTables", "sourceTimestampOrigin", "storageDocumentId",
    "summaryDocumentId", "trashedExternalRecordId", "updatedAtTimestamp", "uploadSourceCreatedAtTimestamp",
    "uploadSourceLastModifiedTimestamp", "uploadSourceTimestampOrigin", "uploadedBy", "version",
    "virtualRecordId", "webUrl",
    # Record processing diagnostics: facts about how a record's processing went, not pipeline state. This release
    # only accepts them and writes none; the schema carries them first so a process on the older schema never
    # meets a key it rejects once a later release writes them.
    "failedAt", "firstQueuedAt", "indexingAttempts", "pageCount",
})


def _digest(schema: object) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()


def _previous_release_schema() -> dict:
    """The current stage-state schema with the relaxation undone, and nothing else."""
    schema = copy.deepcopy(stage_state_schema)
    rule = schema["rule"]
    rule["additionalProperties"] = False
    rule["properties"]["status"] = {"type": "string", "enum": PROGRESS_STATUS_VALUES}
    rule["properties"]["tier"] = {"type": "string", "enum": STAGE_TIERS}
    rule["properties"]["priority"] = {"type": "string", "enum": STAGE_PRIORITIES}
    return schema


def test_only_the_stage_relaxation() -> None:
    assert _digest(_previous_release_schema()) == PREVIOUS_STAGE_STATE_SCHEMA_SHA256
    rule = stage_state_schema["rule"]
    assert rule["additionalProperties"] is True
    assert stage_state_schema["level"] == "strict"
    for field in ("status", "tier", "priority"):
        assert rule["properties"][field] == {"type": "string"}
    # Required fields stay required and typed.
    assert "status" in rule["required"]
    assert rule["properties"]["attempt"] == {"type": "integer", "minimum": 0}


def test_record_and_stage_state_schemas_unchanged() -> None:
    # No new stage-state property (a counter or a lease belongs in the KV store, not here).
    assert set(stage_state_schema["rule"]["properties"]) == STAGE_STATE_PROPERTIES
    assert NODE_SCHEMA_REGISTRY[CollectionNames.STAGE_STATES.value]["additionalProperties"] is True
    assert NODE_SCHEMA_REGISTRY[CollectionNames.STAGE_STATES.value] == adapt_schema(stage_state_schema)


def test_record_schema_properties_pinned() -> None:
    """Record nodes stay closed and keep their property set: no pipeline field goes on the record."""
    rule = record_schema["rule"]
    assert rule["additionalProperties"] is False
    assert NODE_SCHEMA_REGISTRY[CollectionNames.RECORDS.value]["additionalProperties"] is False
    added, removed = set(rule["properties"]) - RECORD_PROPERTIES, RECORD_PROPERTIES - set(rule["properties"])
    assert not added and not removed, (
        f"record_schema changed: added {sorted(added)}, removed {sorted(removed)}. A stored-schema change is "
        "expand-then-contract across two releases: the release that adds a field only accepts it and "
        "writes nothing; the next one writes it. Pipeline state (counters, leases, delivery ids) never goes on "
        "the record node: it lives in the stage-state collection or the KV store. If this is a legitimate "
        "non-pipeline record field, add it to RECORD_PROPERTIES in this file in the same PR, so a reviewer "
        "sees the schema change."
    )
