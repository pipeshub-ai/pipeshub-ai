# pyright: ignore-file

"""MinIO connector fixtures.

Unlike the other object-store connectors, MinIO needs no external account: the
integration compose file runs a MinIO server and creates the bucket, so this
connector has live coverage on any machine that can start the stack.
"""

import os
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio

from connector_lifecycle import constructor, destructor
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol

from connectors.minio.minio_storage_helper import MinioStorageHelper

# Defaults match deployment/docker-compose/docker-compose.integration.*.yml, so
# the suite runs against a locally started stack with nothing exported.
DEFAULT_ENDPOINT = "http://localhost:9000"
DEFAULT_ACCESS_KEY = "pipeshubtest"
DEFAULT_SECRET_KEY = "pipeshubtest123"
DEFAULT_BUCKET = "pipeshub-connector-test"


def _endpoint() -> str:
    return os.getenv("MINIO_TEST_ENDPOINT", DEFAULT_ENDPOINT)


def _bucket() -> str:
    return os.getenv("MINIO_TEST_BUCKET", DEFAULT_BUCKET)



def _unavailable(reason: str) -> None:
    """A source that is not reachable: skip locally, fail in CI.

    Skipping on any exception turns a broken stack into a green run. CI brings
    this source up itself, so if it is not reachable there, that is a result and
    not a reason to report success.
    """
    if os.getenv("CI"):
        pytest.fail(reason)
    pytest.skip(reason)

@pytest.fixture(scope="session")
def minio_storage() -> MinioStorageHelper:
    helper = MinioStorageHelper(
        access_key=os.getenv("MINIO_ROOT_USER", DEFAULT_ACCESS_KEY),
        secret_key=os.getenv("MINIO_ROOT_PASSWORD", DEFAULT_SECRET_KEY),
        endpoint_url=_endpoint(),
    )
    # Skip rather than fail when the stack is not up: this suite is run both
    # from CI, where MinIO is always present, and locally against a partial
    # stack.
    try:
        helper.list_objects(_bucket())
    except Exception as exc:  # noqa: BLE001 — any failure here means "not available"
        _unavailable(f"MinIO not reachable at {_endpoint()}: {exc}")
    return helper


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def minio_connector(
    minio_storage: MinioStorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    sample_data_root,
) -> AsyncGenerator[Dict[str, Any], None]:
    # The connector runs inside the compose network, so it reaches MinIO by
    # service name; the test process reaches it on the published port.
    config = {
        "auth": {
            "endpointUrl": os.getenv("MINIO_CONNECTOR_ENDPOINT", "http://minio:9000"),
            "accessKey": os.getenv("MINIO_ROOT_USER", DEFAULT_ACCESS_KEY),
            "secretKey": os.getenv("MINIO_ROOT_PASSWORD", DEFAULT_SECRET_KEY),
            "useSsl": False,
            "verifySsl": False,
        }
    }

    state = await constructor(
        minio_storage,
        pipeshub_client,
        graph_provider,
        sample_data_root,
        storage_name="MinIO bucket",
        connector_type="MinIO",
        connector_config=config,
        resource_name_override=_bucket(),
    )
    state["bucket_name"] = state["resource_name"]
    yield state
    await destructor(
        minio_storage, pipeshub_client, graph_provider, state, connector_type="MinIO"
    )
