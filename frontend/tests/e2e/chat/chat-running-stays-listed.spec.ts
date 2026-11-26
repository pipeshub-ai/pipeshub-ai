/**
 * FIX-RAIL1: a chat still answering stays in the chat list when the person starts a new chat, and its answer is
 * whole when they go back to it. On the harness lane's scripted model: the first turn holds its model call, so the
 * run is mid-answer while the new chat opens.
 */
import { randomBytes } from 'crypto';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { chatInput, lane, script, ScriptedModel, untilHolding, type Lane } from '../harness/support/lane';

let l: Lane;
let model: ScriptedModel;

test.describe.configure({ mode: 'serial' });

test.beforeAll(async () => {
  l = lane();
  model = await ScriptedModel.open(l);
});
test.afterAll(async () => {
  if ((await model.holding().catch(() => false))) await model.release();
  await model.close();
});

const conversationIdOf = (page: Page): string => new URL(page.url()).searchParams.get('conversationId') as string;

test('FIX-RAIL1: classic: a running chat stays in the sidebar after New chat, and its answer is whole', async ({ page }) => {
  await model.reset();
  const tag = randomBytes(3).toString('hex');
  const question = `Classic question ${tag}`;
  const answer = `Classic answer ${tag} is complete.`;
  await page.goto('/chat/');
  await expect(chatInput(page)).toBeVisible({ timeout: 30_000 });
  await chatInput(page).fill(script(question, 'hold', `say ${answer}`));
  await chatInput(page).press('Enter');
  await expect(page).toHaveURL(/conversationId=/, { timeout: 60_000 });
  const firstId = conversationIdOf(page);
  await untilHolding(model);

  const row = page.locator(`a[href*="conversationId=${firstId}"]`);
  // Recents starts folded in the classic sidebar.
  await page.getByText('Recents', { exact: true }).click();
  await expect(row, 'the running chat is listed while it answers').toBeVisible({ timeout: 30_000 });

  await page.keyboard.press('Control+Shift+K');
  await expect(page).not.toHaveURL(/conversationId=/);
  await expect(page.getByText('What do you want to explore today?'), 'New chat opens the new-chat page').toBeVisible();
  await expect(row, 'the running chat stays listed after New chat').toBeVisible();

  await model.release();
  await row.click();
  await expect(page).toHaveURL(new RegExp(`conversationId=${firstId}`));
  await expect(page.getByText(answer, { exact: true })).toBeVisible({ timeout: 90_000 });
  await expect(row, 'the finished chat is still listed').toBeVisible();
});
