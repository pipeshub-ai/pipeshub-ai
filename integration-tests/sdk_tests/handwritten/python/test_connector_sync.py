"""Enable an RSS connector, wait for its sync, then use what it indexed."""

import os
import time
import uuid

from pipeshub_sdk import Pipeshub

from .errors import raises_status

# The integration stack's web-fixtures feed, so the sync does not depend on the internet.
FEED_URL = os.environ.get(
    "PIPESHUB_SDK_TEST_RSS_FEED_URL", "http://web-fixtures:8080/feeds/news.xml"
)
SYNC_TIMEOUT_SECONDS = 300


def wait_for_idle(pipeshub: Pipeshub, connector_id: str) -> None:
    deadline = time.monotonic() + SYNC_TIMEOUT_SECONDS
    while True:
        connector = pipeshub.connector_instances.get_connector_instance(
            connector_id=connector_id
        ).connector
        if (
            connector.is_active
            and not connector.is_locked
            and connector.status == "IDLE"
        ):
            return
        assert time.monotonic() < deadline, f"the sync did not finish: {connector}"
        time.sleep(2)


def test_handwritten_connector_sync(admin: Pipeshub):
    created = admin.connector_instances.create_connector_instance(
        connector_type="RSS",
        # Connector names are unique in an org.
        instance_name=f"sdk-test-rss-{uuid.uuid4()}",
        scope="team",
        auth_type="NONE",
        config={
            "sync": {
                "feed_urls": FEED_URL,
                "max_articles_per_feed": 1,
                "fetch_full_content": False,
            }
        },
    )
    connector_id = created.connector.connector_id
    try:
        # RSS has no auth, so its saved settings are enough for filter options, but none of
        # them come from the service. It cannot resync before it is enabled.
        filters = admin.connector_filters.get_connector_filters(
            connector_id=connector_id
        )
        assert filters.success is True
        assert filters.filter_options == {}
        with raises_status(404):
            admin.connector_filters.get_filter_field_options(
                connector_id=connector_id, filter_key="sdk_test_no_such_filter"
            )
        with raises_status(400):
            admin.connector_instances.resync_connector(
                connector_id=connector_id, connector_name="RSS"
            )

        toggled = admin.connector_control.toggle_connector(
            connector_id=connector_id, type_="sync"
        )
        assert toggled.success is True
        wait_for_idle(admin, connector_id)

        stats = admin.connector_instances.get_connector_stats(connector_id=connector_id)
        assert stats.data.connector_id == connector_id

        # The listing declares a Retry-After header, so the SDK wraps its body in `result`.
        app = admin.knowledge_hub.get_knowledge_hub_child_nodes(
            parent_type="app", parent_id=connector_id
        ).result
        feed = next(
            (item for item in app.items if item.node_type == "recordGroup"), None
        )
        assert feed is not None, "the sync made no record group"
        reindexed = admin.knowledge_base.reindex_record_group(
            record_group_id=feed.id, depth=0
        )
        assert reindexed.record_group_id == feed.id

        group = admin.knowledge_hub.get_knowledge_hub_child_nodes(
            parent_type="recordGroup", parent_id=feed.id
        ).result
        article = next(
            (
                item
                for item in group.items
                if item.node_type == "record" and item.web_url
            ),
            None,
        )
        assert article is not None, "the sync made no record with a URL"

        lookup = admin.connector.lookup_record_by_identifier(
            identifiers=[article.web_url]
        )
        assert article.id in [match.id for match in lookup.matches]

        content = admin.connector.get_record_content(record_id=article.id)
        assert article.id in content.content

        resynced = admin.connector_instances.resync_connector(
            connector_id=connector_id, connector_name="RSS"
        )
        assert resynced.resync_connector_response.success is True

        stopped = admin.connector_instances.stop_connector_sync(
            connector_id=connector_id
        )
        assert stopped.success is True
    finally:
        admin.connector_instances.delete_connector_instance(connector_id=connector_id)
