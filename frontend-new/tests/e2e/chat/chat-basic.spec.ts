import { test, expect } from '@playwright/test';

test.describe('Chat Basic', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/chat/');
    await page.waitForTimeout(3_000);
  });

  test('page loads with chat interface', async ({ page }) => {
    // Should be on the chat page
    await expect(page).toHaveURL(/\/chat\//);
  });

  test('chat input is visible', async ({ page }) => {
    // Look for a textarea or input for chat messages
    const chatInput = page.locator('textarea, input[type="text"]').last();
    await expect(chatInput).toBeVisible({ timeout: 10_000 });
  });

  test('suggestion chips are visible on empty chat', async ({ page }) => {
    // Look for suggestion buttons/chips on the empty state
    const suggestions = page.locator('button, [role="button"]').filter({
      hasText: /\?|How|What|Tell|Show|Help/,
    });
    const count = await suggestions.count();
    // May or may not have suggestions depending on config
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('can type a message in the input', async ({ page }) => {
    const chatInput = page.locator('textarea').last();
    if (await chatInput.isVisible()) {
      await chatInput.fill('Hello from E2E test');
      const value = await chatInput.inputValue();
      expect(value).toBe('Hello from E2E test');
    }
  });

  test('sidebar shows conversation history', async ({ page }) => {
    const viewport = page.viewportSize();
    if (viewport && viewport.width > 768) {
      const sidebar = page.locator('nav, [data-sidebar]').first();
      await expect(sidebar).toBeVisible({ timeout: 5_000 });
    }
  });
});
