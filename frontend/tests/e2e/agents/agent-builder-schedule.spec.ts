import { test, expect } from '../fixtures/base.fixture';

test.describe('Agent Builder Scheduled Trigger', () => {
  test('shows scheduled input card in the builder palette', async ({ page }) => {
    await page.goto('/agents/new/');

    await page.waitForTimeout(2000);

    const scheduledCard = page.getByText(/Scheduled input|Zeitgesteuerte Eingabe/i).first();
    await expect(scheduledCard).toBeVisible();
  });
});
