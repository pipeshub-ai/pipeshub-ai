# pyright: ignore-file

"""CIFS connector fixtures. Skip when host/share/username/password are absent."""

import os
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio

from connector_lifecycle import constructor, destructor
from helper.graph_provider import GraphProviderProtocol
from helper.source_credentials import missing_env, source_unavailable
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.cifs.cifs_storage_helper import CifsStorageHelper

_SECRETS = ["CIFS_HOST", "CIFS_SHARE", "CIFS_USERNAME", "CIFS_PASSWORD"]


def _require_cifs_creds() -> dict[str, str]:
    absent = missing_env(_SECRETS)
    if absent:
        source_unavailable(
            "The CIFS share this suite syncs from is not configured.",
            secrets=absent,
        )
    host = os.environ["CIFS_HOST"]
    return {
        "host": host,
        "share": os.environ["CIFS_SHARE"],
        "username": os.environ["CIFS_USERNAME"],
        "password": os.environ["CIFS_PASSWORD"],
        "port": os.getenv("CIFS_PORT", "445"),
        "domain": os.getenv("CIFS_DOMAIN", ""),
        "server_name": os.getenv("CIFS_SERVER_NAME", host.split(".")[0]),
    }


@pytest.fixture(scope="session")
def cifs_storage() -> CifsStorageHelper:
    creds = _require_cifs_creds()
    return CifsStorageHelper(
        creds["host"],
        creds["username"],
        creds["password"],
        creds["server_name"],
        port=int(creds["port"]),
        domain=creds["domain"],
    )


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def cifs_connector(
    cifs_storage: CifsStorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    sample_data_root,
) -> AsyncGenerator[Dict[str, Any], None]:
    creds = _require_cifs_creds()
    config = {
        "auth": {
            "server": creds["host"],
            "serverName": creds["server_name"],
            "username": creds["username"],
            "password": creds["password"],
            "share": creds["share"],
            "port": creds["port"],
            "domain": creds["domain"],
        }
    }
    state = await constructor(
        cifs_storage,
        pipeshub_client,
        graph_provider,
        sample_data_root,
        storage_name="CIFS share",
        connector_type="CIFS",
        connector_config=config,
        resource_name_override=creds["share"],
    )
    state["share_name"] = creds["share"]
    yield state
    await destructor(
        cifs_storage, pipeshub_client, graph_provider, state, connector_type="CIFS"
    )
