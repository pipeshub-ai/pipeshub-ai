import { test, expect } from '../fixtures/api-context.fixture';
import { postWithRetry } from '../helpers/api-retry.helper';

const EMAIL_DOMAIN = 'e2etest.pipeshub.local';
const TOTAL_USERS = 30;
const UI_USERS = 5;
const API_USERS = TOTAL_USERS - UI_USERS;
const BATCH_SIZE = 25;

function generateEmail(index: number): string {
  return `e2e-user-${String(index).padStart(4, '0')}@${EMAIL_DOMAIN}`;
}

test.describe.serial('Seed Users', () => {
  test('create 5 users via UI (invite sidebar)', async ({ page }) => {
    await page.goto('/workspace/users/');
    await page.waitForSelector('[role="row"]', { timeout: 10_000 }).catch(() => {
      // Table may be empty initially — that's fine
    });

    // Click the "Invite Users" CTA button
    const ctaButton = page.locator('button').filter({ hasText: /Invite/ });
    await ctaButton.click();

    // Wait for the invite sidebar to appear
    await page.waitForTimeout(500);

    // Generate 5 test emails
    const emails = Array.from({ length: UI_USERS }, (_, i) => generateEmail(i + 1));

    // Find the tag input inside the invite dialog (placeholder disappears after first tag)
    const dialog = page.getByRole('dialog');
    const tagInput = dialog.getByRole('textbox').first();
    await tagInput.focus();

    // Type each email and press Enter
    for (const email of emails) {
      await tagInput.fill(email);
      await tagInput.press('Enter');
      await page.waitForTimeout(100);
    }

    // Click "Send Invite"
    const submitButton = page.locator('button').filter({ hasText: 'Send Invite' });
    await submitButton.first().click();

    // Wait for success feedback
    await page.waitForTimeout(2_000);
  });

  test('create 25 users via API in batches', async ({ apiContext }) => {
    test.setTimeout(5 * 60_000);

    const emails: string[] = [];
    for (let i = UI_USERS + 1; i <= TOTAL_USERS; i++) {
      emails.push(generateEmail(i));
    }

    // Send invites in batches of 100
    for (let offset = 0; offset < emails.length; offset += BATCH_SIZE) {
      const batch = emails.slice(offset, offset + BATCH_SIZE);
      const response = await postWithRetry(apiContext, '/api/v1/users/bulk/invite', { emails: batch });
      if (!response.ok()) {
        const body = await response.text();
        throw new Error(`POST /api/v1/users/bulk/invite failed [${response.status()}]: ${body}`);
      }
    }
  });

  test('verify user count via API', async ({ apiContext }) => {
    const response = await apiContext.get('/api/v1/users', {
      params: { limit: 1, page: 1 },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    const total = data.pagination?.totalCount ?? data.users?.length ?? 0;
    expect(total).toBeGreaterThanOrEqual(TOTAL_USERS);
  });
});
