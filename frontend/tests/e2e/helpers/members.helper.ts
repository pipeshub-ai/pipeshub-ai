import { expect, type APIRequestContext, type Browser, type Page } from '@playwright/test';
import { randomBytes } from 'crypto';
import { inviteLinkPath, waitForEmailTo } from './mailpit.helper';
import { loginViaUI } from './login.helper';

export type Member = { email: string; password: string };

/** A fresh address per run: re-inviting a pending address is a different flow. */
export function uniqueMemberEmail(label: string): string {
  return `e2e-${label}-${Date.now()}@e2etest.pipeshub.local`;
}

/** Meets the password policy: length, upper, lower, digit and symbol. */
function newPassword(): string {
  return `E2e-${randomBytes(6).toString('hex')}!Aa1`;
}

export async function findUserId(apiContext: APIRequestContext, email: string): Promise<string | undefined> {
  const list = await apiContext.get('/api/v1/users', { params: { search: email, page: 1, limit: 10 } });
  if (!list.ok()) throw new Error(`listing users to find ${email} failed [${list.status()}]: ${await list.text()}`);
  const users: Array<{ _id?: string; userId?: string; email?: string }> = (await list.json()).users ?? [];
  const user = users.find((u) => u.email === email);
  if (!user) return undefined;
  const id = user._id ?? user.userId;
  // Without an id a DELETE would hit /undefined, get a 404, and pass as "already gone".
  if (!id) throw new Error(`user ${email} has no id in the list response: ${JSON.stringify(user)}`);
  return id;
}

/** Soft-delete the user with this email, if one exists (the invite may have failed). */
export async function deleteUserByEmail(apiContext: APIRequestContext, email: string): Promise<void> {
  const id = await findUserId(apiContext, email);
  if (!id) return;
  const response = await apiContext.delete(`/api/v1/users/${id}`);
  if (!response.ok() && response.status() !== 404) {
    throw new Error(`deleting user ${email} failed [${response.status()}]: ${await response.text()}`);
  }
}

export async function inviteByApi(apiContext: APIRequestContext, email: string): Promise<void> {
  const response = await apiContext.post('/api/v1/users/bulk/invite', { data: { emails: [email] } });
  if (!response.ok()) throw new Error(`inviting ${email} failed [${response.status()}]: ${await response.text()}`);
}

/**
 * Accept an invite the way a person does: open the link from the email in a
 * browser with nobody signed in, choose a password, sign in, and give the full
 * name the app asks every new member for before it lets them in.
 */
export async function acceptInvite(browser: Browser, email: string, fullName = 'E2E Teammate'): Promise<Member> {
  const link = inviteLinkPath(await waitForEmailTo(email));
  const member = { email, password: newPassword() };
  const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  try {
    const page = await context.newPage();
    await page.goto(link);
    await page.locator('#new-password').fill(member.password);
    await page.locator('#confirm-password').fill(member.password);
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByText('Your password has been changed!')).toBeVisible({ timeout: 20_000 });

    await loginViaUI(page, member.email, member.password);
    const profile = page.getByRole('dialog', { name: 'Complete Your Profile' });
    await expect(profile, 'a new member is asked for their name on first sign-in').toBeVisible({ timeout: 20_000 });
    await profile.getByPlaceholder('e.g. Jane Smith').fill(fullName);
    await profile.getByRole('button', { name: 'Save & continue' }).click();
    await expect(profile).toBeHidden({ timeout: 15_000 });
  } finally {
    await context.close();
  }
  return member;
}

/** A separate, signed-in browser session for `member`. Close its context when done. */
export async function signInAs(browser: Browser, member: Member): Promise<Page> {
  const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const page = await context.newPage();
  await loginViaUI(page, member.email, member.password);
  return page;
}
