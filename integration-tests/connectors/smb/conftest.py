# pyright: ignore-file

"""SMB connector fixtures. Skip when host/share/username/password are absent."""

import os
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio

from connector_lifecycle import constructor, destructor
from helper.graph_provider import GraphProviderProtocol
from helper.source_credentials import missing_env, source_unavailable
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.smb.smb_storage_helper import SmbStorageHelper

_SECRETS = ["SMB_HOST", "SMB_SHARE", "SMB_USERNAME", "SMB_PASSWORD"]


def _require_smb_creds() -> dict[str, str]:
    absent = missing_env(_SECRETS)
    if absent:
        source_unavailable(
            "The SMB share this suite syncs from is not configured.",
            secrets=absent,
        )
    return {
        "host": os.environ["SMB_HOST"],
        "share": os.environ["SMB_SHARE"],
        "username": os.environ["SMB_USERNAME"],
        "password": os.environ["SMB_PASSWORD"],
        "port": os.getenv("SMB_PORT", "445"),
        "domain": os.getenv("SMB_DOMAIN", ""),
    }


@pytest.fixture(scope="session")
def smb_storage() -> SmbStorageHelper:
    creds = _require_smb_creds()
    return SmbStorageHelper(
        creds["host"],
        creds["username"],
        creds["password"],
        port=int(creds["port"]),
        domain=creds["domain"],
    )


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def smb_connector(
    smb_storage: SmbStorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    sample_data_root,
) -> AsyncGenerator[Dict[str, Any], None]:
    creds = _require_smb_creds()
    config = {
        "auth": {
            "server": creds["host"],
            "username": creds["username"],
            "password": creds["password"],
            "share": creds["share"],
            "port": creds["port"],
            "domain": creds["domain"],
        }
    }
    state = await constructor(
        smb_storage,
        pipeshub_client,
        graph_provider,
        sample_data_root,
        storage_name="SMB share",
        connector_type="SMB",
        connector_config=config,
        resource_name_override=creds["share"],
    )
    state["share_name"] = creds["share"]
    yield state
    await destructor(
        smb_storage, pipeshub_client, graph_provider, state, connector_type="SMB"
    )
