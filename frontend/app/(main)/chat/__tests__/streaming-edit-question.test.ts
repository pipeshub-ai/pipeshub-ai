/**
 * Editing the last question: `streamRegenerateForSlot` with an edited query
 * sends it to the regenerate endpoint, shows it at once, keeps it once the
 * saved thread loads, and puts the stored question back if the run fails.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import type { ThreadMessageLike } from '@assistant-ui/react';
import {
  installMemoryStorage,
  jsonResponse,
  jwtExpiringIn,
  sseFrame,
  sseResponse,
} from '@/lib/api/__tests__/sse-response';
import type { ConversationMessage } from '../types';

installMemoryStorage();

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { useChatStore } = await import('../store');
const { streamRegenerateForSlot, withEditedQuestion, withoutQuestionCard, editWasNotTaken } = await import('../streaming');
const { ChatStreamError } = await import('../stream-error');
const { StreamError } = await import('@/lib/api/stream-errors');
const { getThreadMessagePlainText } = await import('../runtime');
const { apiClient } = await import('@/lib/api');

const fetchMock = vi.fn<typeof fetch>();
const initialState = useChatStore.getState();
const MODEL = { modelKey: 'm1', modelName: 'gpt-5', modelFriendlyName: 'GPT-5' };

function frame(type: string, fields: Record<string, unknown> = {}): string {
  return sseFrame(type, { type, ...fields });
}

function stored(overrides: Partial<ConversationMessage>): ConversationMessage {
  return {
    _id: 'm',
    messageType: 'user_query',
    content: '',
    contentFormat: 'MARKDOWN',
    citations: [],
    followUpQuestions: [],
    feedback: [],
    createdAt: '2026-09-18T00:00:00.000Z',
    updatedAt: '2026-09-18T00:00:00.000Z',
    ...overrides,
  } as ConversationMessage;
}

function conversationWith(question: string, answer: string) {
  return {
    _id: 'conv-1',
    id: 'conv-1',
    title: 'Revenue',
    createdAt: '2026-09-18T00:00:00.000Z',
    updatedAt: '2026-09-18T00:00:00.000Z',
    isShared: false,
    status: 'complete',
    modelInfo: { modelKey: 'm1', modelName: 'gpt-5' },
    messages: [
      stored({ _id: 'q1', messageType: 'user_query', content: question }),
      stored({ _id: 'a1', messageType: 'bot_response', content: answer }),
    ],
  };
}

function threadWith(question: string): ThreadMessageLike[] {
  return [
    { id: 'q1', role: 'user', content: [{ type: 'text', text: question }] },
    { id: 'a1', role: 'assistant', content: [{ type: 'text', text: 'old answer' }] },
  ];
}

function newSlot(question = 'Old question'): string {
  const slotId = useChatStore.getState().createSlot('conv-1');
  useChatStore.setState({ activeSlotId: slotId });
  useChatStore.getState().updateSlot(slotId, { messages: threadWith(question) });
  return slotId;
}

const slot = (id: string) => useChatStore.getState().slots[id];
const questionOf = (id: string) => getThreadMessagePlainText(slot(id).messages[0]);
const sentBody = () => JSON.parse(String(fetchMock.mock.calls[0][1]?.body));

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'r' });
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, pendingConversations: {}, conversations: [] });
  window.history.replaceState(null, '', '/chat/');
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('withEditedQuestion', () => {
  it('swaps the text of the question that sits before the answer', () => {
    const out = withEditedQuestion(threadWith('Old'), 'a1', 'New');

    expect(out.map((m) => getThreadMessagePlainText(m))).toEqual(['New', 'old answer']);
  });

  it('finds the answer by its stored message id as well as its row id', () => {
    const thread = threadWith('Old');
    thread[1] = { ...thread[1], id: 'row-9', metadata: { custom: { messageId: 'a1' } } };

    expect(getThreadMessagePlainText(withEditedQuestion(thread, 'a1', 'New')[0])).toBe('New');
  });

  it('leaves the thread untouched when the answer is unknown or has no question before it', () => {
    const thread = threadWith('Old');
    const lone: ThreadMessageLike[] = [thread[1]];

    expect(withEditedQuestion(thread, 'nope', 'New')).toBe(thread);
    expect(withEditedQuestion(lone, 'a1', 'New')).toBe(lone);
  });

  it('only changes the question of the answer it was given', () => {
    const thread: ThreadMessageLike[] = [
      { id: 'q0', role: 'user', content: [{ type: 'text', text: 'First' }] },
      { id: 'a0', role: 'assistant', content: [{ type: 'text', text: 'one' }] },
      ...threadWith('Old'),
    ];

    const out = withEditedQuestion(thread, 'a1', 'New');

    expect(out.map((m) => getThreadMessagePlainText(m))).toEqual(['First', 'one', 'New', 'old answer']);
  });
});

describe('regenerating with an edited question', () => {
  it('sends the new question to the regenerate endpoint and shows it before the answer arrives', async () => {
    const slotId = newSlot();
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    fetchMock.mockImplementationOnce(async () => {
      await gate;
      return sseResponse([
        frame('TEXT_MESSAGE_START'),
        frame('TEXT_MESSAGE_CONTENT', { delta: 'new' }),
        frame('RUN_FINISHED', { result: {} }),
      ]);
    });
    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({
      data: { conversation: conversationWith('Edited question', 'new answer') },
    });

    const run = streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, { query: 'Edited question' });

    expect(questionOf(slotId)).toBe('Edited question');
    expect(slot(slotId).isStreaming).toBe(true);
    release();
    await run;

    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/v1/conversations/conv-1/message/a1/regenerate');
    expect(sentBody().query).toBe('Edited question');
    expect(slot(slotId).isStreaming).toBe(false);
    expect(slot(slotId).messages.map((m) => getThreadMessagePlainText(m))).toEqual(['Edited question', 'new answer']);
  });

  it('sends no question when the user did not edit it', async () => {
    const slotId = newSlot();
    fetchMock.mockImplementationOnce(async () => sseResponse([frame('RUN_FINISHED', { result: {} })]));
    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({
      data: { conversation: conversationWith('Old question', 'new answer') },
    });

    await streamRegenerateForSlot(slotId, 'a1', MODEL);

    expect(sentBody()).not.toHaveProperty('query');
    expect(questionOf(slotId)).toBe('Old question');
  });

  it('puts the stored question back when the request fails', async () => {
    const slotId = newSlot();
    fetchMock.mockResolvedValueOnce(jsonResponse(500, {}));

    await streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, { query: 'Edited question' });

    expect(sentBody().query).toBe('Edited question');
    expect(slot(slotId).isStreaming).toBe(false);
    expect(questionOf(slotId)).toBe('Old question');
    expect(getThreadMessagePlainText(slot(slotId).messages[1])).toBe('old answer');
  });

  it('puts the stored question back when the run ends in an error', async () => {
    const slotId = newSlot();
    fetchMock.mockImplementationOnce(async () =>
      sseResponse([frame('RUN_ERROR', { message: 'The AI model is not responding right now.', code: 'stream_error' })]),
    );

    await streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, { query: 'Edited question' });

    expect(slot(slotId).isStreaming).toBe(false);
    expect(questionOf(slotId)).toBe('Old question');
  });

  it('still sends the question when its row is not in the thread', async () => {
    const slotId = newSlot();
    fetchMock.mockImplementationOnce(async () => sseResponse([frame('RUN_FINISHED', { result: {} })]));
    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({
      data: { conversation: conversationWith('Edited question', 'new answer') },
    });

    await streamRegenerateForSlot(slotId, 'unknown-id', MODEL, undefined, { query: 'Edited question' });

    expect(sentBody().query).toBe('Edited question');
  });

  it('sends the new question to an agent conversation too', async () => {
    const slotId = newSlot();
    useChatStore.getState().updateSlot(slotId, { threadAgentId: 'agent-1' });
    fetchMock.mockImplementationOnce(async () =>
      sseResponse([frame('RUN_ERROR', { message: 'stop here', code: 'stream_error' })]),
    );

    await streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, { query: 'Edited question' });

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      '/api/v1/agents/agent-1/conversations/conv-1/message/a1/regenerate',
    );
    expect(sentBody().query).toBe('Edited question');
  });

  it('moves the sidebar title when editing the first question renamed the chat', async () => {
    const slotId = newSlot();
    useChatStore.setState({ conversations: [{ id: 'conv-1', title: 'Old question' } as never] });
    fetchMock.mockImplementationOnce(async () => sseResponse([frame('RUN_FINISHED', { result: {} })]));
    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({
      data: { conversation: { ...conversationWith('Edited question', 'new answer'), title: 'Edited question' } },
    });

    await streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, { query: 'Edited question' });

    expect(useChatStore.getState().conversations[0].title).toBe('Edited question');
  });
});

describe('editing a turn that went through a question card', () => {
  const card = { name: 'ask_user_question', questions: [{ question: 'What do you enjoy?', options: [] }] };
  const answers = { q1: { questionUuid: 'q1', selectedOptionIds: ['o1'], userInputs: {} } };
  const pending = { assistantMessageId: 'a1', payload: card, answers, status: 'persisted' } as never;

  function cardSlot(): string {
    const slotId = newSlot();
    const messages = threadWith('Old question');
    messages[1] = {
      ...messages[1],
      metadata: { custom: { messageId: 'a1', persistedAskUserQuestion: card, persistedAskUserQuestionAnswers: answers } },
    } as ThreadMessageLike;
    useChatStore.getState().updateSlot(slotId, { messages, pendingAskUserQuestion: pending });
    return slotId;
  }
  const answerCustom = (id: string) =>
    (slot(id).messages[1].metadata?.custom ?? {}) as Record<string, unknown>;

  it('drops the card from the answer row, and keeps the rest of the row', () => {
    const thread = threadWith('Old');
    thread[1] = { ...thread[1], metadata: { custom: { messageId: 'a1', persistedAskUserQuestion: card, confidence: 'High' } } } as ThreadMessageLike;

    const out = withoutQuestionCard(thread, 'a1');

    expect(out[1].metadata?.custom).toEqual({ messageId: 'a1', confidence: 'High' });
    expect(out[0]).toBe(thread[0]);
  });

  it('leaves other rows and an unknown id alone', () => {
    const thread = threadWith('Old');

    expect(withoutQuestionCard(thread, 'nope')).toEqual(thread);
  });

  it('shows no card while the new answer streams, and none on the new answer', async () => {
    const slotId = cardSlot();
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    fetchMock.mockImplementationOnce(async () => {
      await gate;
      return sseResponse([frame('RUN_FINISHED', { result: {} })]);
    });
    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({
      data: { conversation: conversationWith('Edited question', 'new answer') },
    });

    const run = streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, { query: 'Edited question' });

    expect(slot(slotId).pendingAskUserQuestion).toBeNull();
    expect(answerCustom(slotId)).not.toHaveProperty('persistedAskUserQuestion');
    expect(answerCustom(slotId)).not.toHaveProperty('persistedAskUserQuestionAnswers');
    release();
    await run;

    expect(slot(slotId).pendingAskUserQuestion).toBeNull();
    expect(answerCustom(slotId)).not.toHaveProperty('persistedAskUserQuestion');
    expect(slot(slotId).messages.map((m) => getThreadMessagePlainText(m))).toEqual(['Edited question', 'new answer']);
  });

  it('puts the card back when the run fails', async () => {
    const slotId = cardSlot();
    fetchMock.mockResolvedValueOnce(jsonResponse(500, {}));

    await streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, { query: 'Edited question' });

    expect(slot(slotId).pendingAskUserQuestion).toEqual(pending);
    expect(answerCustom(slotId)).toHaveProperty('persistedAskUserQuestion');
    expect(questionOf(slotId)).toBe('Old question');
  });

  it('keeps the card on a plain regenerate, as before', async () => {
    const slotId = cardSlot();
    fetchMock.mockImplementationOnce(async () => sseResponse([frame('RUN_ERROR', { message: 'stop', code: 'stream_error' })]));

    await streamRegenerateForSlot(slotId, 'a1', MODEL);

    expect(slot(slotId).pendingAskUserQuestion).toEqual(pending);
    expect(answerCustom(slotId)).toHaveProperty('persistedAskUserQuestion');
  });
});

const FILE_A = { recordId: 'rec-a', recordName: 'a.pdf', mimeType: 'application/pdf', extension: 'pdf', virtualRecordId: 'v-a' };
const FILE_B = { recordId: 'rec-b', recordName: 'b.png', mimeType: 'image/png', extension: 'png', virtualRecordId: 'v-b' };

describe('editing the files of the question', () => {
  function slotWithFiles(files = [FILE_A]): string {
    const slotId = newSlot();
    const messages = threadWith('Old question');
    messages[0] = { ...messages[0], metadata: { custom: { attachments: files, createdAt: 'then' } } } as ThreadMessageLike;
    useChatStore.getState().updateSlot(slotId, { messages });
    return slotId;
  }
  const questionFiles = (id: string) =>
    (slot(id).messages[0].metadata?.custom as { attachments?: unknown } | undefined)?.attachments;

  it('swaps the files of the question row, and leaves the rest of the row alone', () => {
    const thread = threadWith('Old');
    thread[0] = { ...thread[0], metadata: { custom: { attachments: [FILE_A], createdAt: 'then' } } } as ThreadMessageLike;

    const out = withEditedQuestion(thread, 'a1', 'New', [FILE_B]);

    expect(out[0].metadata?.custom).toEqual({ attachments: [FILE_B], createdAt: 'then' });
    expect(getThreadMessagePlainText(out[0])).toBe('New');
  });

  it('clears the files for an empty list, and keeps them when none are given', () => {
    const thread = threadWith('Old');
    thread[0] = { ...thread[0], metadata: { custom: { attachments: [FILE_A] } } } as ThreadMessageLike;

    expect((withEditedQuestion(thread, 'a1', 'New', [])[0].metadata?.custom as { attachments?: unknown }).attachments).toBeUndefined();
    expect((withEditedQuestion(thread, 'a1', 'New')[0].metadata?.custom as { attachments?: unknown }).attachments).toEqual([FILE_A]);
  });

  it('sends the new files with the question, and shows them before the answer arrives', async () => {
    const slotId = slotWithFiles();
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    fetchMock.mockImplementationOnce(async () => {
      await gate;
      return sseResponse([frame('RUN_FINISHED', { result: {} })]);
    });
    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({
      data: { conversation: conversationWith('Edited question', 'new answer') },
    });

    const run = streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, { query: 'Edited question', attachments: [FILE_B] });

    expect(questionFiles(slotId)).toEqual([FILE_B]);
    release();
    await run;

    expect(sentBody()).toMatchObject({ query: 'Edited question', attachments: [FILE_B] });
  });

  it('sends an empty list to remove every file', async () => {
    const slotId = slotWithFiles();
    fetchMock.mockImplementationOnce(async () => sseResponse([frame('RUN_ERROR', { message: 'stop', code: 'stream_error' })]));

    await streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, { query: 'Old question', attachments: [] });

    expect(sentBody().attachments).toEqual([]);
  });

  it('sends no files when only the text changed, so the stored files stay', async () => {
    const slotId = slotWithFiles();
    fetchMock.mockImplementationOnce(async () => sseResponse([frame('RUN_ERROR', { message: 'stop', code: 'stream_error' })]));

    await streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, { query: 'Edited question' });

    expect(sentBody()).not.toHaveProperty('attachments');
    expect(questionFiles(slotId)).toEqual([FILE_A]);
  });

  it('puts the stored files back when the request fails', async () => {
    const slotId = slotWithFiles();
    fetchMock.mockResolvedValueOnce(jsonResponse(500, {}));

    await streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, { query: 'Edited question', attachments: [FILE_B] });

    expect(questionFiles(slotId)).toEqual([FILE_A]);
    expect(questionOf(slotId)).toBe('Old question');
  });

  it('puts the stored files back when the run ends in an error', async () => {
    const slotId = slotWithFiles();
    fetchMock.mockImplementationOnce(async () => sseResponse([frame('RUN_ERROR', { message: 'stop', code: 'stream_error' })]));

    await streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, { query: 'Edited question', attachments: [] });

    expect(questionFiles(slotId)).toEqual([FILE_A]);
  });

  it('sends the files to an agent conversation too', async () => {
    const slotId = slotWithFiles();
    useChatStore.getState().updateSlot(slotId, { threadAgentId: 'agent-1' });
    fetchMock.mockImplementationOnce(async () => sseResponse([frame('RUN_ERROR', { message: 'stop', code: 'stream_error' })]));

    await streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, { query: 'Edited question', attachments: [FILE_B] });

    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/v1/agents/agent-1/');
    expect(sentBody()).toMatchObject({ query: 'Edited question', attachments: [FILE_B] });
  });
});

describe('an edit whose connection drops', () => {
  const EDIT = { query: 'Edited question' };

  it('reads what the server did back, instead of putting the old question on screen', async () => {
    const slotId = newSlot();
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));

    await streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, EDIT);

    expect(slot(slotId).isStreaming).toBe(false);
    expect(slot(slotId).remoteRunInProgress).toBe(true);
    expect(slot(slotId).convId).toBe('conv-1');
    expect(questionOf(slotId)).toBe('Edited question');
  });

  it('does the same when the connection drops part-way through the answer', async () => {
    const slotId = newSlot();
    fetchMock.mockImplementationOnce(async () =>
      sseResponse({
        chunks: [frame('TEXT_MESSAGE_START'), frame('TEXT_MESSAGE_CONTENT', { delta: 'Two ' })],
        failWith: new TypeError('network error'),
      }),
    );

    await streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, EDIT);

    expect(slot(slotId).remoteRunInProgress).toBe(true);
    expect(questionOf(slotId)).toBe('Edited question');
  });

  it('does the same when the stream closes without saying how it ended', async () => {
    const slotId = newSlot();
    fetchMock.mockImplementationOnce(async () => sseResponse([frame('TEXT_MESSAGE_START')]));

    await streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, EDIT);

    expect(slot(slotId).remoteRunInProgress).toBe(true);
    expect(questionOf(slotId)).toBe('Edited question');
  });

  it('keeps the card of the old turn off the screen, and does not bring it back', async () => {
    const slotId = newSlot();
    const card = { name: 'ask_user_question', questions: [{ question: 'What do you enjoy?', options: [] }] };
    const messages = threadWith('Old question');
    messages[1] = {
      ...messages[1],
      metadata: { custom: { messageId: 'a1', persistedAskUserQuestion: card } },
    } as ThreadMessageLike;
    useChatStore.getState().updateSlot(slotId, {
      messages,
      pendingAskUserQuestion: { assistantMessageId: 'a1', payload: card, answers: {}, status: 'persisted' } as never,
    });
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));

    await streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, EDIT);

    expect(slot(slotId).pendingAskUserQuestion).toBeNull();
    expect((slot(slotId).messages[1].metadata?.custom as Record<string, unknown>)).not.toHaveProperty(
      'persistedAskUserQuestion',
    );
    expect(slot(slotId).remoteRunInProgress).toBe(true);
  });

  it('puts the old question back when the server says the run failed', async () => {
    const slotId = newSlot();
    fetchMock.mockImplementationOnce(async () =>
      sseResponse([frame('RUN_ERROR', { message: 'The model is rate limited.', code: 'rate_limit' })]),
    );

    await streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, EDIT);

    expect(questionOf(slotId)).toBe('Old question');
    expect(slot(slotId).remoteRunInProgress).toBe(false);
  });

  it('puts the old question back when the server refused the request', async () => {
    const slotId = newSlot();
    fetchMock.mockResolvedValueOnce(jsonResponse(500, {}));

    await streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, EDIT);

    expect(questionOf(slotId)).toBe('Old question');
    expect(slot(slotId).remoteRunInProgress).toBe(false);
  });

  it('puts the old question back when the server is busy', async () => {
    const slotId = newSlot();
    fetchMock.mockResolvedValueOnce(jsonResponse(429, {}, { 'retry-after': '5' }));

    await streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, EDIT);

    expect(questionOf(slotId)).toBe('Old question');
    expect(slot(slotId).remoteRunInProgress).toBe(false);
  });

  it('leaves a plain regenerate that drops as it was: nothing was edited to read back', async () => {
    const slotId = newSlot();
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));

    await streamRegenerateForSlot(slotId, 'a1', MODEL);

    expect(slot(slotId).remoteRunInProgress).toBe(false);
    expect(questionOf(slotId)).toBe('Old question');
  });
});

describe('editWasNotTaken', () => {
  it('is true for a run the server reported as failed and for a request it refused', () => {
    expect(editWasNotTaken(new ChatStreamError('The model is rate limited.', 'rate_limit'))).toBe(true);
    expect(editWasNotTaken(new StreamError('PipesHub is busy.', 429))).toBe(true);
  });

  it('is false for a connection that dropped, where the run goes on', () => {
    expect(editWasNotTaken(new StreamError('The connection dropped.'))).toBe(false);
    expect(editWasNotTaken(new TypeError('Failed to fetch'))).toBe(false);
    expect(editWasNotTaken(new Error('The answer was interrupted'))).toBe(false);
    expect(editWasNotTaken(undefined)).toBe(false);
  });
});
