import { test, expect } from './support/two-users.fixture';
import { pdpAllows, type Actor, type NodeApi } from './support/stack';
import { composer, openChat, send } from './support/chat-ui';
import { expectNoBlockingViolations } from './support/a11y';
import { chatTitle, shareChat } from './support/collab.helper';

/**
 * J-07: consent. Python is the lane's fake, so the record routes that ask Node's PDP (the 404 C gets on B's file) are not
 * on this lane; they are Python unit tests (`tests/unit/modules/authz/test_node_pdp_client.py`, `test_chat_content_access.py`,
 * `tests/unit/connectors/api/test_router_chat_content_access.py`). What runs here is the other two halves, end to end:
 * the browser records consent on the turn B sends (`filesShared` on the wire, the audience notice B sees), and Node's PDP
 * endpoint (`/api/v1/authz/internal/check`, called the way Python calls it) answers for C with and without consent and
 * right after C is removed.
 */
test.describe.configure({ mode: 'serial' });

const attach = (recordId: string) => [{ recordId, recordName: `${recordId}.md` }];
const STREAM_URL = /\/conversations\/[0-9a-f]{24}\/messages\/stream$/;

async function sendAs(api: NodeApi, chat: string, body: Record<string, unknown>): Promise<string> {
  const r = await api.call('POST', `/api/v1/conversations/${chat}/messages`, { query: 'see attached', chatMode: 'quick', ...body });
  expect(r.status, JSON.stringify(r.body)).toBe(200);
  return r.headers['x-run-id'];
}

test('J-07: B tells the chat who sees what B sends, and the turn records no consent unless B attached files', { tag: '@collab' }, async ({ users }) => {
  const a = await users.fresh('J7ui');
  const { b, c } = users;
  const chat = await a.api.startChat(chatTitle('j07'));
  await shareChat(a.api, chat, { userId: b.actor.userId, level: 'write' });
  await shareChat(a.api, chat, { userId: c.actor.userId, level: 'read' });

  await openChat(b.page, chat);
  const notice = b.page.getByTestId('audience-notice');
  await expect(notice).toBeVisible();
  await expect(notice).toContainText('files you add are shared');
  await expectNoBlockingViolations(b.page, 'audience-notice');

  const sent = b.page.waitForRequest((r) => r.method() === 'POST' && STREAM_URL.test(r.url()));
  await send(b.page, 'B asks without a file');
  const body = (await sent).postDataJSON() as { filesShared?: boolean; attachments?: unknown[] };
  expect(body.filesShared).toBe(false);
  expect(body.attachments ?? []).toEqual([]);
  await expect(b.page.getByText('Fake answer').last()).toBeVisible({ timeout: 30_000 });
  await expect(composer(b.page)).toBeVisible();
});

test('J-07: C cannot read B\'s file without consent, can with it, and cannot at once after C is removed', { tag: '@collab' }, async ({ users }) => {
  const a = await users.fresh('J7pdp');
  const { b, c } = users;
  const stranger = await users.newPerson('stranger');
  const chat = await a.api.startChat(chatTitle('j07-pdp'));
  await shareChat(a.api, chat, { userId: b.actor.userId, level: 'write' });
  await shareChat(a.api, chat, { userId: c.actor.userId, level: 'read' });

  await sendAs(b.api, chat, { attachments: attach('rec-private') });
  await sendAs(b.api, chat, { attachments: attach('rec-shared'), filesShared: true });
  await sendAs(b.api, chat, { attachments: attach('rec-explicit-no'), filesShared: false });
  const consented = await sendAs(b.api, chat, { shareToolResults: true });
  const withheld = await sendAs(b.api, chat, {});

  const file = (viewer: Actor, recordId: string, conversationId: string | undefined = chat) =>
    pdpAllows(viewer, { type: 'chatAttachment', recordId, ownerUserId: b.actor.userId, conversationId });
  const artifact = (viewer: Actor, runId: string) =>
    pdpAllows(viewer, { type: 'chatArtifact', recordId: 'art-1', ownerUserId: b.actor.userId, conversationId: chat, runId });

  // Without consent a file is as good as missing; with it, it opens. B reads an unconsented file of its own through Python's owner edge, not this check.
  expect(await file(c.actor, 'rec-private')).toBe(false);
  expect(await file(c.actor, 'rec-explicit-no')).toBe(false);
  expect(await file(c.actor, 'rec-shared')).toBe(true);
  expect(await file(c.actor, 'rec-shared', undefined)).toBe(true);
  expect(await file(stranger.actor, 'rec-shared')).toBe(false);
  // The consent of one turn does not spread to another turn's tool results.
  expect(await artifact(c.actor, consented)).toBe(true);
  expect(await artifact(c.actor, withheld)).toBe(false);

  // C opens the chat and reads the history; the file itself stays behind the PDP.
  await openChat(c.page, chat);
  await expect(c.page.getByTestId('read-only-banner')).toContainText('You can read this chat but not continue it.');

  // A removes C: the next check, with and without the conversation id, says no.
  expect((await a.api.revoke(chat, c.actor)).status).toBe(200);
  expect(await file(c.actor, 'rec-shared')).toBe(false);
  expect(await file(c.actor, 'rec-shared', undefined)).toBe(false);
  expect(await artifact(c.actor, consented)).toBe(false);
  expect((await c.api.getChat(chat)).status).toBe(404);
  expect(await file(b.actor, 'rec-shared')).toBe(true);
});
