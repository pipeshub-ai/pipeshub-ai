/**
 * Inviting a teammate, end to end: an admin sends the invite from the Users
 * page, the invitee opens the link in the email, sets a password, signs in and
 * gives their name, then can sign in again straight to chat.
 *
 * Nothing is mocked. The email is read back from Mailpit, the SMTP sink the
 * integration stack runs, so a broken invite template, a wrong link, or a
 * token the reset page rejects all fail here.
 */
import { test, expect } from '../fixtures/api-context.fixture';
import { ensureSmtpConfigured } from '../helpers/smtp.helper';
import { acceptInvite, deleteUserByEmail, signInAs, uniqueMemberEmail } from '../helpers/members.helper';

test.describe('Invite a teammate', () => {
  let email: string | undefined;

  test.beforeEach(async ({ apiContext }) => {
    test.skip(
      !(await ensureSmtpConfigured(apiContext)),
      'SMTP is not configured and SMTP_HOST / SMTP_PORT are not set',
    );
  });

  test.afterEach(async ({ apiContext }) => {
    if (email) await deleteUserByEmail(apiContext, email);
    email = undefined;
  });

  test('an invited user accepts from the email, sets a password and signs in @smoke', async ({ page, browser, apiContext }) => {
    test.setTimeout(180_000);
    const invitee = uniqueMemberEmail('accept');
    email = invitee;

    await page.goto('/workspace/users/');
    await page.getByRole('button', { name: /Invite/ }).first().click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    const tagInput = dialog.getByRole('textbox').first();
    await tagInput.fill(invitee);
    await tagInput.press('Enter');
    await dialog.getByRole('button', { name: 'Send Invite' }).click();
    await expect(page.getByText('Invite sent!').first()).toBeVisible({ timeout: 15_000 });

    const member = await acceptInvite(browser, invitee);

    // Signing in again goes straight to chat: the name was saved, so no prompt this time.
    const memberPage = await signInAs(browser, member);
    try {
      await expect(memberPage).toHaveURL(/\/chat\//);
      await expect(memberPage.locator('textarea').last()).toBeVisible({ timeout: 20_000 });
      // The prompt appears only after the profile loads, so let the page settle before checking.
      await memberPage.waitForLoadState('networkidle');
      await expect(memberPage.getByRole('dialog', { name: 'Complete Your Profile' })).toBeHidden();
    } finally {
      await memberPage.context().close();
    }

    // Admins see the invite as accepted, not still pending.
    await expect
      .poll(
        async () => {
          const res = await apiContext.get('/api/v1/users', { params: { search: invitee, page: 1, limit: 10 } });
          const users: Array<{ email?: string; hasLoggedIn?: boolean }> = (await res.json()).users ?? [];
          return users.find((u) => u.email === invitee)?.hasLoggedIn;
        },
        { message: 'the invite should show as accepted', timeout: 15_000 },
      )
      .toBe(true);
  });
});
