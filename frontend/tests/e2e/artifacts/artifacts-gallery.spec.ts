import { test, expect } from '../fixtures/base.fixture';

test.describe('My artifacts gallery', () => {
  test('page loads without leaving the route @smoke', async ({ page }) => {
    await page.goto('/artifacts/');
    await expect(page).toHaveURL(/\/artifacts/);
    await expect(page.getByRole('heading', { name: 'My artifacts' })).toBeVisible({
      timeout: 30_000,
    });
  });

  test('nav keeps Collections, All Records and My artifacts visible @smoke', async ({ page }) => {
    await page.goto('/artifacts/');
    await expect(page.getByRole('link', { name: /Collections/i }).first()).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByRole('link', { name: /All Records/i }).first()).toBeVisible();
    await expect(page.getByRole('link', { name: /My artifacts/i }).first()).toBeVisible();
  });

  test('New Chat from the gallery opens chat @smoke', async ({ page }) => {
    await page.goto('/artifacts/');
    await page.getByRole('button', { name: /New Chat/i }).first().click();
    await expect(page).toHaveURL(/\/chat/, { timeout: 30_000 });
  });
});
