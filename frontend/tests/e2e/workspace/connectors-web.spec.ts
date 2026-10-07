/**
 * Connecting a data source through the connectors screens, end to end.
 *
 * Nothing is mocked: an admin sets up the Web connector in the UI against the
 * integration stack's `web-fixtures` site, starts the sync, and the test waits
 * for the site's pages to be synced by the real backend. The Web connector needs
 * no outside account, so this runs anywhere the integration stack runs.
 */
import { test, expect } from '../fixtures/api-context.fixture';

// Where the connector (inside the stack) reaches the fixture site.
const FIXTURE_SITE = (process.env.WEB_FIXTURES_CONNECTOR_URL ?? 'http://web-fixtures:8080').replace(/\/$/, '');
const START_URL = `${FIXTURE_SITE}/site/docs/`;
// The five pages under site/docs (see integration-tests/connectors/web/conftest.py).
const MIN_SYNCED_PAGES = 5;

type ConnectorSummary = { _key: string; name: string; isActive?: boolean };

test.describe('Connect a data source (Web connector)', () => {
  let connectorId: string | undefined;

  test.afterEach(async ({ apiContext }) => {
    if (connectorId) {
      const removed = await apiContext.delete(`/api/v1/connectors/${connectorId}`);
      expect(
        removed.ok() || removed.status() === 404,
        `deleting the test connector failed: ${removed.status()} ${await removed.text()}`,
      ).toBe(true);
      connectorId = undefined;
    }
  });

  test('an admin sets up a Web connector and its pages are synced', async ({ page, apiContext }) => {
    test.setTimeout(240_000);
    const instanceName = `e2e-web-${Date.now().toString(36)}`;

    await page.goto('/workspace/connectors/team/');
    await page.getByPlaceholder('Search...').fill('Web');
    const card = page.getByTestId('connector-card-Web');
    await expect(card).toBeVisible({ timeout: 20_000 });
    // "+ Setup" on a fresh stack; "+" (add another instance) once one exists.
    await card.getByTestId('connector-setup').or(card.getByTestId('connector-add-instance')).click();

    // Authenticate step: the Web connector needs no credentials, only a name.
    await expect(page.getByText('No authentication required for this connector')).toBeVisible();
    await page.getByPlaceholder('e.g. Production Slack').fill(instanceName);
    await page.getByRole('button', { name: 'Next →' }).click();

    // Next creates the instance; keep its id so teardown can remove it.
    await expect
      .poll(async () => {
        const res = await apiContext.get('/api/v1/connectors', { params: { scope: 'team', limit: 100 } });
        const body = (await res.json()) as { connectors?: ConnectorSummary[] };
        connectorId = body.connectors?.find((c) => c.name === instanceName)?._key;
        return connectorId;
      }, { message: 'the instance created by "Next" was not listed', timeout: 30_000 })
      .toBeTruthy();

    // Configure step: point it at the fixture site and save.
    await page.locator('input[type="url"]').fill(START_URL);
    await page.getByRole('button', { name: 'Save Configuration' }).click();
    await page.getByRole('button', { name: 'Confirm' }).click();
    await page.getByRole('button', { name: 'Start Syncing Now' }).click();

    // The new instance is listed as configured.
    const instance = page.locator('div').filter({ hasText: instanceName }).filter({ hasText: 'Complete' }).last();
    await expect(instance).toBeVisible({ timeout: 30_000 });

    // Sync is on, and the real backend synced the site's pages.
    await expect
      .poll(async () => {
        const res = await apiContext.get(`/api/v1/connectors/${connectorId}`);
        return ((await res.json()) as { connector?: ConnectorSummary }).connector?.isActive;
      }, { message: '"Start Syncing Now" should turn sync on', timeout: 30_000 })
      .toBe(true);
    await expect
      .poll(async () => {
        const res = await apiContext.get(`/api/v1/connectors/${connectorId}/stats`);
        const body = (await res.json()) as { data?: { stats?: { total?: number } } };
        return body.data?.stats?.total ?? 0;
      }, { message: `expected at least ${MIN_SYNCED_PAGES} synced pages from ${START_URL}`, timeout: 150_000, intervals: [5_000] })
      .toBeGreaterThanOrEqual(MIN_SYNCED_PAGES);
  });
});
