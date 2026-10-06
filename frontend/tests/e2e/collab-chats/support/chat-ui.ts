import { expect, type Page } from '@playwright/test';
import type { Actor } from './stack';

export const SYNC_BUDGET_MS = 20_000;

export async function openChat(page: Page, conversationId: string): Promise<void> {
  await page.goto(`/chat/?conversationId=${conversationId}`);
  await expect(page.getByTestId('user-query-heading').first()).toBeVisible({ timeout: 30_000 });
}

export const composer = (page: Page) => page.getByTestId('chat-composer');

/** The composer's text, whether it is the textarea (flag off) or the rich editor (mentions flag on, as in this lane). */
export async function expectComposerText(page: Page, text: string): Promise<void> {
  await expect
    .poll(() => composer(page).evaluate((el) => (el instanceof HTMLTextAreaElement ? el.value : (el as HTMLElement).innerText.replace(/\n$/, ''))))
    .toBe(text);
}

export async function send(page: Page, text: string): Promise<void> {
  await composer(page).fill(text);
  await page.getByRole('button', { name: 'Send message' }).click();
}

/** The share drawer as A uses it: pick the person, set the level, optionally a note, share. */
export async function openShareDrawer(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Share', exact: true }).first().click();
  await expect(page.getByRole('dialog', { name: 'Share chat' })).toBeVisible();
}

export async function shareWith(
  page: Page,
  person: Actor,
  opts: { level: 'Can continue' | 'Can view'; note?: string },
): Promise<void> {
  const dialog = page.getByRole('dialog', { name: 'Share chat' });
  await dialog.getByPlaceholder(/Emails, teams or names/).fill(person.email);
  await dialog.getByRole('checkbox', { name: person.name === 'Writer' ? 'User Writer' : `User ${person.name}` }).click();
  if (opts.level !== 'Can view') {
    await dialog.getByRole('button', { name: /Can view/ }).click();
    await page.getByRole('menuitemradio', { name: new RegExp(opts.level) }).click();
  }
  if (opts.note) await dialog.getByLabel('Message to recipients (optional)').fill(opts.note);
  const shared = page.waitForResponse((r) => r.url().includes('/collaborators') && r.request().method() === 'PUT');
  await dialog.getByRole('button', { name: 'Share', exact: true }).click();
  expect((await shared).status()).toBe(200);
}

/** The author chip of the turn whose question is `question` (the chip sits beside the question heading, so take the nearest ancestor that holds one). */
export const authorChipOf = (page: Page, question: string) =>
  page.getByTestId('user-query-heading').filter({ hasText: question }).locator("xpath=ancestor::*[.//*[@data-testid='author-chip']][1]").getByTestId('author-chip');
