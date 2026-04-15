"""Fixtures for storage integration tests."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Generator
from urllib.parse import unquote, urlparse

import pytest
import requests

_THIS_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _THIS_DIR.parent
_HELPER_DIR = _ROOT_DIR / "helper"
for _p in (_ROOT_DIR, _HELPER_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from dotenv import load_dotenv

logger = logging.getLogger("storage-conftest")


class _S3CleanupTracker:
    def __init__(self) -> None:
        self._doc_ids: set[str] = set()

    def add_document_id(self, doc_id: str) -> None:
        if doc_id:
            self._doc_ids.add(doc_id)

    def document_ids(self) -> list[str]:
        return sorted(self._doc_ids)


def _load_env() -> None:
    env_path = _ROOT_DIR / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)
    test_env = os.getenv("PIPESHUB_TEST_ENV", "").strip().lower()
    if test_env == "local":
        local_env = _ROOT_DIR / ".env.local"
        if local_env.exists():
            load_dotenv(dotenv_path=local_env, override=True)
        os.environ.pop("PIPESHUB_USER_BEARER_TOKEN", None)
    elif test_env == "prod":
        prod_env = _ROOT_DIR / ".env.prod"
        if prod_env.exists():
            load_dotenv(dotenv_path=prod_env, override=True)


_load_env()

from local_auth import obtain_local_oauth_credentials
from pipeshub_client import PipeshubClient
from storage_client import StorageClient

# ---------------------------------------------------------------------------
# Storage backend configuration helpers
# ---------------------------------------------------------------------------
_STORAGE_CONFIG_PATH = "/api/v1/configurationManager/storageConfig"


def _build_storage_payload(backend: str) -> dict:
    """Build the request body for POST /storageConfig."""
    if backend == "s3":
        return {
            "storageType": "s3",
            "s3AccessKeyId": os.environ["S3_ACCESS_KEY"],
            "s3SecretAccessKey": os.environ["S3_SECRET_KEY"],
            "s3Region": os.environ["S3_REGION"],
            "s3BucketName": os.environ["S3_BUCKET"],
        }
    # default: local
    return {"storageType": "local"}


def _set_storage_backend(client: PipeshubClient, backend: str) -> None:
    """Switch the server's storage backend via the configuration manager API."""
    payload = _build_storage_payload(backend)
    url = client._url(_STORAGE_CONFIG_PATH)
    resp = requests.post(
        url,
        headers=client._headers(),
        json=payload,
        timeout=client.timeout_seconds,
    )
    resp.raise_for_status()
    logger.info("Switched storage backend to '%s'", backend)


def _available_backends() -> list[str]:
    """Return the list of storage backends to test based on available credentials."""
    backends = ["local"]
    if os.getenv("S3_ACCESS_KEY") and os.getenv("S3_SECRET_KEY") and os.getenv("S3_REGION") and os.getenv("S3_BUCKET"):
        backends.append("s3")
    return backends


def _extract_s3_key_from_url(url: str, bucket: str) -> str | None:
    parsed = urlparse(url)
    path = unquote(parsed.path.lstrip("/"))
    if not path:
        return None

    if parsed.scheme == "s3" and parsed.netloc == bucket:
        return path

    host = parsed.netloc.lower()
    bucket_host_prefix = f"{bucket}.s3."
    if host == f"{bucket}.s3.amazonaws.com":
        return path
    if host.startswith(bucket_host_prefix) and host.endswith(".amazonaws.com"):
        return path
    if host == "s3.amazonaws.com" and path.startswith(f"{bucket}/"):
        return path[len(bucket) + 1 :]
    return None


def _extract_document_s3_keys(document: dict, bucket: str) -> set[str]:
    keys: set[str] = set()

    doc_path = document.get("documentPath")
    if isinstance(doc_path, str) and doc_path:
        keys.add(doc_path)

    s3_info = document.get("s3")
    if isinstance(s3_info, dict):
        s3_url = s3_info.get("url")
        if isinstance(s3_url, str):
            key = _extract_s3_key_from_url(s3_url, bucket)
            if key:
                keys.add(key)

    for version in document.get("versionHistory") or []:
        if not isinstance(version, dict):
            continue
        version_s3 = version.get("s3")
        if not isinstance(version_s3, dict):
            continue
        version_url = version_s3.get("url")
        if not isinstance(version_url, str):
            continue
        key = _extract_s3_key_from_url(version_url, bucket)
        if key:
            keys.add(key)

    return keys


def _chunked(values: list[str], size: int) -> Generator[list[str], None, None]:
    for i in range(0, len(values), size):
        yield values[i : i + size]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def local_oauth_credentials() -> None:
    if os.getenv("PIPESHUB_TEST_ENV") != "local":
        return
    if os.getenv("CLIENT_ID") and os.getenv("CLIENT_SECRET"):
        return
    base_url = os.getenv("PIPESHUB_BASE_URL", "").rstrip("/")
    if not base_url:
        return
    client_id, client_secret = obtain_local_oauth_credentials(base_url)
    os.environ["CLIENT_ID"] = client_id
    os.environ["CLIENT_SECRET"] = client_secret


@pytest.fixture(scope="session")
def pipeshub_client() -> PipeshubClient:
    return PipeshubClient()


@pytest.fixture(scope="session")
def s3_cleanup_tracker(
    storage_backend: str,
    pipeshub_client: PipeshubClient,
) -> Generator[_S3CleanupTracker, None, None]:
    tracker = _S3CleanupTracker()
    yield tracker

    if storage_backend != "s3":
        return

    bucket = os.getenv("S3_BUCKET", "").strip()
    access_key = os.getenv("S3_ACCESS_KEY", "").strip()
    secret_key = os.getenv("S3_SECRET_KEY", "").strip()
    region = os.getenv("S3_REGION", "").strip()
    if not bucket or not access_key or not secret_key or not region:
        logger.warning("Skipping centralized S3 cleanup due to missing S3 credentials")
        return

    document_ids = tracker.document_ids()
    if not document_ids:
        return

    object_keys: set[str] = set()
    for doc_id in document_ids:
        try:
            resp = requests.get(
                pipeshub_client._url(f"/api/v1/document/{doc_id}"),
                headers=pipeshub_client._headers(),
                timeout=pipeshub_client.timeout_seconds,
            )
            if resp.status_code != 200:
                logger.warning(
                    "Skipping S3 key discovery for doc %s (status=%s, body=%s)",
                    doc_id,
                    resp.status_code,
                    resp.text[:500],
                )
                continue
            object_keys.update(_extract_document_s3_keys(resp.json(), bucket))
        except Exception:
            logger.exception("Failed to discover S3 keys for doc %s", doc_id)

    if not object_keys:
        logger.info(
            "Centralized S3 cleanup: no object keys discovered for %d document(s)",
            len(document_ids),
        )
        return

    import boto3
    from botocore.exceptions import ClientError

    s3_client = boto3.client(
        "s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )

    keys_to_delete = sorted(object_keys)
    logger.info(
        "Centralized S3 cleanup: attempting to delete %d object(s) from bucket '%s'",
        len(keys_to_delete),
        bucket,
    )

    deleted_keys: list[str] = []
    failed_keys: list[str] = []

    def _log_client_error(action: str, key: str, exc: ClientError) -> None:
        err = exc.response.get("Error")
        meta = exc.response.get("ResponseMetadata")
        logger.error(
            "S3 %s failed for key '%s' in bucket '%s': "
            "code=%s, message=%s, http_status=%s, request_id=%s, host_id=%s",
            action,
            key,
            bucket,
            err.get("Code"),
            err.get("Message"),
            meta.get("HTTPStatusCode"),
            meta.get("RequestId"),
            meta.get("HostId"),
        )

    def _retry_delete_single(key: str) -> bool:
        """Re-attempt delete via delete_object to surface the real S3 error."""
        try:
            s3_client.delete_object(Bucket=bucket, Key=key)
            logger.info("S3 retry delete_object succeeded for key '%s'", key)
            return True
        except ClientError as exc:
            _log_client_error("delete_object (retry)", key, exc)
        except Exception:
            logger.exception("S3 retry delete_object raised for key '%s'", key)
        return False

    for key_chunk in _chunked(keys_to_delete, 1000):
        try:
            delete_resp = s3_client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": key} for key in key_chunk], "Quiet": False},
            )
        except ClientError as exc:
            err = exc.response.get("Error", {})
            meta = exc.response.get("ResponseMetadata", {})
            logger.error(
                "S3 delete_objects failed for %d key(s) in bucket '%s': "
                "code=%s, message=%s, http_status=%s, request_id=%s; keys=%s",
                len(key_chunk),
                bucket,
                err.get("Code"),
                err.get("Message"),
                meta.get("HTTPStatusCode"),
                meta.get("RequestId"),
                key_chunk,
            )
            failed_keys.extend(key_chunk)
            continue
        except Exception:
            logger.exception(
                "S3 delete_objects call raised for %d key(s): %s",
                len(key_chunk),
                key_chunk,
            )
            failed_keys.extend(key_chunk)
            continue

        for deleted in delete_resp.get("Deleted") or []:
            key = deleted.get("Key")
            if key:
                deleted_keys.append(key)

        errors = delete_resp.get("Errors") or []
        if errors:
            for err in errors:
                logger.error(
                    "S3 delete_objects reported failure for key '%s' in bucket '%s': "
                    "code=%s, message=%s, version_id=%s",
                    err.get("Key"),
                    bucket,
                    err.get("Code"),
                    err.get("Message"),
                    err.get("VersionId"),
                )
                err_key = err.get("Key")
                if err_key:
                    failed_keys.append(err_key)

    # Rely on delete_objects' per-key Deleted/Errors response (S3 has
    # strong read-after-write consistency, so a separate HEAD verification
    # pass is redundant).  For any key the batch API reported as failed,
    # retry once via delete_object so the real S3 error (AccessDenied,
    # object-lock, versioning/MFA-delete, ...) is surfaced in the logs.
    retry_failed: list[str] = []
    for key in list(failed_keys):
        if _retry_delete_single(key):
            deleted_keys.append(key)
        else:
            retry_failed.append(key)

    logger.info(
        "Centralized S3 cleanup summary: deleted=%d, batch_failed=%d, "
        "retry_failed=%d",
        len(deleted_keys),
        len(failed_keys),
        len(retry_failed),
    )
    if retry_failed:
        logger.error("S3 cleanup leaked objects (retry also failed): %s", retry_failed)


@pytest.fixture(scope="session", params=_available_backends())
def storage_backend(
    request: pytest.FixtureRequest,
    pipeshub_client: PipeshubClient,
) -> Generator[str, None, None]:
    """Parametrized fixture that configures the server's storage backend.

    Yields the backend name (e.g. "local", "s3") so tests can inspect it
    if needed.  After the test session for each parameter completes, the
    backend is reset to 'local'.
    """
    backend: str = request.param
    _set_storage_backend(pipeshub_client, backend)
    yield backend
    # Reset to local after the parametrized run
    if backend != "local":
        try:
            _set_storage_backend(pipeshub_client, "local")
        except Exception:
            logger.warning("Failed to reset storage backend to 'local'", exc_info=True)


@pytest.fixture(scope="session")
def sc(
    pipeshub_client: PipeshubClient,
    storage_backend: str,
    s3_cleanup_tracker: _S3CleanupTracker,
) -> StorageClient:
    return StorageClient(
        pipeshub_client,
        register_document_id=s3_cleanup_tracker.add_document_id,
    )
