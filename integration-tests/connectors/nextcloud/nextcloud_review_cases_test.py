# pyright: ignore-file

"""
Nextcloud connector: the review cases, live
==================================================

Same fixture as nextcloud_integration_test.py (its own connector and run
folder, full sync first); every case after that is incremental unless it says
otherwise.

  TC-EMAIL-ISOLATION   — the Nextcloud profile email names another person: they get no access to the app
  TC-EMPTY-FILE        — a zero-byte file streams as an empty file
  TC-FOLDER-COPY       — a folder copied on the server comes with what it holds (Nextcloud logs only the folder)
  TC-UNSHARE           — a folder another user shares appears, and leaves the index once unshared
  TC-ACTIVITY-MISSING  — with the Activity app disabled, new files still sync (the drive is listed instead)
  TC-FULLSYNC-OUTAGE   — a full sync while Nextcloud is in maintenance mode unlinks nothing

The last two need ``occ`` on the server (``NEXTCLOUD_CONTAINER``, or the stack's
nextcloud-source service) and skip without it.
"""

import logging
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connectors.nextcloud.nextcloud_source_helper import (  # type: ignore[import-not-found]
    NextcloudSourceHelper,
)
from helper.compose_control import ComposeUnavailable
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_for_sync_completion
from helper.storage_incremental import (
    DEFAULT_SYNC_TIMEOUT_SEC,
    restart_sync,
    settle_record_baseline,
    sync_until_names_absent,
    sync_until_names_visible,
)
from pipeshub_client import (
    PipeshubClient,  # type: ignore[import-not-found]
)

logger = logging.getLogger("nextcloud-review-cases-test")


def _occ_or_skip(source: NextcloudSourceHelper, *args: str) -> str:
    try:
        return source.occ(*args)
    except ComposeUnavailable as e:
        pytest.skip(f"occ is not available on the test Nextcloud: {e}")


@pytest.mark.integration
@pytest.mark.nextcloud
@pytest.mark.asyncio(loop_scope="session")
class TestNextcloudReviewCases:
    @pytest.mark.order(1)
    async def test_tc_email_isolation_profile_email_gets_no_access(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-EMAIL-ISOLATION: only the creator is linked to the app, whatever the profile says."""
        connector_id = nextcloud_connector["connector_id"]
        stranger = f"nc-isolation-{uuid.uuid4().hex[:8]}@example.com"
        nextcloud_source.set_email(stranger)
        try:
            # Turning the connector off and on runs init(), which used to read the profile
            # email; the full sync after it is what linked that email's user to the app.
            restart_sync(pipeshub_client, connector_id)
            await wait_for_sync_completion(
                pipeshub_client, graph_provider, connector_id, timeout=DEFAULT_SYNC_TIMEOUT_SEC
            )
            pipeshub_client.resync_connector(connector_id, full_sync=True)
            await wait_for_sync_completion(
                pipeshub_client, graph_provider, connector_id, timeout=DEFAULT_SYNC_TIMEOUT_SEC
            )
        finally:
            nextcloud_source.set_email(nextcloud_source.email)

        assert await graph_provider.principal_app_relation(connector_id, stranger) is None, (
            "TC-EMAIL-ISOLATION: the Nextcloud profile email was given access to the connector's app"
        )
        assert await graph_provider.principal_app_relation(connector_id, nextcloud_source.email) is not None, (
            "TC-EMAIL-ISOLATION: the creator (the signed-in test user) lost access"
        )
        assert await graph_provider.count_user_app_relation_edges(connector_id) == 1
        logger.info("TC-EMAIL-ISOLATION passed: %s has no app edge", stranger)

    @pytest.mark.order(2)
    async def test_tc_empty_file_streams_empty(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-EMPTY-FILE: a zero-byte file is a file, not a 404."""
        connector_id = nextcloud_connector["connector_id"]
        name = f"empty-{uuid.uuid4().hex[:6]}.txt"
        path = f"{nextcloud_connector['folder']}/{name}"
        await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        nextcloud_source.put(path, "")
        await sync_until_names_visible(pipeshub_client, graph_provider, connector_id, [name])

        record = await graph_provider.get_record_by_external_id(connector_id, nextcloud_source.file_id(path))
        assert record is not None, f"TC-EMPTY-FILE: {name} is not in the graph"
        response = pipeshub_client.stream_record(record.id)
        assert response.status_code == 200, f"TC-EMPTY-FILE: streaming answered {response.status_code}"
        assert response.content == b""

    @pytest.mark.order(3)
    async def test_tc_folder_copy_brings_its_contents(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FOLDER-COPY: Nextcloud logs one activity for a copied folder; its files sync too."""
        connector_id = nextcloud_connector["connector_id"]
        folder = nextcloud_connector["folder"]
        copy = f"Handbook-copy-{uuid.uuid4().hex[:6]}"
        before = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)

        nextcloud_source.copy(f"{folder}/Handbook", f"{folder}/{copy}")
        await sync_until_names_visible(pipeshub_client, graph_provider, connector_id, [copy])

        async def copies_in_graph() -> list[str]:
            found = []
            for name in ("leave-policy.txt", "onboarding.md"):
                file_id = nextcloud_source.file_id(f"{folder}/{copy}/{name}")
                if await graph_provider.get_record_by_external_id(connector_id, file_id) is not None:
                    found.append(name)
            return found

        assert await copies_in_graph() == ["leave-policy.txt", "onboarding.md"], (
            "TC-FOLDER-COPY: the copied folder's files are missing from the graph"
        )
        after = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        assert after == before + 3, f"TC-FOLDER-COPY: expected the folder and its two files; {before} -> {after}"

    @pytest.mark.order(4)
    async def test_tc_unshare_removes_what_was_shared(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-UNSHARE: a share shows up incrementally, and an unshare takes it out of the index."""
        connector_id = nextcloud_connector["connector_id"]
        token = uuid.uuid4().hex[:8]
        owner = (f"review-owner-{token}", f"Owner-Pass-{token}-2026!")
        shared_folder, shared_file = f"shared-{token}", f"plan-{token}.txt"
        nextcloud_source.ensure_other_user(*owner)
        try:
            nextcloud_source.put(f"{shared_folder}/{shared_file}", "The shared plan.", auth=owner)
            await settle_record_baseline(pipeshub_client, graph_provider, connector_id)

            share_id = nextcloud_source.share_with_sync_user(owner, shared_folder)
            await sync_until_names_visible(
                pipeshub_client, graph_provider, connector_id, [shared_folder, shared_file]
            )

            nextcloud_source.unshare(owner, share_id)
            await sync_until_names_absent(
                pipeshub_client, graph_provider, connector_id, [shared_folder, shared_file]
            )
        finally:
            nextcloud_source.delete_user(owner[0])
        await graph_provider.assert_no_orphan_records(connector_id)
        logger.info("TC-UNSHARE passed: %s appeared and left with its share", shared_folder)

    @pytest.mark.order(5)
    async def test_tc_activity_missing_still_syncs_new_files(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-ACTIVITY-MISSING: without the Activity app the drive is listed instead of read from the feed."""
        connector_id = nextcloud_connector["connector_id"]
        name = f"no-activity-{uuid.uuid4().hex[:6]}.md"
        await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        _occ_or_skip(nextcloud_source, "app:disable", "activity")
        try:
            nextcloud_source.put(f"{nextcloud_connector['folder']}/{name}", "# Written while Activity is off")
            await sync_until_names_visible(pipeshub_client, graph_provider, connector_id, [name])
        finally:
            nextcloud_source.occ("app:enable", "activity")

    @pytest.mark.order(6)
    async def test_tc_fullsync_outage_unlinks_nothing(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FULLSYNC-OUTAGE: a full sync that cannot list the drive leaves every record reachable."""
        connector_id = nextcloud_connector["connector_id"]
        await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        linked = await graph_provider.count_records(connector_id, scoped=True)
        assert linked > 0

        _occ_or_skip(nextcloud_source, "maintenance:mode", "--on")
        try:
            pipeshub_client.resync_connector(connector_id, full_sync=True)
            await wait_for_sync_completion(
                pipeshub_client, graph_provider, connector_id, timeout=DEFAULT_SYNC_TIMEOUT_SEC
            )
        finally:
            nextcloud_source.occ("maintenance:mode", "--off")

        after = await graph_provider.count_records(connector_id, scoped=True)
        assert after == linked, (
            f"TC-FULLSYNC-OUTAGE: {linked - after} record(s) lost their link to the drive after a failed full sync"
        )

        # Once Nextcloud is back, a full sync finishes and everything is still there.
        pipeshub_client.resync_connector(connector_id, full_sync=True)
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=DEFAULT_SYNC_TIMEOUT_SEC)
        assert await graph_provider.count_records(connector_id, scoped=True) == linked
        logger.info("TC-FULLSYNC-OUTAGE passed: %d records stayed linked", linked)
