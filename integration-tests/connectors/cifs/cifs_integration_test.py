# pyright: ignore-file

"""CIFS/SMB1 connector integration tests.

Skip when CIFS_HOST / CIFS_SHARE / CIFS_USERNAME / CIFS_PASSWORD are absent.
Do not point this suite at SMB 2+ servers; use the SMB connector tests.
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

from connectors.cifs.cifs_storage_helper import CifsStorageHelper  # type: ignore[import-not-found]  # noqa: E402

logger = logging.getLogger("cifs-lifecycle-test")


@pytest.mark.integration
@pytest.mark.cifs
@pytest.mark.asyncio(loop_scope="session")
class TestCifsConnector:
    @pytest.mark.order(1)
    async def test_list_share_root(
        self,
        cifs_connector: Dict[str, Any],
        cifs_storage: CifsStorageHelper,
    ) -> None:
        listed = cifs_storage.list_objects(cifs_connector["share_name"], cifs_connector["folder"])
        assert listed, "CIFS run folder should contain uploaded files"

    @pytest.mark.order(2)
    async def test_sync_fixture_tree(
        self,
        cifs_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        connector_id = cifs_connector["connector_id"]
        await graph_provider.assert_min_records(connector_id, cifs_connector["uploaded_count"])
        known_name = cifs_connector.get("rename_source_name")
        if known_name:
            await graph_provider.assert_record_paths_or_names_contain(connector_id, [known_name])

    @pytest.mark.order(3)
    async def test_rename_uses_move_not_delete_create(
        self,
        cifs_connector: Dict[str, Any],
        cifs_storage: CifsStorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        connector_id = cifs_connector["connector_id"]
        share = cifs_connector["share_name"]
        old_key = cifs_connector["rename_source_key"]
        old_name = Path(old_key).name
        new_name = f"renamed-{old_name}"
        parts = old_key.rsplit("/", 1)
        new_key = f"{parts[0]}/{new_name}" if len(parts) == 2 else new_name

        before = await graph_provider.get_record_by_external_id(
            connector_id, f"{share}/{old_key}"
        )
        cifs_storage.rename_object(share, old_key, new_key)
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
            description="CIFS rename sync",
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
        logger.info("CIFS rename %s -> %s (connector %s)", old_name, new_name, connector_id)
