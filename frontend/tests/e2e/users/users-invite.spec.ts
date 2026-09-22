import { test, expect } from '../fixtures/api-context.fixture';
import { ensureSmtpConfigured } from '../helpers/smtp.helper';
import { deleteUserByEmail } from '../helpers/members.helper';

test.describe('Users Invite', () => {
  test.beforeEach(async ({ page, apiContext }) => {
    // The Invite button stays disabled until SMTP is configured. Relying on
    // another suite to have configured it made these tests pass or fail
    // depending on what ran before them.
    test.skip(
      !(await ensureSmtpConfigured(apiContext)),
      'SMTP is not configured and SMTP_HOST / SMTP_PORT are not set',
    );
    await page.goto('/workspace/users/');
    await page.waitForTimeout(3_000);
  });

  test('opens invite sidebar when clicking CTA', async ({ page }) => {
    const ctaButton = page.locator('button').filter({ hasText: /Invite/ });
    await ctaButton.first().click();
    await page.waitForTimeout(500);

    // The invite dialog should now be visible
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible({ timeout: 3_000 });
  });

  test('invite single email via tag input', async ({ page }) => {
    const ctaButton = page.locator('button').filter({ hasText: /Invite/ });
    await ctaButton.first().click();
    await page.waitForTimeout(500);

    const dialog = page.getByRole('dialog');
    const tagInput = dialog.getByRole('textbox').first();
    await tagInput.fill('e2e-invite-test@e2etest.pipeshub.local');
    await tagInput.press('Enter');

    // A tag pill should appear
    await page.waitForTimeout(300);
    const closePills = page.locator('span.material-icons-outlined').filter({ hasText: 'close' });
    const pillCount = await closePills.count();
    expect(pillCount).toBeGreaterThanOrEqual(1);
  });

  test('invite multiple emails via tag input', async ({ page }) => {
    const ctaButton = page.locator('button').filter({ hasText: /Invite/ });
    await ctaButton.first().click();
    await page.waitForTimeout(500);

    const dialog = page.getByRole('dialog');
    const tagInput = dialog.getByRole('textbox').first();

    const emails = [
      'e2e-multi-1@e2etest.pipeshub.local',
      'e2e-multi-2@e2etest.pipeshub.local',
      'e2e-multi-3@e2etest.pipeshub.local',
    ];

    for (const email of emails) {
      await tagInput.fill(email);
      await tagInput.press('Enter');
      await page.waitForTimeout(100);
    }

    const closePills = page.locator('span.material-icons-outlined').filter({ hasText: 'close' });
    const pillCount = await closePills.count();
    expect(pillCount).toBeGreaterThanOrEqual(3);
  });

  test('an invalid email alone cannot be sent', async ({ page }) => {
    const ctaButton = page.locator('button').filter({ hasText: /Invite/ });
    await ctaButton.first().click();

    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible({ timeout: 5_000 });
    const tagInput = dialog.getByRole('textbox').first();
    await tagInput.fill('not-a-valid-email');
    await tagInput.press('Enter');

    await expect(dialog.getByRole('button', { name: 'Send Invite' })).toBeDisabled();
  });

  test('submit invite sends invitations and lists the invited user', async ({ page, apiContext }) => {
    // Unique per run: re-inviting an address that is already pending is a different flow.
    const email = `e2e-submit-${Date.now()}@e2etest.pipeshub.local`;

    try {
      const ctaButton = page.locator('button').filter({ hasText: /Invite/ });
      await ctaButton.first().click();

      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible({ timeout: 5_000 });
      const tagInput = dialog.getByRole('textbox').first();
      await tagInput.fill(email);
      await tagInput.press('Enter');

      const submitButton = dialog.getByRole('button', { name: 'Send Invite' });
      await expect(submitButton).toBeEnabled();
      await submitButton.click();

      await expect(page.getByText('Invite sent!').first()).toBeVisible({ timeout: 15_000 });
      await page.reload();
      await expect(page.getByText(email).first(), 'the invited user should be listed').toBeVisible({
        timeout: 15_000,
      });
    } finally {
      await deleteUserByEmail(apiContext, email);
    }
  });
});
