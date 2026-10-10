import { test, expect } from './support/two-users.fixture';
import { test as visualTest, expect as visualExpect, type Page } from '@playwright/test';
import { fake, waitFor } from './support/stack';
import { composer, openChat, send } from './support/chat-ui';
import { COMBOS, VISUAL_ENABLED, makeCast, setCurrentCombo, shot } from './support/visual-m2';

/**
 * J-14: "Try again" on a failed answer and the "Answering {name}…" line. Python is the lane's fake: the failure and the
 * slow answer are scripted (`run_error`, `held_stream`). What is real here is Node's persisted failed turn, the sync that
 * shows another participant the run in progress, and the permission to retry.
 */
test.describe.configure({ mode: 'serial' });

const FAILURE = 'The model is unavailable';
const failedReply = (page: Page) => page.getByTestId('reply-message').filter({ has: page.getByTestId('answer-failed') });

test('J-14: a failed answer offers Try again, which reruns the question without asking it twice', { tag: '@collab' }, async ({ users }) => {
  const a = await users.fresh('J14a');
  await fake.script('chat_stream', { kind: 'run_error', message: FAILURE });
  const chat = await a.api.startChat('J14 question that fails');
  await openChat(a.page, chat);

  const failed = a.page.getByTestId('answer-failed');
  await expect(failed).toContainText('This answer failed', { timeout: 30_000 });
  await expect(failed).toContainText(FAILURE);
  await expect(failedReply(a.page).getByTestId('message-time')).toBeVisible();
  await expect(failed.getByRole('button', { name: 'Try again' })).toBeVisible();

  await fake.script('chat_stream', { kind: 'stream_answer', text: 'Recovered on the second try', runId: 'run-retry' });
  const mark = await fake.mark();
  await failed.getByRole('button', { name: 'Try again' }).click();

  await expect(a.page.getByText('Recovered on the second try')).toBeVisible({ timeout: 40_000 });
  await expect(a.page.getByTestId('answer-failed-retry')).toHaveCount(0);
  const sent = await fake.requests(['chat_stream'], mark);
  expect(sent).toHaveLength(1);
  expect((sent[0].body as { query: string }).query).toBe('J14 question that fails');
  await expect(a.page.getByTestId('human-message').filter({ hasText: 'J14 question that fails' })).toHaveCount(1);
});

test('J-14: someone who may not regenerate the turn is not offered Try again', { tag: '@collab' }, async ({ users }) => {
  const a = await users.fresh('J14b');
  const { b, c } = users;
  await fake.script('chat_stream', { kind: 'run_error', message: FAILURE });
  const chat = await a.api.startChat('J14 failed for the owner');
  await a.api.share(chat, b.actor, 'write');
  await a.api.share(chat, c.actor, 'read');

  await openChat(a.page, chat);
  await expect(a.page.getByTestId('answer-failed-retry')).toBeVisible({ timeout: 30_000 });
  for (const other of [b, c]) {
    await openChat(other.page, chat);
    await expect(other.page.getByTestId('answer-failed')).toContainText('This answer failed', { timeout: 30_000 });
    await expect(other.page.getByTestId('answer-failed-retry')).toHaveCount(0);
  }
});

test('J-14: B sees "Answering {name}…" while A\'s run streams, and it goes when the run is done; A never sees it', { tag: '@collab' }, async ({ users }) => {
  const { a, b } = users;
  const chat = await a.api.startChat('J14 first question');
  await a.api.share(chat, b.actor, 'write');
  await openChat(a.page, chat);
  await openChat(b.page, chat);

  const gate = `j14-${Date.now()}`;
  await fake.script('chat_stream', { kind: 'held_stream', gate, text: 'Slowly answering the second question', runId: 'run-j14' });
  await send(a.page, 'J14 second question');
  await waitFor('the run to reach the gate', () => fake.gateReached(gate));

  const line = b.page.getByTestId('answering-line');
  await expect(line).toContainText('Answering User Owner', { timeout: 20_000 });
  await expect(a.page.getByTestId('answering-line')).toHaveCount(0);

  await fake.openGate(gate);
  await expect(b.page.getByText('Slowly answering the second question')).toBeVisible({ timeout: 40_000 });
  await expect(line).toHaveCount(0);
  await expect(a.page.getByTestId('answering-line')).toHaveCount(0);
  await expect(composer(b.page)).toBeVisible();
});

visualTest.describe('J-14 visual', () => {
  visualTest.skip(!VISUAL_ENABLED, 'screenshots: set PCC_VISUAL_TOUR=1');
  visualTest.describe.configure({ mode: 'serial' });
  for (const combo of COMBOS) {
    visualTest(`${combo.viewport} ${combo.theme}`, async ({ browser }) => {
      visualTest.setTimeout(240_000);
      setCurrentCombo(combo);
      await fake.reset();
      const owner = await fake.freshActor(`j14v${Date.now().toString(36)}`);
      const cast = makeCast(browser, combo, { owner });
      try {
        const alice = await cast.open('owner');
        await fake.script('chat_stream', { kind: 'run_error', message: FAILURE });
        const chat = await alice.api.startChat('J14 visual question');
        await alice.page.goto(`/chat/?conversationId=${chat}`);
        await visualExpect(alice.page.getByTestId('answer-failed-retry')).toBeVisible({ timeout: 30_000 });
        await shot(alice.page, 'j14a', 'failed-try-again', 'A failed answer in the reply block with the reason, the time and Try again.');

        const bob = await cast.open('write_recipient');
        await alice.api.share(chat, bob.actor, 'write');
        await fake.script('chat_stream', { kind: 'held_stream', gate: `j14v-${combo.viewport}-${combo.theme}`, text: 'Streaming a longer answer', runId: 'run-v' });
        await bob.page.goto(`/chat/?conversationId=${chat}`);
        await visualExpect(bob.page.getByTestId('answer-failed')).toBeVisible({ timeout: 30_000 });
        await alice.page.getByTestId('answer-failed-retry').click();
        await visualExpect(bob.page.getByTestId('answering-line')).toBeVisible({ timeout: 30_000 });
        await shot(bob.page, 'j14c', 'answering-line', 'Bob while Alice\'s run streams: "Answering User Owner…" in the reply header.');
        await fake.openGate(`j14v-${combo.viewport}-${combo.theme}`);
        await visualExpect(bob.page.getByTestId('answering-line')).toHaveCount(0, { timeout: 40_000 });
      } finally {
        await cast.close();
        await fake.reset();
      }
    });
  }
});
