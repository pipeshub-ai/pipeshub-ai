/**
 * chat-allow-all-batch.spec.ts — DECIDE409: the owner's "Your decision could not be sent. Try again." on
 * "Allow all 7". The card posted seven decisions at once and they raced on the run's revision; one came back 409.
 *
 * - "Allow all 7" is one request naming the seven calls; no per-call decision is posted.
 * - A transient 409 on that request is retried once with the same body (the idempotency key replays it), and the
 *   card folds to its receipt with no error.
 * A recorded 7-wonders run replayed into the real chat page (as chat-interactions-group.spec.ts).
 */
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { openConversation } from './durable-conversation.mock';
import { BATCHES, WondersRun } from './uxint-replay';

const SEVEN = BATCHES[BATCHES.length - 1].map((c) => `ix.approve.${c.id}`);

/** Decides the four earlier batches, leaving "Generate 7 images?" as the active card. */
async function toTheSevenImages(page: Page) {
  for (const n of BATCHES.slice(0, -1).map((b) => b.length)) {
    const active = page.locator('[data-interaction-active="true"]');
    await expect(active).toHaveCount(1, { timeout: 30_000 });
    await active.getByRole('button', { name: n > 1 ? `Allow all ${n}` : /^Allow once: / }).click();
  }
  const card = page.locator('[data-interaction-active="true"]');
  await expect(card.getByTestId('interaction-heading')).toHaveText('Generate 7 images?', { timeout: 30_000 });
  return card;
}

test.describe('Chat — "Allow all 7" is one decision request (DECIDE409)', () => {
  test('one request names the seven calls; the card folds to "Allowed all 7"', async ({ page }) => {
    test.setTimeout(120_000);
    const run = new WondersRun('conv-decide409-one', 'run-decide409-one');
    await run.mock(page);
    await openConversation(page, run.sessionId);
    const card = await toTheSevenImages(page);
    const batchesBefore = run.batchRequests.length;
    const decisionsBefore = run.decisions.length;

    await card.getByRole('button', { name: 'Allow all 7' }).click();

    await expect.poll(() => run.batchRequests.length).toBe(batchesBefore + 1);
    expect(run.batchRequests.at(-1)).toEqual(SEVEN);
    expect(run.decisions.slice(decisionsBefore)).toEqual(SEVEN);
    await expect(page.getByTestId('interaction-receipt').last()).toHaveText(/Generate 7 images · Allowed all 7 by you/, { timeout: 30_000 });
    // Next's route announcer is an empty alert of its own: the card's error is looked for by its words.
    await expect(page.getByText('did not reach the server')).toHaveCount(0);
  });

  test('a 409 while the run moved is retried once; the receipt shows, not the error', async ({ page }) => {
    test.setTimeout(120_000);
    const run = new WondersRun('conv-decide409-retry', 'run-decide409-retry');
    await run.mock(page);
    const bodies: unknown[] = [];
    // Registered after the run's mock, so it is asked first: the first "Allow all" answers as an older server did.
    await page.route(`**/api/v1/conversations/${run.sessionId}/runs/${run.runId}/interactions/decisions`, (route) => {
      const body = route.request().postDataJSON() as { interactionIds: string[] };
      bodies.push(body);
      if (bodies.length === 1 && body.interactionIds.length === SEVEN.length) {
        return route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({ error: { code: 'HTTP_AGENT_HARNESS_REVISION_CONFLICT', message: 'Run revision changed' } }),
        });
      }
      return route.fallback();
    });
    await openConversation(page, run.sessionId);
    const card = await toTheSevenImages(page);
    bodies.length = 0;

    await card.getByRole('button', { name: 'Allow all 7' }).click();

    await expect(page.getByTestId('interaction-receipt').last()).toHaveText(/Generate 7 images · Allowed all 7 by you/, { timeout: 30_000 });
    expect(bodies).toHaveLength(2);
    expect(bodies[1]).toEqual(bodies[0]);
    await expect(page.getByText('did not reach the server')).toHaveCount(0);
    await expect(page.getByText('could not be sent')).toHaveCount(0);
  });
});
