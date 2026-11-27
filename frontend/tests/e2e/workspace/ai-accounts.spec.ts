/**
 * ai-accounts.spec.ts — U2 PW-0 on the e2e lane (deployment/e2e/run.sh): the
 * real Node routes. "My AI accounts" sets no model key of the person's own:
 * runs use the models the admin configured in AI Models, shared with everyone.
 */
import type { Request } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';

test.describe('AI accounts (U2)', () => {
  test('PW-0: the page offers no key of the person\'s own and asks for none', async ({ page }) => {
    const asked: string[] = [];
    page.on('request', (request: Request) => {
      if (request.url().includes('/model-accounts/me')) asked.push(request.url());
    });
    await page.goto('/workspace/ai-accounts/');
    await expect(page.getByRole('heading', { name: 'My AI accounts' })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole('table', { name: 'What you can use' })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole('button', { name: /Add .* key/ })).toHaveCount(0);
    await expect(page.getByText('No key added')).toHaveCount(0);
    expect(asked).toEqual([]);
    const answer = await page.request.get('/api/v1/agent-harness/model-accounts/me');
    expect(answer.status()).toBe(404);
  });
});
