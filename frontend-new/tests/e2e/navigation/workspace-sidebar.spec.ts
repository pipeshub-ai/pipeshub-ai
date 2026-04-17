import { test, expect } from '@playwright/test';

const SIDEBAR_ITEMS = [
  { label: 'General', url: '/workspace/general/' },
  { label: 'Profile', url: '/workspace/profile/' },
  { label: 'Authentication', url: '/workspace/authentication/' },
  { label: 'Connectors', url: '/workspace/connectors/' },
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

  for (const item of SIDEBAR_ITEMS) {
    test(`navigates to ${item.label}`, async ({ page }) => {
      // Click sidebar item by text
      const sidebarLink = page.locator(`text="${item.label}"`).first();
      if (await sidebarLink.isVisible()) {
        await sidebarLink.click();
        await page.waitForURL(`**${item.url}`, { timeout: 5_000 });
        await expect(page).toHaveURL(new RegExp(item.url.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
      }
    });
  }
});
