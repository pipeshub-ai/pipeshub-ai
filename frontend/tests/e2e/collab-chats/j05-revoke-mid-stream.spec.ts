import { test, expect } from './support/two-users.fixture';
import { fake, waitFor } from './support/stack';
import { composer, openChat, send } from './support/chat-ui';
import { expectNoBlockingViolations } from './support/a11y';

test.describe.configure({ mode: 'serial' });

const LOST = 'You no longer have access to this chat. What you see here is kept until you leave it.';
const LOST_EMPTY = "This chat isn't available to you. It may have been deleted or your access removed.";

test('J-05: A revokes B mid-stream; B keeps the history while it is open and loses the chat on navigation', { tag: '@collab' }, async ({ users }) => {
  const { a, b } = users;
  const chat = await a.api.startChat('A first question');
  expect((await a.api.share(chat, b.actor, 'write')).status).toBe(200);

  await openChat(a.page, chat);
  await openChat(b.page, chat);
  await expect(composer(b.page)).toBeVisible();

  await fake.script('chat_stream', { kind: 'held_stream', gate: 'a-run', text: 'A answers after the revoke', runId: 'run-a' });
  await send(a.page, 'A second question');
  await waitFor('A run to reach the gate', () => fake.gateReached('a-run'));
  await expect(b.page.getByTestId('busy-banner')).toBeVisible({ timeout: 20_000 });

  // A removes B while A's run is open.
  expect((await a.api.revoke(chat, b.actor)).status).toBe(200);

  // B's next poll gets a 404: banner instead of the composer, history kept, no busy banner.
  const banner = b.page.getByTestId('read-only-banner');
  await expect(banner).toContainText(LOST, { timeout: 30_000 });
  await expect(composer(b.page)).toHaveCount(0);
  await expect(b.page.getByTestId('busy-banner')).toHaveCount(0);
  await expect(b.page.getByText('A first question')).toBeVisible();
  await expect(b.page.getByText('Fake answer')).toBeVisible();
  await expectNoBlockingViolations(b.page, 'chat-access-lost-banner');

  // The run is not cut short by the revoke.
  await fake.openGate('a-run');
  await expect(a.page.getByText('A answers after the revoke')).toBeVisible({ timeout: 30_000 });

  // Leaving the slot evicts it. Opening the chat again client-side (no reload) must load it from the server
  // again, which now answers 404, rather than bring the kept history back.
  await b.page.getByText('New Chat', { exact: true }).click();
  await expect(b.page).not.toHaveURL(new RegExp(chat));
  await expect(composer(b.page)).toBeVisible();
  await expect(b.page.getByText('A first question')).toHaveCount(0);
  const reloaded = b.page.waitForResponse((r) => r.url().includes(`/api/v1/conversations/${chat}`) && r.request().method() === 'GET');
  await b.page.evaluate((id) => window.history.pushState(null, '', `/chat/?conversationId=${id}`), chat);
  expect((await reloaded).status()).toBe(404);
  await expect(b.page.getByText('A first question')).toHaveCount(0);
  await expect(b.page.getByText('Fake answer')).toHaveCount(0);
  // No composer on the empty thread of a chat B cannot open.
  await expect(b.page.getByTestId('read-only-banner')).toContainText(LOST_EMPTY);
  await expect(composer(b.page)).toHaveCount(0);
});

test('J-05 (a11y): the read-only banner of a viewer has no serious violations', { tag: '@collab' }, async ({ users }) => {
  const { a } = users;
  const viewer = users.c;
  const chat = await a.api.startChat('A first question');
  expect((await a.api.share(chat, viewer.actor, 'read')).status).toBe(200);

  await openChat(viewer.page, chat);
  await expect(viewer.page.getByTestId('read-only-banner')).toContainText('You can read this chat but not continue it.');
  await expect(composer(viewer.page)).toHaveCount(0);
  await expectNoBlockingViolations(viewer.page, 'chat-read-only-banner');
});
