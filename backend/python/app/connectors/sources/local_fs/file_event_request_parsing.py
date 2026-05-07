import json

from fastapi import HTTPException, Request
from pydantic import ValidationError

from app.config.constants.arangodb import CollectionNames
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.local_fs.models import LocalFsFileEventBatchRequest
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.logger import create_logger
from app.utils.time_conversion import get_epoch_timestamp_in_ms

logger = create_logger("connector_service")

# Hard cap on the number of file events accepted in a single replay/journal
# batch. The connector still chunks internally, but capping at the edge keeps a
# single signed-in caller from forcing the connector into a multi-megabyte
# DB/Kafka loop while it stays in SYNCING.
LOCAL_FS_MAX_EVENTS_PER_BATCH = 5_000

# Per-file ceiling on multipart uploaded content. Matches the Node.js
# storage route's `maxFileSize: 100 MB`; exceeding it inside the upload
# helper would 4xx anyway, so reject up front before buffering the bytes.
LOCAL_FS_MAX_UPLOADED_FILE_BYTES = 100 * 1024 * 1024
# Aggregate ceiling on a single uploaded batch. With 5 000 events × the
# per-file cap the worst case is still 500 GB; cap aggregate to keep one
# signed-in caller from OOM'ing the connectors-service.
LOCAL_FS_MAX_UPLOADED_BATCH_BYTES = 1024 * 1024 * 1024  # 1 GB


def _unwrap_local_fs_file_event_payload(raw_payload: object) -> object:
    """Unwrap nested Local FS payload envelopes into the actual event payload."""
    candidate = raw_payload

    for _ in range(3):
        if isinstance(candidate, str):
            stripped = candidate.strip()
            if not stripped:
                return candidate
            try:
                candidate = json.loads(stripped)
                continue
            except json.JSONDecodeError:
                return candidate

        if not isinstance(candidate, dict):
            return candidate

        nested = (
            candidate.get("body")
            if candidate.get("body") is not None
            else candidate.get("payload")
            if candidate.get("payload") is not None
            else candidate.get("data")
        )
        if nested is None:
            return candidate
        candidate = nested

    return candidate


async def _parse_local_fs_file_event_batch_request(
    request: Request,
) -> LocalFsFileEventBatchRequest:
    """Parse and validate JSON Local FS file-event batches from request body."""
    raw_body = await request.body()
    if not raw_body:
        raise HTTPException(
            status_code=HttpStatusCode.UNPROCESSABLE_ENTITY.value,
            detail="Request body is required",
        )

    try:
        raw_payload: object = json.loads(raw_body)
    except json.JSONDecodeError:
        raw_payload = raw_body.decode("utf-8", errors="replace")

    payload = _unwrap_local_fs_file_event_payload(raw_payload)
    now = get_epoch_timestamp_in_ms()

    if isinstance(payload, list):
        payload = {
            "batchId": f"localfs-replay-{now}",
            "events": payload,
            "timestamp": now,
        }
    elif isinstance(payload, dict) and isinstance(payload.get("events"), list):
        batch_id = payload.get("batchId")
        timestamp = payload.get("timestamp")
        payload = {
            "batchId": batch_id
            if batch_id is not None
            else f"localfs-replay-{now}",
            "events": payload.get("events"),
            "timestamp": timestamp if timestamp is not None else now,
            "resetBeforeApply": payload.get("resetBeforeApply", False),
        }

    if (
        isinstance(payload, dict)
        and isinstance(payload.get("events"), list)
        and len(payload["events"]) > LOCAL_FS_MAX_EVENTS_PER_BATCH
    ):
        raise HTTPException(
            status_code=HttpStatusCode.PAYLOAD_TOO_LARGE.value,
            detail=(
                f"Local FS file event batch exceeds maximum size "
                f"({LOCAL_FS_MAX_EVENTS_PER_BATCH} events); "
                f"received {len(payload['events'])}"
            ),
        )

    try:
        return LocalFsFileEventBatchRequest.model_validate(payload)
    except ValidationError as exc:
        logger.warning(
            "Invalid Local FS file event batch payload",
            extra={"errors": exc.errors()},
        )
        raise HTTPException(
            status_code=HttpStatusCode.UNPROCESSABLE_ENTITY.value,
            detail={
                "message": "Invalid Local FS file event batch payload",
                "errors": exc.errors(),
            },
        ) from exc


async def _parse_local_fs_uploaded_file_event_batch_request(
    request: Request,
) -> tuple[LocalFsFileEventBatchRequest, dict[str, bytes]]:
    """Parse and validate multipart Local FS event manifests and uploaded files."""
    form = await request.form()
    raw_manifest = form.get("manifest")
    if raw_manifest is None:
        raise HTTPException(
            status_code=HttpStatusCode.UNPROCESSABLE_ENTITY.value,
            detail="Multipart field 'manifest' is required",
        )

    if hasattr(raw_manifest, "read"):
        manifest_bytes = await raw_manifest.read()
        manifest_text = manifest_bytes.decode("utf-8", errors="replace")
    else:
        manifest_text = str(raw_manifest)

    try:
        raw_payload: object = json.loads(manifest_text)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=HttpStatusCode.UNPROCESSABLE_ENTITY.value,
            detail="Multipart manifest must be valid JSON",
        ) from exc

    payload = _unwrap_local_fs_file_event_payload(raw_payload)
    now = get_epoch_timestamp_in_ms()
    if isinstance(payload, list):
        payload = {
            "batchId": f"localfs-upload-{now}",
            "events": payload,
            "timestamp": now,
        }
    elif isinstance(payload, dict) and isinstance(payload.get("events"), list):
        batch_id = payload.get("batchId")
        timestamp = payload.get("timestamp")
        payload = {
            "batchId": batch_id
            if batch_id is not None
            else f"localfs-upload-{now}",
            "events": payload.get("events"),
            "timestamp": timestamp if timestamp is not None else now,
            "resetBeforeApply": payload.get("resetBeforeApply", False),
        }

    if (
        isinstance(payload, dict)
        and isinstance(payload.get("events"), list)
        and len(payload["events"]) > LOCAL_FS_MAX_EVENTS_PER_BATCH
    ):
        raise HTTPException(
            status_code=HttpStatusCode.PAYLOAD_TOO_LARGE.value,
            detail=(
                f"Local FS file event batch exceeds maximum size "
                f"({LOCAL_FS_MAX_EVENTS_PER_BATCH} events); "
                f"received {len(payload['events'])}"
            ),
        )

    try:
        parsed = LocalFsFileEventBatchRequest.model_validate(payload)
    except ValidationError as exc:
        logger.warning(
            "Invalid Local FS uploaded file event batch manifest",
            extra={"errors": exc.errors()},
        )
        raise HTTPException(
            status_code=HttpStatusCode.UNPROCESSABLE_ENTITY.value,
            detail={
                "message": "Invalid Local FS uploaded file event batch manifest",
                "errors": exc.errors(),
            },
        ) from exc

    files_by_field: dict[str, bytes] = {}
    aggregate_bytes = 0
    for key, value in form.multi_items():
        if key == "manifest" or not hasattr(value, "read"):
            continue
        data = await value.read()
        # Reject pathological uploads at the edge — buffering them in
        # files_by_field exposes the connectors-service to OOM. The per-file
        # cap mirrors storage.routes.ts:303; the aggregate cap protects us
        # from one batch with many medium files.
        if len(data) > LOCAL_FS_MAX_UPLOADED_FILE_BYTES:
            raise HTTPException(
                status_code=HttpStatusCode.PAYLOAD_TOO_LARGE.value,
                detail=(
                    f"Local FS uploaded file '{key}' exceeds "
                    f"{LOCAL_FS_MAX_UPLOADED_FILE_BYTES} bytes "
                    f"(got {len(data)})"
                ),
            )
        aggregate_bytes += len(data)
        if aggregate_bytes > LOCAL_FS_MAX_UPLOADED_BATCH_BYTES:
            raise HTTPException(
                status_code=HttpStatusCode.PAYLOAD_TOO_LARGE.value,
                detail=(
                    f"Local FS uploaded batch exceeds "
                    f"{LOCAL_FS_MAX_UPLOADED_BATCH_BYTES} aggregate bytes"
                ),
            )
        files_by_field[key] = data
        close = getattr(value, "close", None)
        if close is not None:
            await close()

    return parsed, files_by_field


def _normalize_connector_type_value(value: str) -> str:
    """Normalize connector type values for consistent Local FS checks."""
    return value.replace("_", "").replace(" ", "").strip().lower()


async def _update_connector_status(
    graph_provider: IGraphDBProvider,
    connector_id: str,
    status: str,
) -> None:
    """Persist connector runtime status transition with updated timestamp."""
    await graph_provider.batch_upsert_nodes(
        [
            {
                "id": connector_id,
                "status": status,
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }
        ],
        CollectionNames.APPS.value,
    )
