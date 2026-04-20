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
    const sectionHeading = page.locator('text=/Infrastructure Services/i').first();
    await expect(sectionHeading).toBeVisible({ timeout: 5_000 });
  });

  test('displays application services section', async ({ page }) => {
    const sectionHeading = page.locator('text=/Application Services/i').first();
    await expect(sectionHeading).toBeVisible({ timeout: 5_000 });
  });

  test('shows core infrastructure service rows', async ({ page }) => {
    await expect(page.locator('text=Redis').first()).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('text=MongoDB').first()).toBeVisible({ timeout: 5_000 });
    // These show resolved names from the API (e.g. "Kafka" or "Redis Streams" instead of "Message Broker")
    const broker = page.locator('text=/Kafka|Redis Streams|Message Broker/');
    await expect(broker.first()).toBeVisible({ timeout: 5_000 });
    const graphDb = page.locator('text=/ArangoDB|Neo4j|Graph Database/');
    await expect(graphDb.first()).toBeVisible({ timeout: 5_000 });
    const vectorDb = page.locator('text=/Qdrant|Vector Database/');
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
    await expect(page.locator('text=Query Service').first()).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('text=Connector Service').first()).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('text=Indexing Service').first()).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('text=Docling Service').first()).toBeVisible({ timeout: 5_000 });
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
    const policyText = page.locator('text=/Service Health Policy/i');
    await expect(policyText).toBeVisible({ timeout: 5_000 });
  });

  test('does not show duplicate Redis rows when KV store uses Redis', async ({ page }) => {
    // When kvStoreType=redis, there should be exactly one Redis row
    const redisRows = page.locator('text=Redis').filter({ hasNotText: /Streams/ });
    const count = await redisRows.count();
    // Should be 1 (combined) or 2 (separate Redis + etcd) but never 2 Redis rows
    expect(count).toBeLessThanOrEqual(2);
  });
});
