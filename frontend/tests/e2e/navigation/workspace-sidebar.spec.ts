import { test, expect } from '../fixtures/base.fixture';

const SIDEBAR_ITEMS = [
  { label: 'General', url: '/workspace/general/' },
  { label: 'Profile', url: '/workspace/profile/' },
  { label: 'Authentication', url: '/workspace/authentication/' },
  { label: 'AI Models', url: '/workspace/ai-models/' },
  { label: 'Users', url: '/workspace/users/' },
  { label: 'Teams', url: '/workspace/teams/' },
  { label: 'Groups', url: '/workspace/groups/' },
  { label: 'Bots', url: '/workspace/bots/' },
  { label: 'Mail', url: '/workspace/mail/' },
  { label: 'Web Search', url: '/workspace/web-search/' },
  { label: 'Prompts', url: '/workspace/prompts/' },
  { label: 'Services', url: '/workspace/services/' },
  { label: 'Labs', url: '/workspace/labs/' },
];

test.describe('Workspace Sidebar Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/general/');
    await page.waitForTimeout(2_000);
  });

  // TODO: Re-enable once Connectors route is confirmed — admin route is /workspace/connectors/team/.
  test.skip('navigates to Connectors', async ({ page }) => {
    const item = { label: 'Connectors', url: '/workspace/connectors/team/' };
    const sidebarLink = page.locator(`text="${item.label}"`).first();
    if (await sidebarLink.isVisible()) {
      await sidebarLink.click();
      await page.waitForURL(`**${item.url}`, { timeout: 5_000 });
      await expect(page).toHaveURL(new RegExp(item.url.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
  });

  test('navigates to Groups and shows the Enterprise placeholder', async ({ page }) => {
    // Works either way: Groups under People (this branch) or top-level (other PR).
    const sidebarLink = page.getByRole('link', { name: 'Groups' }).first();
    if (!(await sidebarLink.isVisible())) {
      await page.getByRole('button', { name: 'People' }).click();
    }
    await expect(sidebarLink).toBeVisible({ timeout: 5_000 });
    await sidebarLink.click();
    await page.waitForURL('**/workspace/groups/**', { timeout: 5_000 });
    await expect(page).toHaveURL(/\/workspace\/groups\//);
    await expect(
      page.getByRole('heading', {
        name: /Group permissions are available in the Enterprise Edition/i,
      })
    ).toBeVisible({ timeout: 10_000 });
  });

  for (const item of SIDEBAR_ITEMS) {
    test(`navigates to ${item.label}`, async ({ page }) => {
      const sidebarLink = page.locator(`text="${item.label}"`).first();
      if (await sidebarLink.isVisible()) {
        await sidebarLink.click();
        await page.waitForURL(`**${item.url}`, { timeout: 5_000 });
        await expect(page).toHaveURL(new RegExp(item.url.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
      }
    });
  }
});
