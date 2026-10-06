import { test, expect } from './support/two-users.fixture';
import { fake } from './support/stack';
import { expectNoBlockingViolations } from './support/a11y';
import { authorChipOf, composer, openChat, openShareDrawer, send, shareWith, SYNC_BUDGET_MS } from './support/chat-ui';

test.describe.configure({ mode: 'serial' });

test('J-03: A shares with B (can continue), B sends, history is intact and the turn carries B as author', { tag: '@collab' }, async ({ users }) => {
  const { a, b } = users;
  const chat = await a.api.startChat('A first question');

  await openChat(a.page, chat);
  await openShareDrawer(a.page);
  await expectNoBlockingViolations(a.page, 'share-drawer');
  await shareWith(a.page, b.actor, { level: 'Can continue', note: 'please take over' });
  await expect(a.page.getByRole('dialog', { name: 'Share chat' }).getByText('User Writer')).toBeVisible();

  await fake.script('chat_stream', { kind: 'stream_answer', text: 'Answer for B' });
  const mark = await fake.mark();
  await openChat(b.page, chat);
  await expect(b.page.getByText('A first question')).toBeVisible();
  await expect(b.page.getByText('Fake answer')).toBeVisible();
  await expect(composer(b.page)).toBeVisible();

  await send(b.page, 'B follow-up');
  await expect(b.page.getByText('Answer for B')).toBeVisible({ timeout: 30_000 });

  // B's turn is B's: "You" for B, "User Writer" for everyone else, and A's earlier turn keeps A.
  await expect(authorChipOf(b.page, 'B follow-up')).toHaveAttribute('aria-label', 'Sent by You');
  await expect(authorChipOf(b.page, 'A first question')).toHaveAttribute('aria-label', 'Sent by User Owner');

  // A's open tab picks B's turn up by sync, with B's name on it and A's own turn still A's.
  await expect(a.page.getByText('Answer for B')).toBeVisible({ timeout: SYNC_BUDGET_MS });
  await expect(authorChipOf(a.page, 'B follow-up')).toHaveAttribute('aria-label', 'Sent by User Writer');
  await expect(authorChipOf(a.page, 'A first question')).toHaveAttribute('aria-label', 'Sent by You');

  // Python was asked once, as B.
  const asked = await fake.requests(['chat_stream'], mark);
  expect(asked).toHaveLength(1);
  expect(asked[0].userId).toBe(b.actor.userId);
});
