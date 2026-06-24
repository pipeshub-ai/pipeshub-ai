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
