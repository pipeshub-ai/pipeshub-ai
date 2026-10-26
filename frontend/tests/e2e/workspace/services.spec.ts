import { test, expect } from '@playwright/test';

test.describe('Workspace Services', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/services/');
    await page.waitForTimeout(3_000);
  });

  test('page loads with services heading', async ({ page }) => {
    const heading = page.locator('text=/Services/i').first();
    await expect(heading).toBeVisible({ timeout: 5_000 });
  });

  test('displays infrastructure services section', async ({ page }) => {
    const sectionHeading = page.locator('text=/Infrastructure services/i').first();
    await expect(sectionHeading).toBeVisible({ timeout: 5_000 });
  });

  test('displays application services section', async ({ page }) => {
    const sectionHeading = page.locator('text=/Application services/i').first();
    await expect(sectionHeading).toBeVisible({ timeout: 5_000 });
  });

  test('shows core infrastructure service rows @smoke', async ({ page }) => {
    await expect(page.locator('text=Redis').first()).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('text=MongoDB').first()).toBeVisible({ timeout: 5_000 });
    // These show resolved names from the API (e.g. "Kafka" or "Redis Streams" instead of "Message broker")
    const broker = page.locator('text=/Kafka|Redis Streams|Message broker/');
    await expect(broker.first()).toBeVisible({ timeout: 5_000 });
    const graphDb = page.locator('text=/ArangoDB|Neo4j|Graph database/');
    await expect(graphDb.first()).toBeVisible({ timeout: 5_000 });
    const vectorDb = page.locator('text=/Qdrant|Vector database/');
    await expect(vectorDb.first()).toBeVisible({ timeout: 5_000 });
  });

  test('shows dynamic service names from deployment config', async ({ page }) => {
    // The message broker should resolve to either "Kafka" or "Redis Streams"
    const brokerName = page.locator('text=/Kafka|Redis Streams/');
    await expect(brokerName.first()).toBeVisible({ timeout: 5_000 });

    // The graph DB should resolve to either "ArangoDB" or "Neo4j"
    const graphDbName = page.locator('text=/ArangoDB|Neo4j/');
    await expect(graphDbName.first()).toBeVisible({ timeout: 5_000 });
  });

  test('shows application service rows', async ({ page }) => {
    await expect(page.locator('text=Query service').first()).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('text=Connector service').first()).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('text=Indexing service').first()).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('text=Docling service').first()).toBeVisible({ timeout: 5_000 });
  });

  test('shows health status badges', async ({ page }) => {
    const badges = page.locator('text=/Healthy|Unhealthy|Unknown/');
    await expect(badges.first()).toBeVisible({ timeout: 5_000 });
    const count = await badges.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test('check now button is visible and clickable', async ({ page }) => {
    const refreshButton = page.locator('button').filter({ hasText: /Check Now/i });
    await expect(refreshButton).toBeVisible({ timeout: 5_000 });
    await refreshButton.click();

    const checkingText = page.locator('button').filter({ hasText: /Checking/i });
    await expect(
      refreshButton.or(checkingText)
    ).toBeVisible({ timeout: 5_000 });
  });

  test('displays last checked timestamp', async ({ page }) => {
    await page.waitForTimeout(2_000);
    const timestamp = page.locator('text=/Last checked/i');
    await expect(timestamp).toBeVisible({ timeout: 5_000 });
  });

  test('displays service health policy info', async ({ page }) => {
    const policyText = page.locator('text=/Service health policy/i');
    await expect(policyText).toBeVisible({ timeout: 5_000 });
  });

  test('does not show duplicate Redis rows when KV store uses Redis', async ({ page }) => {
    // When kvStoreType=redis, there should be exactly one Redis row
    const redisRows = page.locator('text=Redis').filter({ hasNotText: /Streams/ });
    const count = await redisRows.count();
    // Should be 1 (combined) or 2 (separate Redis + etcd) but never 2 Redis rows
    expect(count).toBeLessThanOrEqual(2);
  });

  test('redis row label reflects the configured redis mode', async ({ page }) => {
    // deployment.redisMode from /api/v1/health (health.routes.ts) is
    // 'standalone' by default; the row appends '(Cluster)' / '(MemoryDB)'
    // only when the backend reports one of those modes.
    const redisRow = page
      .locator('text=/^Redis( \\((Cluster|MemoryDB)\\))?$/')
      .first();
    await expect(redisRow).toBeVisible({ timeout: 5_000 });
  });
});

// ---------------------------------------------------------------------------
// Deterministic redis mode label, with /api/v1/health mocked
// ---------------------------------------------------------------------------
//
// The test above asserts against whatever mode the live backend under test
// happens to be running in. These mock the health endpoint's `deployment`
// field directly so the "MemoryDB" (and "Cluster") label is exercised on
// every CI run, regardless of what redis mode is actually configured for
// the E2E environment.

const MOCK_HEALTH_SERVICES_RESPONSE = {
  status: 'healthy',
  services: { query: 'healthy', connector: 'healthy', indexing: 'healthy', docling: 'healthy' },
};

async function mockHealthWithRedisMode(
  page: import('@playwright/test').Page,
  redisMode: string | undefined,
): Promise<void> {
  await page.route('**/api/v1/health', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'healthy',
        services: {
          redis: 'healthy',
          mongodb: 'healthy',
          messageBroker: 'healthy',
          graphDb: 'healthy',
          vectorDb: 'healthy',
        },
        serviceNames: {},
        deployment: {
          kvStoreType: 'redis',
          messageBrokerType: 'kafka',
          graphDbType: 'neo4j',
          vectorDbType: 'qdrant',
          redisMode,
        },
      }),
    }),
  );
  await page.route('**/api/v1/health/services', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_HEALTH_SERVICES_RESPONSE),
    }),
  );
}

test.describe('Workspace Services - Redis mode label (mocked deployment)', () => {
  test('shows "Redis (MemoryDB)" when the backend reports redisMode=memorydb', async ({
    page,
  }) => {
    await mockHealthWithRedisMode(page, 'memorydb');
    await page.goto('/workspace/services/');
    await page.waitForTimeout(1_500);

    await expect(page.locator('text=Redis (MemoryDB)').first()).toBeVisible({
      timeout: 5_000,
    });
  });

  test('shows "Redis (Cluster)" when the backend reports redisMode=cluster', async ({
    page,
  }) => {
    await mockHealthWithRedisMode(page, 'cluster');
    await page.goto('/workspace/services/');
    await page.waitForTimeout(1_500);

    await expect(page.locator('text=Redis (Cluster)').first()).toBeVisible({
      timeout: 5_000,
    });
  });

  test('shows plain "Redis" when the backend omits redisMode', async ({ page }) => {
    await mockHealthWithRedisMode(page, undefined);
    await page.goto('/workspace/services/');
    await page.waitForTimeout(1_500);

    const redisRow = page.locator('text=/^Redis$/').first();
    await expect(redisRow).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('text=Redis (MemoryDB)')).toHaveCount(0);
    await expect(page.locator('text=Redis (Cluster)')).toHaveCount(0);
  });
});
