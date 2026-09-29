"""Admin calls behind the Labs search-index rebuild and the embedding model settings.

The Labs page has two buttons, "Delete all embeddings" and "Recreate all
embeddings", which call ``POST /api/v1/connectors/vector-store/cleanup`` and
``.../reindex``. Both answer 202 at once and run in the background; neither has
a status endpoint, so a test watches the stores instead. Both refuse with 409
while another rebuild holds the lock or while any record is still queued or
being indexed, which is normal for a few seconds after an upload, so starting a
job waits that out rather than failing on the first 409.

The Python side refuses both with 403 unless the platform feature flag
``ENABLE_VECTOR_STORE_REBUILD`` is on. The flag lives in one platform-settings
document that is written whole, so changing it reads the document first and
sends every other setting back unchanged.

The embedding model calls mirror the AI models page: add a provider, make one
the default, delete one. Every add, and every change of default, runs the
query service's collection guard, which refuses a different model while the
vector store holds points and rebuilds the collection at the new size while it
is empty.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import requests

from pipeshub_client import PipeshubClient

VECTOR_STORE_REBUILD_FLAG = "ENABLE_VECTOR_STORE_REBUILD"

PLATFORM_SETTINGS_PATH = "/api/v1/configurationManager/platform/settings"
VECTOR_STORE_JOB_PATH = "/api/v1/connectors/vector-store/{operation}"
EMBEDDING_MODELS_PATH = "/api/v1/configurationManager/ai-models/embedding"
PROVIDERS_PATH = "/api/v1/configurationManager/ai-models/providers"
DEFAULT_MODEL_PATH = "/api/v1/configurationManager/ai-models/default/embedding/{model_key}"

# The model the product ships as "Default (System Provided)". It is baked into
# the image (Dockerfile.base, BUNDLE_BGE_EMBEDDING), so the local embedding
# server can serve it with no download, and its vectors are 1024 wide, unlike
# the 1536 of the cloud model the suite seeds.
LOCAL_MODEL_PROVIDER = "default"
LOCAL_MODEL_NAME = "BAAI/bge-large-en-v1.5"
LOCAL_MODEL_DIMENSION = 1024

# The first request to the local model loads it on CPU, and the health check
# that runs on every model change embeds a sample with it.
MODEL_CHANGE_TIMEOUT = 660

MODEL_CHANGE_REFUSED = (
    "Embedding model cannot be changed while the vector store contains data "
    "indexed with a different model"
)


@dataclass
class PlatformSettings:
    file_upload_max_size_bytes: int
    feature_flags: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def from_api(cls, body: dict[str, Any]) -> "PlatformSettings":
        size = body.get("fileUploadMaxSizeBytes")
        if not isinstance(size, int) or size <= 0:
            raise AssertionError(
                f"Platform settings came back without a usable fileUploadMaxSizeBytes: {body}"
            )
        flags = body.get("featureFlags") or {}
        return cls(size, {str(k): bool(v) for k, v in flags.items()})

    def to_api(self) -> dict[str, Any]:
        return {
            "fileUploadMaxSizeBytes": self.file_upload_max_size_bytes,
            "featureFlags": dict(self.feature_flags),
        }

    def with_flag(self, name: str, enabled: bool) -> "PlatformSettings":
        return PlatformSettings(
            self.file_upload_max_size_bytes, {**self.feature_flags, name: enabled}
        )

    def flag(self, name: str) -> bool:
        return bool(self.feature_flags.get(name, False))


@dataclass(frozen=True)
class EmbeddingModel:
    model_key: str
    provider: str
    model: str
    is_default: bool


def error_message(resp: requests.Response) -> str:
    """The message a person would see, whichever error shape the gateway used."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:500]
    if not isinstance(body, dict):
        return str(body)[:500]
    error = body.get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    if isinstance(error, str) and error:
        return error
    for key in ("message", "detail"):
        if body.get(key):
            return str(body[key])
    return str(body)[:500]


def parse_embedding_models(body: dict[str, Any]) -> list[EmbeddingModel]:
    models = body.get("models")
    if not isinstance(models, list):
        raise AssertionError(f"Embedding model list has no 'models' array: {body}")
    parsed = []
    for entry in models:
        if not isinstance(entry, dict) or not entry.get("modelKey"):
            raise AssertionError(f"Embedding model entry without a modelKey: {entry}")
        configuration = entry.get("configuration") or {}
        parsed.append(
            EmbeddingModel(
                model_key=str(entry["modelKey"]),
                provider=str(entry.get("provider") or ""),
                model=str(configuration.get("model") or ""),
                is_default=bool(entry.get("isDefault")),
            )
        )
    return parsed


def default_model(models: list[EmbeddingModel]) -> EmbeddingModel | None:
    """The model indexing uses: the one marked default, else the first."""
    for model in models:
        if model.is_default:
            return model
    return models[0] if models else None


def read_platform_settings(client: PipeshubClient) -> PlatformSettings:
    resp = client.request("GET", PLATFORM_SETTINGS_PATH)
    assert resp.status_code == 200, (
        f"Reading platform settings failed: HTTP {resp.status_code} {error_message(resp)}"
    )
    return PlatformSettings.from_api(resp.json())


def write_platform_settings(client: PipeshubClient, settings: PlatformSettings) -> None:
    resp = client.request("POST", PLATFORM_SETTINGS_PATH, json=settings.to_api())
    assert resp.status_code == 200, (
        f"Saving platform settings failed: HTTP {resp.status_code} {error_message(resp)}"
    )


def set_rebuild_flag(client: PipeshubClient, enabled: bool) -> None:
    write_platform_settings(
        client, read_platform_settings(client).with_flag(VECTOR_STORE_REBUILD_FLAG, enabled)
    )


def post_vector_store_job(client: PipeshubClient, operation: str) -> requests.Response:
    if operation not in ("cleanup", "reindex"):
        raise ValueError(f"not a vector store job: {operation!r}")
    return client.request("POST", VECTOR_STORE_JOB_PATH.format(operation=operation), json={})


def start_vector_store_job(
    client: PipeshubClient, operation: str, *, timeout: float, poll: float = 10
) -> requests.Response:
    """Start a Labs job, waiting out the 409s that only mean "not yet".

    Returns the last response, so the caller asserts on what the product said.
    A 409 that outlasts the timeout is returned as-is: its message names what
    is still busy (a sync, or records stuck queued), which is the finding.
    """
    deadline = time.monotonic() + timeout
    while True:
        resp = post_vector_store_job(client, operation)
        if resp.status_code != 409 or time.monotonic() >= deadline:
            return resp
        time.sleep(poll)


def list_embedding_models(client: PipeshubClient) -> list[EmbeddingModel]:
    resp = client.request("GET", EMBEDDING_MODELS_PATH)
    assert resp.status_code == 200, (
        f"Listing embedding models failed: HTTP {resp.status_code} {error_message(resp)}"
    )
    return parse_embedding_models(resp.json())


def add_local_embedding_model(client: PipeshubClient, *, is_default: bool) -> requests.Response:
    """Add the built-in local model, with the payload the AI models page sends."""
    return client.request(
        "POST",
        PROVIDERS_PATH,
        json={
            "modelType": "embedding",
            "provider": LOCAL_MODEL_PROVIDER,
            "configuration": {"model": LOCAL_MODEL_NAME},
            "isMultimodal": False,
            "isReasoning": False,
            "isDefault": is_default,
            "contextLength": None,
        },
        timeout=MODEL_CHANGE_TIMEOUT,
    )


def model_key_from_add(resp: requests.Response) -> str:
    details = (resp.json() or {}).get("details") or {}
    key = details.get("modelKey")
    assert key, f"Adding an embedding model answered without details.modelKey: {resp.text[:500]}"
    return str(key)


def set_default_embedding_model(client: PipeshubClient, model_key: str) -> requests.Response:
    return client.request(
        "PUT", DEFAULT_MODEL_PATH.format(model_key=model_key), timeout=MODEL_CHANGE_TIMEOUT
    )


def delete_embedding_model(client: PipeshubClient, model_key: str) -> requests.Response:
    return client.request("DELETE", f"{PROVIDERS_PATH}/embedding/{model_key}")
