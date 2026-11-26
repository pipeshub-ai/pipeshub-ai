/**
 * chat-interactions-group.spec.ts — UXINT-2 (gallery §2): a recorded 7-wonders run, 14 approvals in 5 batches,
 * replayed as the projector's frames into the real chat page.
 *
 * - Each batch is one card at its step: "Generate 7 images?" lists the seven prompts and offers Allow all 7.
 * - One click on "Allow all N" posts one request naming the N calls (DECIDE409); each is journaled and audited on its own.
 * - The run completes with 5 clicks for 14 asks; the header count drops as batches are decided.
 * - Only one card is a call to action at a time; decided batches fold to one receipt line each.
 * - DOM order of the cards is the journal order; nothing hangs below the answer.
 * Desktop, phone (390 px: 44 px targets, no sideways scroll, a long list folds after 3) and dark.
 *
 * UXINT_SHOTS=<dir> also writes the gallery's after-shots there (evidence/uxint/after-c/).
 */
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { openConversation } from './durable-conversation.mock';
import { ASKS, BATCHES, evidenceFonts, WONDERS, WONDERS_ANSWER, WondersRun } from './uxint-replay';

const SHOTS = process.env.UXINT_SHOTS;
async function shot(page: Page, name: string) {
  await page.waitForTimeout(400);
  await page.screenshot({ path: test.info().outputPath(`${name}.png`) });
  if (SHOTS) {
    // Evidence only: the dev server's own overlay is not part of the page, and icons need their font.
    await page.addStyleTag({ content: 'nextjs-portal { display: none !important; }' });
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: `${SHOTS}/${name}.png` });
  }
}
const scrollBottom = (page: Page) =>
  page.evaluate(() => {
    for (const el of document.querySelectorAll('*')) {
      if (el.scrollHeight > el.clientHeight + 20 && /auto|scroll/.test(getComputedStyle(el).overflowY)) el.scrollTop = el.scrollHeight;
    }
  });

const TITLES = ['Generate an image?', 'Search the web 3 times?', 'Search the web 2 times?', 'Read a web page?', 'Generate 7 images?'];

for (const view of [
  { tag: 'desktop', width: 1280, height: 900, dark: false },
  { tag: 'phone', width: 390, height: 844, dark: false },
  { tag: 'dark', width: 1280, height: 900, dark: true },
] as const) {
  test.describe(`Chat — the 7-wonders run, one card per batch (UXINT-2, ${view.tag})`, () => {
    test.use({ viewport: { width: view.width, height: view.height }, colorScheme: view.dark ? 'dark' : 'light' });

    test(`14 asks in 5 batches take 5 clicks; each batch is one card, then one receipt (${view.tag})`, async ({ page }) => {
      test.setTimeout(180_000);
      const run = new WondersRun(`conv-uxintc-wonders-${view.tag}`, `run-uxintc-wonders-${view.tag}`);
      await run.mock(page);
      await evidenceFonts(page);
      await openConversation(page, run.sessionId);
      const tag = `w-${view.tag}`;
      let clicks = 0;

      for (let b = 0; b < BATCHES.length; b++) {
        const n = BATCHES[b].length;
        const active = page.locator('[data-interaction-active="true"]');
        await expect(active).toHaveCount(1, { timeout: 30_000 });
        await expect(active.getByTestId('interaction-heading')).toHaveText(TITLES[b]);
        // One call to action: every earlier batch is a receipt, nothing else offers a button.
        await expect(page.getByTestId('interaction-receipt')).toHaveCount(b);
        // WP-71: the header counts the calls waiting, the n the card's "Allow all n" decides.
        await expect(page.getByTestId('interaction-waiting-count')).toHaveText(`${n} waiting for you`);
        if (n > 1) await expect(active.getByTestId('approval-group-item')).toHaveCount(view.tag === 'phone' && n > 3 ? 3 : n);
        await scrollBottom(page);
        await shot(page, `${tag}-b${b + 1}-pending-${n}`);

        if (view.tag === 'phone') {
          const sizes = await active.getByRole('button').evaluateAll((els) => els.map((e) => e.getBoundingClientRect().height));
          expect(Math.min(...sizes)).toBeGreaterThanOrEqual(44);
          expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
          if (n > 3) {
            await active.getByRole('button', { name: `+${n - 3} more` }).click();
            await expect(active.getByTestId('approval-group-item')).toHaveCount(n);
          }
        }

        const before = run.decisions.length;
        await active.getByRole('button', { name: n > 1 ? `Allow all ${n}` : /^Allow once: / }).click();
        clicks += 1;
        await expect.poll(() => run.decisions.length).toBe(before + n);
        if (n > 1 && b === 1) {
          await scrollBottom(page);
          await shot(page, `${tag}-b${b + 1}-after-allow-all-${n}`);
        }
      }

      await expect(page.getByText(WONDERS_ANSWER, { exact: true })).toBeVisible({ timeout: 30_000 });
      expect(clicks).toBe(BATCHES.length);
      expect(run.batchRequests).toHaveLength(BATCHES.filter((b) => b.length > 1).length);
      expect(run.decisions).toHaveLength(ASKS);
      expect(run.decisions).toEqual(BATCHES.flat().map((c) => `ix.approve.${c.id}`));
      // The steps stay open where the person decided: five receipts, in journal order, above the answer.
      const receipts = page.getByTestId('interaction-receipt');
      await expect(receipts).toHaveCount(BATCHES.length);
      await expect(receipts.last()).toHaveText(/Generate 7 images · Allowed all 7 by you/);
      const ys = await receipts.evaluateAll((els) => els.map((e) => e.getBoundingClientRect().top));
      expect([...ys].sort((a, b) => a - b)).toEqual(ys);
      const answerY = await page.getByText(WONDERS_ANSWER, { exact: true }).evaluate((e) => e.getBoundingClientRect().top);
      expect(Math.max(...ys)).toBeLessThan(answerY);
      await expect(page.locator('[data-interaction-active="true"]')).toHaveCount(0);
      await expect(page.getByTestId('interaction-waiting-count')).toHaveCount(0);
      await scrollBottom(page);
      await shot(page, `${tag}-99-final`);
    });
  });
}

test.describe('Chat — reviewing a batch one by one (UXINT-2)', () => {
  test('"Review each" decides one call at a time; a denied one says so on its row', async ({ page }) => {
    test.setTimeout(120_000);
    const run = new WondersRun('conv-uxintc-review', 'run-uxintc-review');
    await run.mock(page);
    // The decision route says "denied" for Petra so the row's receipt reads as the server decided.
    await page.route(`**/api/v1/conversations/${run.sessionId}/runs/${run.runId}/interactions/ix.approve.t7.1/decision`, (route) => {
      run.decisions.push('ix.approve.t7.1');
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ receipt: { commandId: 'c' }, interaction: { interactionId: 'ix.approve.t7.1', decision: 'denied' } }) });
    });
    await evidenceFonts(page);
    await openConversation(page, run.sessionId);
    for (const n of [1, 3, 2, 1]) {
      const active = page.locator('[data-interaction-active="true"]');
      await expect(active).toHaveCount(1, { timeout: 30_000 });
      await active.getByRole('button', { name: n > 1 ? `Allow all ${n}` : /^Allow once: / }).click();
    }
    const group = page.locator('[data-interaction-active="true"]');
    await expect(group.getByTestId('interaction-heading')).toHaveText('Generate 7 images?', { timeout: 30_000 });
    await group.getByTestId('review-each').click();
    await group.getByRole('button', { name: `Deny: A photorealistic picture of ${WONDERS[1]}` }).click();
    await expect.poll(() => run.decisions.includes('ix.approve.t7.1')).toBe(true);
    const petra = group.getByTestId('approval-group-item').nth(1);
    await expect(petra.getByTestId('interaction-receipt')).toHaveText(/Denied by you · nothing was sent/);
    await expect(group.getByTestId('approval-group-item').nth(0).getByRole('button', { name: /^Allow once/ })).toBeVisible();
  });
});
