import { randomUUID } from "node:crypto";
import { expect, test } from "vitest";
import type { Pipeshub } from "../../index.js";
import { createClient } from "./client.js";
import { expectStatus } from "./errors.js";

// The integration stack's web-fixtures feed, so the sync does not depend on the internet.
const FEED_URL =
  process.env["PIPESHUB_SDK_TEST_RSS_FEED_URL"] ?? "http://web-fixtures:8080/feeds/news.xml";
const SYNC_TIMEOUT_MS = 300_000;

async function waitForIdle(pipeshub: Pipeshub, connectorId: string): Promise<void> {
  const deadline = Date.now() + SYNC_TIMEOUT_MS;
  for (;;) {
    const { connector } = await pipeshub.connectorInstances.getConnectorInstance({ connectorId });
    if (connector.isActive && !connector.isLocked && connector.status === "IDLE") {
      return;
    }
    if (Date.now() > deadline) {
      throw new Error(`the sync did not finish: ${JSON.stringify(connector)}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 2_000));
  }
}

test("Handwritten Connector Sync", async () => {
  const pipeshub = createClient();

  const created = await pipeshub.connectorInstances.createConnectorInstance({
    connectorType: "RSS",
    // Connector names are unique in an org.
    instanceName: `sdk-test-rss-${randomUUID()}`,
    scope: "team",
    authType: "NONE",
    config: {
      sync: { feed_urls: FEED_URL, max_articles_per_feed: 1, fetch_full_content: false },
    },
  });
  const connectorId = created.connector.connectorId;
  try {
    // RSS has no auth, so its saved settings are enough for filter options, but none of them
    // come from the service. It cannot resync before it is enabled.
    const filters = await pipeshub.connectorFilters.getConnectorFilters({ connectorId });
    expect(filters.success).toBe(true);
    expect(filters.filterOptions).toEqual({});
    await expectStatus(
      pipeshub.connectorFilters.getFilterFieldOptions({
        connectorId,
        filterKey: "sdk_test_no_such_filter",
      }),
      404,
    );
    await expectStatus(
      pipeshub.connectorInstances.resyncConnector({ connectorId, body: { connectorName: "RSS" } }),
      400,
    );

    const toggled = await pipeshub.connectorControl.toggleConnector({
      connectorId,
      body: { type: "sync" },
    });
    expect(toggled.success).toBe(true);
    await waitForIdle(pipeshub, connectorId);

    const stats = await pipeshub.connectorInstances.getConnectorStats({ connectorId });
    expect(stats.data.connectorId).toEqual(connectorId);

    const app = await pipeshub.knowledgeHub.getKnowledgeHubChildNodes({
      parentType: "app",
      parentId: connectorId,
    });
    const feed = app.items.find((item) => item.nodeType === "recordGroup");
    expect(feed, "the sync made no record group").toBeDefined();
    const reindexed = await pipeshub.knowledgeBase.reindexRecordGroup({
      recordGroupId: feed!.id,
      body: { depth: 0 },
    });
    expect(reindexed.recordGroupId).toEqual(feed!.id);

    const group = await pipeshub.knowledgeHub.getKnowledgeHubChildNodes({
      parentType: "recordGroup",
      parentId: feed!.id,
    });
    const article = group.items.find((item) => item.nodeType === "record" && item.webUrl);
    expect(article, "the sync made no record with a URL").toBeDefined();

    const lookup = await pipeshub.connector.lookupRecordByIdentifier({
      identifiers: [article!.webUrl!],
    });
    expect(lookup.matches.map((match) => match.id)).toContain(article!.id);

    const content = await pipeshub.connector.getRecordContent({ recordId: article!.id });
    expect(content.content).toContain(article!.id);

    const resynced = await pipeshub.connectorInstances.resyncConnector({
      connectorId,
      body: { connectorName: "RSS" },
    });
    expect(resynced.resyncConnectorResponse.success).toBe(true);

    const stopped = await pipeshub.connectorInstances.stopConnectorSync({ connectorId });
    expect(stopped.success).toBe(true);
  } finally {
    await pipeshub.connectorInstances.deleteConnectorInstance({ connectorId });
  }
});
