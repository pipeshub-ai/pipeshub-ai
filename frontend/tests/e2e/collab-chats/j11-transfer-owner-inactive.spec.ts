import { test, expect } from './support/two-users.fixture';
import { AI_ROUTES, fake } from './support/stack';
import { composer, openChat, openShareDrawer, send } from './support/chat-ui';
import { expectNoBlockingViolations } from './support/a11y';
import { chatTitle, shareChat } from './support/collab.helper';

/**
 * J-11: ownership transfer and an inactive owner. Both run through the real Node API and the real UI; the API half
 * (the refusals, audit rows, offboarding) is PH-06's `integration_test_j11_transfer_owner_inactive.py`.
 */
test.describe.configure({ mode: 'serial' });

const INACTIVE = "The owner of this chat is no longer active, so it can't be continued. Ask an admin for help.";

test('J-11: A transfers the chat to B from the share drawer; B is the owner and A keeps Can continue', { tag: '@collab' }, async ({ users }) => {
  const a = await users.fresh('J11t');
  const { b } = users;
  const chat = await a.api.startChat(chatTitle('j11'));
  await shareChat(a.api, chat, { userId: b.actor.userId, level: 'write' });

  await openChat(a.page, chat);
  await openShareDrawer(a.page);
  const drawer = a.page.getByRole('dialog', { name: 'Share chat' });
  await expect(drawer.getByText(`User ${b.actor.name}`)).toBeVisible();
  await drawer.getByRole('button', { name: /Can continue/ }).first().click();
  await a.page.getByRole('menuitem', { name: 'Make owner' }).click();

  const confirm = a.page.getByRole('alertdialog').or(a.page.getByRole('dialog', { name: /owner\?/ }));
  await expect(confirm.first()).toContainText(`Make User ${b.actor.name} the owner?`);
  await expect(confirm.first()).toContainText("You'll stay on it as someone who can continue it.");
  await expectNoBlockingViolations(a.page, 'transfer-confirmation');
  const transferred = a.page.waitForResponse((r) => r.url().includes('/transfer-ownership') && r.request().method() === 'POST');
  await confirm.first().getByRole('button', { name: 'Make owner' }).click();
  expect((await transferred).status()).toBe(200);
  await expect(a.page.getByText('Ownership transferred').first()).toBeVisible();

  // Node agrees: B owns it, A is a direct editor and can no longer manage sharing.
  const view = await b.api.call('GET', `/api/v1/conversations/${chat}/collaborators`);
  expect(view.status).toBe(200);
  expect(view.body.owner.userId).toBe(b.actor.userId);
  const mine = await a.api.getChat(chat);
  expect(mine.body.conversation.access).toMatchObject({ isOwner: false, role: 'write', canSend: true, canManage: false });
  expect((await b.api.getChat(chat)).body.conversation.access).toMatchObject({ isOwner: true, role: 'owner' });

  // B's page: owner badge in the member list and the owner's panel; A's page: an editor, no Share button.
  await openChat(b.page, chat);
  await b.page.getByRole('button', { name: 'Who can access this chat' }).click();
  await expect(b.page.getByTestId('access-explain')).toContainText('You own this chat.', { timeout: 15_000 });
  await b.page.keyboard.press('Escape');
  await openShareDrawer(b.page);
  const bDrawer = b.page.getByRole('dialog', { name: 'Share chat' });
  await expect(bDrawer.getByText(`User ${a.actor.name}`)).toBeVisible();
  await expect(bDrawer.getByText('Owner', { exact: true }).first()).toBeVisible();
  await expectNoBlockingViolations(b.page, 'share-drawer-new-owner');

  await openChat(a.page, chat);
  await expect(composer(a.page)).toBeVisible();
  await expect(a.page.getByRole('button', { name: 'Share', exact: true })).toHaveCount(0);
});

test('J-11: when the owner is disabled, an editor sees the read-only OWNER_INACTIVE banner and nothing is sent', { tag: '@collab' }, async ({ users }) => {
  const a = await users.fresh('J11i');
  const { b } = users;
  const chat = await a.api.startChat(chatTitle('j11-inactive'));
  await shareChat(a.api, chat, { userId: b.actor.userId, level: 'write' });
  await fake.setDisabled(a.actor.userId, true);

  // The detail still says the editor may send; the readiness check on open turns it into the banner.
  const ready = await b.api.call('GET', `/api/v1/conversations/${chat}/readiness`);
  expect(ready.body).toEqual({ canSend: false, reasons: ['OWNER_INACTIVE'] });
  await openChat(b.page, chat);
  await expect(b.page.getByTestId('read-only-banner')).toContainText(INACTIVE);
  await expect(composer(b.page)).toHaveCount(0);
  await expect(b.page.getByText(chatTitle('j11-inactive'))).toBeVisible();
  await expectNoBlockingViolations(b.page, 'owner-inactive-banner');

  // Forced through the API, the send is refused with the same code and Python is never asked.
  const mark = await fake.mark();
  const forced = await b.api.call('POST', `/api/v1/conversations/${chat}/messages`, { query: 'owner is gone', chatMode: 'quick' });
  expect(forced.status).toBe(403);
  expect(forced.body.error.code).toBe('OWNER_INACTIVE');
  expect(await fake.requests(AI_ROUTES, mark)).toEqual([]);
  // The history stays readable.
  expect((await b.api.getChat(chat)).status).toBe(200);
});

test('J-11: a send into an inactive owner\'s chat from a stale tab turns the page read-only too', { tag: '@collab' }, async ({ users }) => {
  const a = await users.fresh('J11s');
  const { b } = users;
  const chat = await a.api.startChat(chatTitle('j11-stale'));
  await shareChat(a.api, chat, { userId: b.actor.userId, level: 'write' });
  // The tab asks the readiness route once on open; a disable that lands before that answer is not a stale tab.
  const checked = b.page.waitForResponse((r) => r.url().includes(`/conversations/${chat}/readiness`));
  await openChat(b.page, chat);
  expect((await (await checked).json()).reasons).toEqual([]);
  await expect(composer(b.page)).toBeVisible();

  // The owner is disabled after B's tab checked, so it still offers the composer.
  await fake.setDisabled(a.actor.userId, true);
  const mark = await fake.mark();
  await send(b.page, 'still there?');
  await expect(b.page.getByTestId('read-only-banner')).toContainText(INACTIVE, { timeout: 20_000 });
  await expect(composer(b.page)).toHaveCount(0);
  expect(await fake.requests(AI_ROUTES, mark)).toEqual([]);
});

test('J-11: after a transfer to an active user the former owner\'s deactivation no longer matters', { tag: '@collab' }, async ({ users }) => {
  const a = await users.fresh('J11x');
  const { b, c } = users;
  const chat = await a.api.startChat(chatTitle('j11-after'));
  await shareChat(a.api, chat, { userId: b.actor.userId, level: 'write' });
  await shareChat(a.api, chat, { userId: c.actor.userId, level: 'write' });
  const moved = await a.api.call('POST', `/api/v1/conversations/${chat}/transfer-ownership`, { newOwnerUserId: b.actor.userId });
  expect(moved.status, JSON.stringify(moved.body)).toBe(200);
  await fake.setDisabled(a.actor.userId, true);

  // Every collaborator, the other editor included, can send as before: the owner is B now.
  await openChat(c.page, chat);
  await expect(composer(c.page)).toBeVisible();
  await expect(c.page.getByTestId('read-only-banner')).toHaveCount(0);
  await fake.script('chat_stream', { kind: 'stream_answer', text: 'Answer after the transfer' });
  await send(c.page, 'I can still write');
  await expect(c.page.getByText('Answer after the transfer')).toBeVisible({ timeout: 30_000 });
});
