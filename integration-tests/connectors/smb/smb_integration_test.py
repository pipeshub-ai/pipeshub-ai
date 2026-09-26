# pyright: ignore-file

"""SMB connector integration tests.

Skip when SMB_HOST / SMB_SHARE / SMB_USERNAME / SMB_PASSWORD are absent.
No SMB1 or QUIC cases belong here; those servers use the CIFS connector.
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import wait_until_graph_condition  # noqa: E402
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402

from connectors.smb.smb_storage_helper import SmbStorageHelper  # type: ignore[import-not-found]  # noqa: E402

logger = logging.getLogger("smb-lifecycle-test")


@pytest.mark.integration
@pytest.mark.smb
@pytest.mark.asyncio(loop_scope="session")
class TestSmbConnector:
    @pytest.mark.order(1)
    async def test_list_share_root(
        self,
        smb_connector: Dict[str, Any],
        smb_storage: SmbStorageHelper,
    ) -> None:
        listed = smb_storage.list_objects(smb_connector["share_name"], smb_connector["folder"])
        assert listed, "Share root of the run folder should contain uploaded files"

    @pytest.mark.order(2)
    async def test_sync_fixture_tree(
        self,
        smb_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        connector_id = smb_connector["connector_id"]
        uploaded = smb_connector["uploaded_count"]
        await graph_provider.assert_min_records(connector_id, uploaded)
        known_name = smb_connector.get("rename_source_name")
        if known_name:
            await graph_provider.assert_record_paths_or_names_contain(connector_id, [known_name])

    @pytest.mark.order(3)
    async def test_rename_uses_move_not_delete_create(
        self,
        smb_connector: Dict[str, Any],
        smb_storage: SmbStorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        connector_id = smb_connector["connector_id"]
        share = smb_connector["share_name"]
        old_key = smb_connector["rename_source_key"]
        old_name = Path(old_key).name
        new_name = f"renamed-{old_name}"
        parts = old_key.rsplit("/", 1)
        new_key = f"{parts[0]}/{new_name}" if len(parts) == 2 else new_name

        before = await graph_provider.get_record_by_external_id(
            connector_id, f"{share}/{old_key}"
        )
        smb_storage.rename_object(share, old_key, new_key)
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        async def _renamed() -> bool:
            return await graph_provider.record_paths_or_names_contain(connector_id, [new_name])

        await wait_until_graph_condition(
            connector_id,
            check=_renamed,
            timeout=120,
            poll_interval=10,
            description="SMB rename sync",
        )
        await graph_provider.assert_record_paths_or_names_contain(connector_id, [new_name])
        await graph_provider.assert_record_not_exists(connector_id, old_name)
        after = await graph_provider.get_record_by_external_id(
            connector_id, f"{share}/{new_key}"
        )
        if before is not None and after is not None:
            before_id = getattr(before, "id", None) or (before.get("id") if isinstance(before, dict) else None)
            after_id = getattr(after, "id", None) or (after.get("id") if isinstance(after, dict) else None)
            assert before_id == after_id, (
                "Rename should reuse the graph vertex (on_records_moved), not delete+create"
            )
        logger.info("SMB rename %s -> %s (connector %s)", old_name, new_name, connector_id)
