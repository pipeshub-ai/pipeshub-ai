import { test, expect } from './support/two-users.fixture';
import { AI_ROUTES, fake, type NodeApi } from './support/stack';
import { SYNC_BUDGET_MS, composer, openChat } from './support/chat-ui';
import { expectNoBlockingViolations } from './support/a11y';
import { chatTitle, shareChat } from './support/collab.helper';

/**
 * J-12: mentions. The composer mechanics (popover, keyboard, escaping) are `mentions-composer.spec.ts`; this journey is
 * the people-facing result: a human-only mention is a note (no AI call), the mentioned person gets a bell entry and
 * nobody else does, and the `@assistant` alias asks the AI. The AI is the lane's fake, so only whether it was asked is
 * asserted, never wording.
 */
test.describe.configure({ mode: 'serial' });

const STREAM_URL = /\/conversations\/[0-9a-f]{24}\/messages\/stream$/;
const NOTE_URL = /\/conversations\/[0-9a-f]{24}\/notes$/;

const mentioned = async (api: NodeApi) =>
  (((await api.call('GET', '/api/v1/notifications?limit=30')).body.notifications ?? []) as { type: string }[]).filter((n) => n.type === 'chat.mentioned');

test('J-12: B mentions A in a note, A gets a bell entry, nobody asks the AI; @assistant then runs the AI', { tag: '@collab' }, async ({ users }) => {
  const a = await users.fresh('J12');
  const { b, c } = users;
  const chat = await a.api.startChat(chatTitle('j12'));
  await shareChat(a.api, chat, { userId: b.actor.userId, level: 'write' });
  await shareChat(a.api, chat, { userId: c.actor.userId, level: 'read' });
  await openChat(a.page, chat);
  await openChat(b.page, chat);

  const streamed: string[] = [];
  b.page.on('request', (r) => {
    if (r.method() === 'POST' && STREAM_URL.test(r.url())) streamed.push(r.url());
  });
  const mark = await fake.mark();
  const ownerName = `User ${a.actor.name}`;

  // B types @, picks A, writes the note.
  const box = composer(b.page);
  await box.click();
  await b.page.keyboard.type('@');
  const list = b.page.getByRole('listbox', { name: 'Mention suggestions' });
  await expect(list).toBeVisible();
  await list.getByRole('option').filter({ hasText: ownerName }).click();
  await b.page.keyboard.type('can you approve the launch checklist?');
  const posted = b.page.waitForResponse((r) => r.request().method() === 'POST' && NOTE_URL.test(r.url()));
  await b.page.getByRole('button', { name: 'Send message' }).click();
  expect((await posted).status()).toBe(201);

  // A note, not a question: the bubble is on both screens, the AI was not asked, and no answer follows it.
  const noteOnB = b.page.getByTestId('note-bubble');
  await expect(noteOnB).toContainText('can you approve the launch checklist?');
  const noteOnA = a.page.getByTestId('note-bubble');
  await expect(noteOnA).toBeVisible({ timeout: SYNC_BUDGET_MS });
  await expect(noteOnA.getByTestId('author-chip')).toContainText(`User ${b.actor.name}`);
  await expectNoBlockingViolations(a.page, 'note-bubble');
  expect(streamed).toEqual([]);
  expect(await fake.requests(AI_ROUTES, mark)).toEqual([]);

  // A's bell: one chat.mentioned (outbox, then consumer), none for B, the author, or C, who was not named.
  await expect.poll(async () => (await mentioned(a.api)).length, { timeout: 40_000 }).toBe(1);
  expect(await mentioned(b.api)).toEqual([]);
  expect(await mentioned(c.api)).toEqual([]);
  await a.page.goto('/chat/');
  const inbox = a.page.getByText('Inbox', { exact: true });
  await expect(inbox).toBeVisible({ timeout: 20_000 });
  await inbox.click();
  const row = a.page.getByText('You were mentioned').first();
  await expect(row).toBeVisible({ timeout: 20_000 });
  await expectNoBlockingViolations(a.page, 'inbox-mentioned');

  // @assistant is the one way to ask the AI in a shared chat.
  await fake.script('chat_stream', { kind: 'stream_answer', text: 'Here is the recap' });
  await composer(b.page).click();
  await b.page.keyboard.type('@assistant recap the thread');
  const sent = b.page.waitForRequest((r) => r.method() === 'POST' && STREAM_URL.test(r.url()));
  await b.page.getByRole('button', { name: 'Send message' }).click();
  const body = (await sent).postDataJSON() as { mentions?: unknown };
  expect(body.mentions).toEqual([{ type: 'assistant', id: 'self' }]);
  await expect(b.page.getByText('Here is the recap')).toBeVisible({ timeout: 30_000 });
  expect(await fake.requests(['chat_stream'], mark)).toHaveLength(1);
  await expect(b.page.getByTestId('note-bubble')).toHaveCount(1);
  expect(await mentioned(a.api)).toHaveLength(1);
});

test('J-12: a reader cannot mention anyone into the chat: no composer, so no note', { tag: '@collab' }, async ({ users }) => {
  const a = await users.fresh('J12r');
  const { c } = users;
  const chat = await a.api.startChat(chatTitle('j12-reader'));
  await shareChat(a.api, chat, { userId: c.actor.userId, level: 'read' });
  await openChat(c.page, chat);
  await expect(composer(c.page)).toHaveCount(0);
  const refused = await c.api.call('POST', `/api/v1/conversations/${chat}/notes`, {
    query: 'sneaky',
    mentions: [{ type: 'user', id: a.actor.userId }],
    clientMessageId: 'n-reader',
  });
  expect(refused.status).toBe(403);
});
