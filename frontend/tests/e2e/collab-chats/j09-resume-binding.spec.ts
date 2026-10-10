import { test, expect } from './support/two-users.fixture';
import { AI_ROUTES, fake } from './support/stack';
import { composer, expectComposerText, openChat, send } from './support/chat-ui';

test.describe.configure({ mode: 'serial' });

const NOT_ASKER = /Only the person who was asked can answer this question/;

const QUESTION = {
  userIntent: 'Deploy the service',
  questions: [{ question: 'Which environment?', options: ['staging', 'production'], multiSelect: false }],
};

test('J-09: B parks a question; A sees it read-only and cannot answer it; B answers', { tag: '@collab' }, async ({ users }) => {
  const { a, b } = users;
  const chat = await a.api.startChat('A first question');
  expect((await a.api.share(chat, b.actor, 'write')).status).toBe(200);
  await openChat(a.page, chat);
  await openChat(b.page, chat);

  // B asks something whose run stops on a question card; it is B's to answer.
  await fake.script('chat_stream', { kind: 'ask_user_question', toolData: QUESTION });
  await send(b.page, 'deploy it');
  await expect(b.page.getByText('Which environment?')).toBeVisible({ timeout: 30_000 });
  await expect(b.page.getByTestId('ask-card-waiting')).toHaveCount(0);
  await expect(b.page.getByRole('button', { name: 'Submit' })).toBeVisible();

  // A sees the same card, read-only, naming B.
  await expect(a.page.getByText('Which environment?')).toBeVisible({ timeout: 30_000 });
  await expect(a.page.getByTestId('ask-card-waiting')).toContainText('Waiting for User Writer to answer');
  await expect(a.page.getByRole('radio', { name: 'staging' })).toBeDisabled();
  await expect(a.page.getByRole('button', { name: 'Submit' })).toBeDisabled();

  const mark = await fake.mark();

  // A tries anyway. Through the composer, with the text the card would send:
  await send(a.page, 'User selections: staging');
  await expect(a.page.getByText(NOT_ASKER).first()).toBeVisible({ timeout: 15_000 });
  // The refused attempt leaves no local-only bubble; the text goes back to the composer.
  await expect(a.page.getByTestId('human-message').filter({ hasText: 'User selections: staging' })).toHaveCount(0);
  await expectComposerText(a.page, 'User selections: staging');
  await composer(a.page).fill('');

  // ... and with the card named, over the plain and the streamed route.
  const detail = await a.api.getChat(chat);
  const card = detail.body.conversation.messages.find((m: { messageType: string }) => m.messageType === 'tool_call');
  expect(card).toBeTruthy();
  const body = { query: 'User selections: staging', chatMode: 'quick', resume: { toolCallMessageId: card._id } };
  const plain = await a.api.call('POST', `/api/v1/conversations/${chat}/messages`, body);
  const streamed = await a.api.call('POST', `/api/v1/conversations/${chat}/messages/stream`, { ...body, chatMode: 'internal_search' });
  for (const refused of [plain, streamed]) {
    expect(refused.status).toBe(403);
    expect(refused.body.error.code).toBe('RESUME_NOT_ALLOWED');
  }
  // No tool ran: Python was never asked on A's behalf.
  expect(await fake.requests(AI_ROUTES, mark)).toEqual([]);

  // B answers their own question.
  await fake.script('chat_stream', { kind: 'stream_answer', text: 'Deployed to staging', runId: 'run-b2' });
  await b.page.getByRole('radio', { name: 'staging' }).click();
  await b.page.getByRole('button', { name: 'Submit' }).click();
  await expect(b.page.getByText('Deployed to staging')).toBeVisible({ timeout: 30_000 });

  const asked = await fake.requests(['chat_stream'], mark);
  expect(asked).toHaveLength(1);
  expect(asked[0].userId).toBe(b.actor.userId);
  expect((asked[0].body as { resume?: { toolCallMessageId: string } }).resume).toEqual({ toolCallMessageId: card._id });

  // A's tab follows: the card is closed and B's answer is there.
  await expect(a.page.getByText('Deployed to staging')).toBeVisible({ timeout: 25_000 });
  await expect(a.page.getByTestId('ask-card-waiting')).toHaveCount(0);
  await expect(composer(a.page)).toBeVisible();
});
