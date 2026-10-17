/**
 * web-search.spec.ts
 *
 * Workspace → Web Search: adding self-hosted providers and what a page reader
 * may and may not do. The settings API is mocked with page.route(), so no
 * provider server is needed; the signed-in user must be an admin, as for every
 * workspace settings spec.
 */

import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';

const API = '/api/v1/configurationManager/web-search';

interface Provider {
  providerKey: string;
  provider: string;
  configuration: Record<string, string>;
  isDefault: boolean;
}

async function mockWebSearchApi(page: Page, providers: Provider[]) {
  const posted: Record<string, unknown>[] = [];
  await page.route((url) => url.pathname.replace(/\/$/, '') === API, (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'success', providers, settings: { includeImages: false, maxImages: 3 } }),
    });
  });
  await page.route(`**${API}/providers`, (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    const body = route.request().postDataJSON() as { provider: string; configuration: Record<string, string> };
    posted.push(body);
    providers.push({ providerKey: `key-${body.provider}`, provider: body.provider, configuration: body.configuration, isDefault: false });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success' }) });
  });
  return posted;
}

/** The innermost element holding a provider's configure button: its row. */
function row(page: Page, label: string) {
  return page
    .locator('div')
    .filter({ has: page.getByRole('button', { name: new RegExp(`^(Configure|Edit configuration): ${label}$`) }) })
    .last();
}

test.describe('Workspace — web search providers (mocked API)', () => {
  test('adding SearXNG asks for its server address and sends it trimmed', async ({ page }) => {
    const posted = await mockWebSearchApi(page, []);
    await page.goto('/workspace/web-search/');

    await page.getByRole('button', { name: 'Configure: SearXNG' }).click();
    const save = page.getByRole('button', { name: 'Save', exact: true });
    await expect(page.getByText('Server URL')).toBeVisible();
    await expect(page.getByPlaceholder(/API key/i)).toHaveCount(0);
    await expect(save).toBeDisabled();

    await page.getByPlaceholder('http://searxng:8080').fill('  http://searxng:8080  ');
    await expect(save).toBeEnabled();
    await save.click();

    await expect.poll(() => posted.length).toBe(1);
    expect(posted[0]).toMatchObject({ provider: 'searxng', configuration: { baseUrl: 'http://searxng:8080' } });
    await expect(page.getByRole('button', { name: 'Edit configuration: SearXNG' })).toBeVisible();
  });

  test('a page reader cannot be the search default', async ({ page }) => {
    await mockWebSearchApi(page, [
      { providerKey: 'key-crawl4ai', provider: 'crawl4ai', configuration: { baseUrl: 'http://crawl4ai:11235' }, isDefault: false },
      { providerKey: 'key-searxng', provider: 'searxng', configuration: { baseUrl: 'http://searxng:8080' }, isDefault: false },
    ]);
    await page.goto('/workspace/web-search/');

    await expect(row(page, 'Crawl4AI')).toBeVisible();
    await expect(row(page, 'Crawl4AI').getByRole('button', { name: 'Set as default' })).toHaveCount(0);
    await expect(row(page, 'SearXNG').getByRole('button', { name: 'Set as default' })).toBeVisible();
  });

  test('Crawl4AI needs its server address before it can be saved', async ({ page }) => {
    await mockWebSearchApi(page, []);
    await page.goto('/workspace/web-search/');

    await page.getByRole('button', { name: 'Configure: Crawl4AI' }).click();
    const save = page.getByRole('button', { name: 'Save', exact: true });
    await expect(save).toBeDisabled();
    await page.getByPlaceholder('http://crawl4ai:11235').fill('http://crawl4ai:11235');
    await expect(save).toBeEnabled();
  });
});
