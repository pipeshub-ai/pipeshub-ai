import { test, expect } from './support/two-users.fixture';
import { fake, waitFor, type Actor } from './support/stack';
import { openChat, send } from './support/chat-ui';
import { expectNoBlockingViolations } from './support/a11y';

test.describe.configure({ mode: 'serial' });

const DRAFT = (requestedBy: string) => ({
  draftId: '5d1f6d1e-1a52-4f67-a3d4-6a4a1f3a9a01',
  name: 'Offer drafter',
  handleSuggestion: 'offer-drafter',
  description: 'Drafts offer letters',
  instructions: 'Write short, friendly offer letters.',
  knowledge: [],
  toolsets: [],
  suggestedTools: [],
  provenance: 'sender',
  requestedBy,
});

const draftTurn = (requester: Actor, over: Record<string, unknown> = {}) =>
  fake.script('chat_stream', { kind: 'agent_draft', draft: { ...DRAFT(requester.userId), ...over } });

const card = (page: import('@playwright/test').Page) => page.getByTestId('agent-draft-card');

test('draft card: the requester creates a private agent, Node forwards the server-set provenance, others see a one-line notice', { tag: '@collab' }, async ({ users }) => {
  const { a, b } = users;
  await draftTurn(a.actor);
  const chat = await a.api.startChat('Make me an agent that drafts offer letters');
  expect((await a.api.share(chat, b.actor, 'write')).status).toBe(200);

  await openChat(a.page, chat);
  const mine = card(a.page);
  await expect(mine).toBeVisible({ timeout: 30_000 });
  await expect(mine.getByTestId('agent-draft-name')).toHaveValue('Offer drafter');
  await expect(mine.getByTestId('agent-draft-handle')).toHaveValue('offer-drafter');
  await expect(mine.getByTestId('agent-draft-handle-status')).toContainText(/available/i);
  await expectNoBlockingViolations(a.page, 'agent-draft-card');

  const mark = await fake.mark();
  await mine.getByTestId('agent-draft-name').fill('Offer drafter v2');
  await mine.getByTestId('agent-draft-create').click();
  await expect(a.page.getByTestId('agent-draft-created')).toContainText('@offer-drafter', { timeout: 20_000 });

  const [created] = await waitFor('the create to reach Python', async () => {
    const sent = await fake.requests(['agent_create_from_chat'], mark);
    return sent.length ? sent : false;
  });
  expect(await fake.requests(['agent_create'], mark)).toHaveLength(0);
  const body = created.body as Record<string, unknown>;
  expect(body).toMatchObject({ name: 'Offer drafter v2', handle: 'offer-drafter', createdVia: 'chat', sourceConversationId: chat });
  expect(typeof body.sourceMessageId).toBe('string');
  expect(body).not.toHaveProperty('draftRef');
  expect(body.isServiceAccount ?? false).toBe(false);
  expect(body.shareWithOrg ?? false).toBe(false);
  expect(created.userId).toBe(a.actor.userId);

  // B sees only that A drafted an agent, on first load and without the contents.
  await openChat(b.page, chat);
  await expect(b.page.getByTestId('agent-draft-redacted')).toBeVisible({ timeout: 30_000 });
  await expect(b.page.getByTestId('agent-draft-card')).toHaveCount(0);
  await expect(b.page.getByText('Write short, friendly offer letters.')).toHaveCount(0);
});

test('draft card: once created, the toast and the card open the agent chat; no mention is offered until guest agent turns (M2)', { tag: '@collab' }, async ({ users }) => {
  const { a } = users;
  await draftTurn(a.actor);
  const chat = await a.api.startChat('Make me an agent');
  await openChat(a.page, chat);
  await expect(card(a.page)).toBeVisible({ timeout: 30_000 });

  await card(a.page).getByTestId('agent-draft-create').click();
  await expect(a.page.getByText('Created @offer-drafter (private)').first()).toBeVisible({ timeout: 20_000 });
  await expect(a.page.getByRole('button', { name: 'Open agent chat' }).first()).toBeVisible();
  await expect(a.page.getByRole('button', { name: 'Mention here' })).toHaveCount(0);

  await card(a.page).getByTestId('agent-draft-open-chat').click();
  await expect(a.page).toHaveURL(/\/chat\/?\?agentId=agent-from-chat/, { timeout: 20_000 });
});

test('draft card: a taken handle is shown inline with its suggestion and no global toast', { tag: '@collab' }, async ({ users }) => {
  const { a } = users;
  await draftTurn(a.actor);
  const chat = await a.api.startChat('Make me an agent');
  await openChat(a.page, chat);
  await expect(card(a.page)).toBeVisible({ timeout: 30_000 });

  await fake.script('agent_create_from_chat', {
    kind: 'reply',
    status: 409,
    body: { detail: { code: 'HANDLE_TAKEN', message: 'The handle @offer-drafter is already taken.', suggestion: 'offer-drafter-2' } },
  });
  await card(a.page).getByTestId('agent-draft-create').click();

  const status = card(a.page).getByTestId('agent-draft-handle-status');
  await expect(status).toContainText(/taken/i, { timeout: 20_000 });
  await expect(card(a.page).getByTestId('agent-draft-use-suggestion')).toHaveText(/offer-drafter-2/);
  await expect(a.page.getByText(/action required/i)).toHaveCount(0);

  const mark = await fake.mark();
  await card(a.page).getByTestId('agent-draft-use-suggestion').click();
  await expect(card(a.page).getByTestId('agent-draft-handle')).toHaveValue('offer-drafter-2');
  await card(a.page).getByTestId('agent-draft-create').click();
  await expect(a.page.getByTestId('agent-draft-created')).toContainText('@offer-drafter-2', { timeout: 20_000 });
  const [sent] = await fake.requests(['agent_create_from_chat'], mark);
  expect(sent.body).toMatchObject({ handle: 'offer-drafter-2' });
});

test('draft card: a draft shaped by document content says so and ticks no knowledge', { tag: '@collab' }, async ({ users }) => {
  const { a } = users;
  await draftTurn(a.actor, { provenance: 'content', knowledge: ['connector-1'] });
  const chat = await a.api.startChat('Make me an agent');
  await openChat(a.page, chat);
  await expect(card(a.page)).toBeVisible({ timeout: 30_000 });

  await expect(card(a.page).getByTestId('agent-draft-content-banner')).toContainText('document content');
  await expect(card(a.page).getByTestId('agent-draft-knowledge-connector-1')).not.toBeChecked();
});

test('draft card: asking in the page shows a card that can be used once the turn has finished', { tag: '@collab' }, async ({ users }) => {
  const { a } = users;
  const chat = await a.api.startChat('Hello');
  await openChat(a.page, chat);

  await draftTurn(a.actor);
  await send(a.page, 'Make me an agent that drafts offer letters');
  await expect(card(a.page)).toBeVisible({ timeout: 30_000 });
  await expect(a.page.getByText('I drafted it for you to review.')).toBeVisible({ timeout: 30_000 });
  // The row is stored while the turn runs; once it is, Create must work without a reload.
  await expect(card(a.page).getByTestId('agent-draft-create')).toBeEnabled({ timeout: 30_000 });
});
