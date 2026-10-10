import { test, expect } from './support/two-users.fixture';
import { AI_ROUTES, fake, waitFor } from './support/stack';
import { composer, openChat, send } from './support/chat-ui';
import { expectNoBlockingViolations } from './support/a11y';

test.describe.configure({ mode: 'serial' });

test('J-04: B sees A streaming, queues a message, and it is sent once when A is done', { tag: '@collab' }, async ({ users }) => {
  const { a, b } = users;
  const chat = await a.api.startChat('A first question');
  await a.api.share(chat, b.actor, 'write');

  await openChat(a.page, chat);
  await openChat(b.page, chat);

  // Script, in the order Python is asked: A's held run, then B's answer.
  await fake.script(
    'chat_stream',
    { kind: 'held_stream', gate: 'a-run', text: 'A is still answering', runId: 'run-a' },
    { kind: 'stream_answer', text: 'Answer for B', runId: 'run-b' },
  );
  const mark = await fake.mark();

  await send(a.page, 'A second question');
  await waitFor('A run to reach the gate', () => fake.gateReached('a-run'));

  // B's next poll shows the busy banner naming A.
  const busy = b.page.getByTestId('busy-banner');
  await expect(busy).toContainText('User Owner is asking', { timeout: 20_000 });
  await expectNoBlockingViolations(b.page, 'chat-busy-banner');

  // B types and sends: the message is queued, not sent.
  await send(b.page, 'B queued message');
  await expect(busy).toContainText('Waiting for User Owner to finish', { timeout: 10_000 });
  expect(await fake.requests(AI_ROUTES, mark)).toHaveLength(1);

  // A finishes; B's message goes out by itself, once.
  await fake.openGate('a-run');
  await expect(b.page.getByText('Answer for B')).toBeVisible({ timeout: 40_000 });
  await expect(busy).toBeHidden();

  const asked = await fake.requests(['chat_stream'], mark);
  expect(asked.map((r) => r.userId)).toEqual([a.actor.userId, b.actor.userId]);
  expect((asked[1].body as { query: string }).query).toBe('B queued message');

  // Nothing is sent a second time.
  await b.page.waitForTimeout(6_000);
  expect(await fake.requests(['chat_stream'], mark)).toHaveLength(2);
  await expect(b.page.getByText('B queued message')).toHaveCount(1);
  await expect(composer(b.page)).toBeVisible();
});
