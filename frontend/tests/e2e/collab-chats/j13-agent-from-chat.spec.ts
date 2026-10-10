import { test, expect } from './support/two-users.fixture';
import { fake, waitFor, type Actor } from './support/stack';
import { composer, openChat } from './support/chat-ui';
import { expectNoBlockingViolations } from './support/a11y';
import { chatTitle, shareChat } from './support/collab.helper';

/**
 * J-13: an agent built from a chat. Python is the lane's fake: the draft is what the fake streams and the created agent
 * is what the fake's create-from-chat route returns. What is real here is Node (what it forwards, who may see the draft,
 * which agents the picker offers) and the card. That the draft has no tools until the user ticks them and that nothing is
 * stored before the click is Python's `integration_test_j13_agent_from_chat.py` and `agent_builder` unit tests.
 */
test.describe.configure({ mode: 'serial' });

const draftOf = (requester: Actor) => ({
  draftId: '8b9d3c1e-5a52-4f67-a3d4-6a4a1f3a9b13',
  name: 'Offer drafter',
  handleSuggestion: 'offer-drafter',
  description: 'Prepares quarterly pricing sheets',
  instructions: 'Write short, friendly offer letters.',
  knowledge: [],
  toolsets: [],
  suggestedTools: [],
  provenance: 'sender',
  requestedBy: requester.userId,
});

test('J-13: A asks for an agent, gets a draft with nothing ticked and nothing created, creates it private, and B sees only a one-line notice', { tag: '@collab' }, async ({ users }) => {
  const a = await users.fresh('J13');
  const { b } = users;
  await fake.script('chat_stream', { kind: 'agent_draft', draft: draftOf(a.actor) });
  const chat = await a.api.startChat(chatTitle('j13', 'Make me an agent that drafts offer letters'));
  await shareChat(a.api, chat, { userId: b.actor.userId, level: 'write' });

  const mark = await fake.mark();
  await openChat(a.page, chat);
  const card = a.page.getByTestId('agent-draft-card');
  await expect(card).toBeVisible({ timeout: 30_000 });
  await expect(card.getByTestId('agent-draft-name')).toHaveValue('Offer drafter');
  await expect(card.getByTestId('agent-draft-handle')).toHaveValue('offer-drafter');
  // Nothing is ticked and nothing exists yet: the fake saw no create.
  await expect(card.getByRole('checkbox', { checked: true })).toHaveCount(0);
  expect(await fake.requests(['agent_create', 'agent_create_from_chat'], mark)).toEqual([]);
  await expectNoBlockingViolations(a.page, 'agent-draft-card');

  await card.getByTestId('agent-draft-create').click();
  await expect(a.page.getByTestId('agent-draft-created')).toContainText('@offer-drafter', { timeout: 20_000 });
  const [created] = await waitFor('the create to reach Python', async () => {
    const sent = await fake.requests(['agent_create_from_chat'], mark);
    return sent.length ? sent : false;
  });
  expect(created.userId).toBe(a.actor.userId);
  expect(created.body).toMatchObject({ handle: 'offer-drafter', createdVia: 'chat', sourceConversationId: chat });
  // Private: not shared with the org, not a service account, and the chat's other people are not made members.
  const body = created.body as Record<string, unknown>;
  expect(body.shareWithOrg ?? false).toBe(false);
  expect(body.isServiceAccount ?? false).toBe(false);
  expect(JSON.stringify(body)).not.toContain(b.actor.userId);
  await expect(a.page.getByText('Created @offer-drafter (private)').first()).toBeVisible();

  // B reads the same chat and sees that A drafted an agent, not what is in it.
  await openChat(b.page, chat);
  await expect(b.page.getByTestId('agent-draft-redacted')).toBeVisible({ timeout: 30_000 });
  await expect(b.page.getByTestId('agent-draft-card')).toHaveCount(0);
  await expect(b.page.getByText('Write short, friendly offer letters.')).toHaveCount(0);
  await expect(b.page.getByText('Prepares quarterly pricing sheets')).toHaveCount(0);
  await expectNoBlockingViolations(b.page, 'agent-draft-redacted');
});

test('J-13: the @ picker offers an agent the caller can run in a plain chat, with its handle, to its creator', { tag: '@collab' }, async ({ users }) => {
  const a = await users.fresh('J13p');
  const agents = [
    { _key: 'agent-from-chat', name: 'Offer drafter', handle: 'offer-drafter', createdBy: a.actor.userId, isServiceAccount: false },
  ];
  const listing = { kind: 'reply' as const, body: { success: true, agents, pagination: { currentPage: 1, limit: 100, totalItems: 1, totalPages: 1 } } };
  await fake.script('agent_list', ...Array.from({ length: 40 }, () => listing));
  const chat = await a.api.startChat(chatTitle('j13-picker'));
  // The lane's fake has no service-account lookup, which a shared chat needs, so the plain chat is the one checked here.

  // M2: the picker and the validator share one verdict, and a guest agent may answer in any chat, so every caller who can run it is offered it.
  const list = (page: typeof a.page) => page.getByRole('listbox', { name: 'Mention suggestions' });
  for (const [person, expected] of [[a, 1]] as const) {
    await openChat(person.page, chat);
    await composer(person.page).click();
    await person.page.keyboard.type('@');
    await expect(list(person.page)).toBeVisible();
    await expect(list(person.page).getByRole('option').first()).toBeVisible();
    await expect(list(person.page).getByRole('option').filter({ hasText: 'Offer drafter' })).toHaveCount(expected, { timeout: 15_000 });
    await expect(list(person.page).getByRole('option').filter({ hasText: '@offer-drafter' })).toHaveCount(expected);
    await person.page.keyboard.press('Escape');
  }
});
