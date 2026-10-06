import { test, expect } from './support/two-users.fixture';
import { fake } from './support/stack';
import { expectNoBlockingViolations } from './support/a11y';
import { SYNC_BUDGET_MS, composer, openChat } from './support/chat-ui';

test.describe.configure({ mode: 'serial' });

const STREAM_URL = /\/conversations\/[0-9a-f]{24}\/messages\/stream$/;

test('mentions: typing @ lists the participants, a pick becomes a chip, and the send carries the mention', { tag: '@collab' }, async ({ users }) => {
  const { a, b } = users;
  const chat = await a.api.startChat('A first question');
  expect((await a.api.share(chat, b.actor, 'write')).status).toBe(200);

  await openChat(a.page, chat);
  const box = composer(a.page);
  await expect(box).toHaveJSProperty('isContentEditable', true);
  await box.click();
  await a.page.keyboard.type('@');

  const list = a.page.getByRole('listbox', { name: 'Mention suggestions' });
  await expect(list).toBeVisible();
  // The composer card has a backdrop-filter; a fixed-position anchor inside it once put the list off-screen.
  await expect(list).toBeInViewport({ ratio: 1 });
  const options = list.getByRole('option');
  await expect(options.first()).toHaveText('Assistant');
  await expect(options.filter({ hasText: 'User Writer' })).toHaveCount(1);
  await expectNoBlockingViolations(a.page, 'mention-popover');

  await a.page.keyboard.press('ArrowDown');
  const active = options.filter({ hasText: 'User Writer' });
  await expect(active).toHaveAttribute('aria-selected', 'true');
  await expect(box).toHaveAttribute('aria-activedescendant', (await active.getAttribute('id')) ?? '');
  await a.page.keyboard.press('Enter');

  await expect(list).toBeHidden();
  await expect(a.page.getByTestId('mention-chip')).toHaveText('@User Writer');


  await a.page.keyboard.type('can you take this? @assistant');
  await fake.script('chat_stream', { kind: 'stream_answer', text: 'Noted' });
  const mark = await fake.mark();
  const sent = a.page.waitForRequest((r) => r.method() === 'POST' && STREAM_URL.test(r.url()));
  await a.page.getByRole('button', { name: 'Send message' }).click();

  const body = (await sent).postDataJSON() as { query: string; mentions?: unknown };
  expect(body.mentions).toEqual([{ type: 'user', id: b.actor.userId }, { type: 'assistant', id: 'self' }]);
  expect(body.query).toBe(`<@user:${b.actor.userId}> can you take this? @assistant`);
  await expect(a.page.getByText('Noted')).toBeVisible({ timeout: 30_000 });

  // The same wire text is what Python is asked: the question with the stable token.
  const asked = await fake.requests(['chat_stream'], mark);
  expect(asked).toHaveLength(1);
  expect(JSON.stringify(asked[0].body)).toContain(`<@user:${b.actor.userId}>`);
});

test('mentions: Escape closes the popover without inserting, and Shift+Enter adds a line instead of sending', { tag: '@collab' }, async ({ users }) => {
  const { a } = users;
  const chat = await a.api.startChat('A first question');
  await openChat(a.page, chat);

  const box = composer(a.page);
  await box.click();
  await a.page.keyboard.type('hi @');
  const list = a.page.getByRole('listbox', { name: 'Mention suggestions' });
  await expect(list).toBeVisible();
  await a.page.keyboard.press('Escape');
  await expect(list).toBeHidden();
  await expect(a.page.getByTestId('mention-chip')).toHaveCount(0);
  await expect(box).toHaveText('hi @');

  await a.page.keyboard.press('Shift+Enter');
  await a.page.keyboard.type('second line');
  await expect(box.locator('br:not(.ProseMirror-trailingBreak)')).toHaveCount(1);
  await expect(a.page.getByText('A first question')).toBeVisible();
});

test('mentions: a typed or pasted <@...> token is sent escaped and carries no mention', { tag: '@collab' }, async ({ users }) => {
  const { a } = users;
  const chat = await a.api.startChat('A first question');
  await openChat(a.page, chat);

  const box = composer(a.page);
  await box.click();
  await a.page.keyboard.insertText('look at <@agent:xyz> please');
  await expect(box).toContainText('<@agent:xyz>');

  await fake.script('chat_stream', { kind: 'stream_answer', text: 'Seen' });
  const sent = a.page.waitForRequest((r) => r.method() === 'POST' && STREAM_URL.test(r.url()));
  await a.page.getByRole('button', { name: 'Send message' }).click();
  const body = (await sent).postDataJSON() as { query: string; mentions?: unknown };
  expect(body.query).toBe('look at <\\@agent:xyz> please');
  expect(body.mentions ?? []).toEqual([]);
  await expect(a.page.getByText('Seen')).toBeVisible({ timeout: 30_000 });
});

test('mentions: B mentions only A, so a note appears for both and no AI request is made', { tag: '@collab' }, async ({ users }) => {
  const { a, b } = users;
  const chat = await a.api.startChat('A first question');
  expect((await a.api.share(chat, b.actor, 'write')).status).toBe(200);
  await openChat(a.page, chat);
  await openChat(b.page, chat);

  const streamed: string[] = [];
  b.page.on('request', (r) => {
    if (r.method() === 'POST' && STREAM_URL.test(r.url())) streamed.push(r.url());
  });
  const ownerName = `User ${a.actor.name}`;

  const box = composer(b.page);
  await box.click();
  await b.page.keyboard.type('@');
  const list = b.page.getByRole('listbox', { name: 'Mention suggestions' });
  await expect(list).toBeVisible();
  await b.page.keyboard.press('ArrowDown');
  await expect(list.getByRole('option').filter({ hasText: ownerName })).toHaveAttribute('aria-selected', 'true');
  await b.page.keyboard.press('Enter');
  await b.page.keyboard.type('please review the thread');

  const mark = await fake.mark();
  const posted = b.page.waitForResponse((r) => r.request().method() === 'POST' && /\/conversations\/[0-9a-f]{24}\/notes$/.test(r.url()));
  await b.page.getByRole('button', { name: 'Send message' }).click();
  const response = await posted;
  expect(response.status()).toBe(201);
  expect(response.request().postDataJSON()).toMatchObject({
    query: `<@user:${a.actor.userId}> please review the thread`,
    mentions: [{ type: 'user', id: a.actor.userId }],
  });

  const noteOnB = b.page.getByTestId('note-bubble');
  await expect(noteOnB).toBeVisible();
  await expect(noteOnB).toContainText('please review the thread');
  await expect(noteOnB.getByTestId('mention-chip')).toHaveText(`@${ownerName}`);
  await expect(composer(b.page)).toHaveText('');

  const noteOnA = a.page.getByTestId('note-bubble');
  await expect(noteOnA).toBeVisible({ timeout: SYNC_BUDGET_MS });
  await expect(noteOnA).toContainText('please review the thread');
  await expect(noteOnA.getByTestId('author-chip')).toContainText('User Writer');

  expect(streamed).toEqual([]);
  expect(await fake.requests(['chat_stream'], mark)).toHaveLength(0);
});

test('mentions: B mentions @assistant, so the AI runs with the mention', { tag: '@collab' }, async ({ users }) => {
  const { a, b } = users;
  const chat = await a.api.startChat('A first question');
  expect((await a.api.share(chat, b.actor, 'write')).status).toBe(200);
  await openChat(b.page, chat);

  await fake.script('chat_stream', { kind: 'stream_answer', text: 'Here is the recap' });
  const mark = await fake.mark();
  await composer(b.page).click();
  await b.page.keyboard.type('@assistant recap the thread');
  const sent = b.page.waitForRequest((r) => r.method() === 'POST' && STREAM_URL.test(r.url()));
  await b.page.getByRole('button', { name: 'Send message' }).click();
  const body = (await sent).postDataJSON() as { query: string; mentions?: unknown };
  expect(body.mentions).toEqual([{ type: 'assistant', id: 'self' }]);
  expect(body.query).toBe('@assistant recap the thread');
  await expect(b.page.getByText('Here is the recap')).toBeVisible({ timeout: 30_000 });
  expect(await fake.requests(['chat_stream'], mark)).toHaveLength(1);
  await expect(b.page.getByTestId('note-bubble')).toHaveCount(0);
});

test('mentions: the list stays on screen while filtering, and a mouse pick inserts a chip', { tag: '@collab' }, async ({ users }) => {
  const { a, b } = users;
  const chat = await a.api.startChat('A first question');
  expect((await a.api.share(chat, b.actor, 'write')).status).toBe(200);
  await openChat(a.page, chat);
  await composer(a.page).click();
  await a.page.keyboard.type('ping @Wri');
  const list = a.page.getByRole('listbox', { name: 'Mention suggestions' });
  // The match and the closing "Add people to this chat…" row.
  await expect(list.getByRole('option')).toHaveCount(2);
  // The composer card has a backdrop-filter; a fixed-position anchor inside it once opened the list off-screen.
  await expect(list).toBeInViewport({ ratio: 1 });
  await list.getByRole('option').filter({ hasText: 'User Writer' }).click();
  await expect(list).toBeHidden();
  await expect(composer(a.page).getByTestId('mention-chip')).toHaveText('@User Writer');
});
