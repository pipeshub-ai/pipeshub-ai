/**
 * Unit tests for `loadHistoricalMessages`: how a stored conversation becomes
 * the thread shown after a reload.
 */
import { describe, it, expect, vi } from 'vitest';
import type { ConversationMessage } from '../types';

// `../runtime` pulls in `@/lib/api`, whose auth store reads localStorage at
// import time; nothing here makes a request.
vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const { loadHistoricalMessages } = await import('../runtime');

function message(overrides: Partial<ConversationMessage>): ConversationMessage {
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

const WAITING = 'Waiting for your approval to run create_issue on Jira.';
const CARD = { approvalId: 'ap-1', serverName: 'Jira', toolName: 'create_issue', expiresAt: 1_900_000_000_000 };

describe('loadHistoricalMessages, an answered approval card', () => {
  it('hides the answer and carries the reply on in the card\'s row, the call settled in place', () => {
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'file a bug' }),
      message({
        _id: 'a1', messageType: 'bot_response', content: WAITING,
        parts: [
          { type: 'tool_call', toolCallId: 'c0', toolName: 'search', status: 'completed' },
          { type: 'sub_agent', runId: 'r', parts: [
            { type: 'tool_call', toolCallId: 'c1', toolName: 'mcp_jira_create_issue', status: 'awaiting_approval', approval: CARD },
          ] },
          { type: 'text', content: WAITING, isFinal: true },
        ],
      } as Partial<ConversationMessage>),
      message({ _id: 'ans', messageType: 'user_query', content: 'Tool approval: allow' }),
      message({
        _id: 'a2', messageType: 'bot_response', content: 'Filed JIRA-7.',
        parts: [
          { type: 'tool_call', toolCallId: 'c1', toolName: 'mcp_jira_create_issue', status: 'completed', approved: true, approvalAnswer: 'allowed' },
          { type: 'text', content: 'Filed JIRA-7.', isFinal: true },
        ],
      } as Partial<ConversationMessage>),
      message({ _id: 'q2', messageType: 'user_query', content: 'thanks' }),
    ]);

    expect(messages.map((m) => [m.role, m.id])).toEqual([['user', 'q'], ['assistant', 'a1'], ['user', 'q2']]);
    expect(messages[1].content).toEqual([{ type: 'text', text: 'Filed JIRA-7.' }]);
    expect(messages[1].metadata?.custom?.messageId).toBe('a2');
    const parts = messages[1].metadata?.custom?.persistedParts as Array<Record<string, unknown>>;
    expect(parts.map((p) => p.toolCallId ?? p.type)).toEqual(['c0', 'sub_agent', 'text', 'text']);
    expect((parts[1].parts as Array<Record<string, unknown>>)[0]).toMatchObject({
      toolCallId: 'c1', status: 'completed', approvalAnswer: 'allowed', approval: CARD,
    });
  });

  it('keeps the next question\'s reply on its own row after an answer that failed', () => {
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'file a bug' }),
      message({
        _id: 'a1', messageType: 'bot_response', content: WAITING,
        parts: [{ type: 'tool_call', toolCallId: 'c1', toolName: 'mcp_jira_create_issue', status: 'awaiting_approval', approval: CARD }],
      } as Partial<ConversationMessage>),
      message({ _id: 'ans', messageType: 'user_query', content: 'Tool approval: allow' }),
      message({ _id: 'err', messageType: 'error', content: 'Error Generating Response, Please try again' }),
      message({ _id: 'q2', messageType: 'user_query', content: 'never mind, what is on my calendar?' }),
      message({ _id: 'a2', messageType: 'bot_response', content: 'Two meetings.' }),
    ]);

    expect(messages.map((m) => [m.role, m.id])).toEqual([
      ['user', 'q'], ['assistant', 'a1'], ['user', 'q2'], ['assistant', 'a2'],
    ]);
    expect((messages[1].metadata?.custom?.persistedParts as Array<Record<string, unknown>>)[0].status).toBe('awaiting_approval');
  });

  it('hides the error row of an answer whose run failed, leaving the card to answer again', () => {
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'file a bug' }),
      message({
        _id: 'a1', messageType: 'bot_response', content: WAITING,
        parts: [{ type: 'tool_call', toolCallId: 'c1', toolName: 'mcp_jira_create_issue', status: 'awaiting_approval', approval: CARD }],
      } as Partial<ConversationMessage>),
      message({ _id: 'ans', messageType: 'user_query', content: 'Tool approval: deny' }),
      message({ _id: 'err', messageType: 'error', content: 'Error Generating Response, Please try again' }),
    ]);

    expect(messages.map((m) => [m.role, m.id])).toEqual([['user', 'q'], ['assistant', 'a1']]);
    expect((messages[1].metadata?.custom?.persistedParts as Array<Record<string, unknown>>)[0].status).toBe('awaiting_approval');
  });
});

describe('loadHistoricalMessages', () => {
  it('drops a reply that was stopped before any text arrived', () => {
    // The backend saves this row when a stopped run's connection closes; the
    // live view never shows it, so a reload must not either.
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'A question' }),
      message({ _id: 'a', messageType: 'bot_response', content: '', status: 'stopped' }),
    ]);

    expect(messages.map((m) => m.role)).toEqual(['user']);
  });

  it('drops an empty stopped reply whose only part is the sign-in card', () => {
    const parts = [{ type: 'mcp_sign_in', servers: [{ instanceId: 'inst-drive', serverName: 'Drive', scopes: [] }] }];
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'A question' }),
      message({ _id: 'a', messageType: 'bot_response', content: '', status: 'stopped', parts } as Partial<ConversationMessage>),
    ]);

    expect(messages.map((m) => m.role)).toEqual(['user']);
  });

  it('keeps a stopped reply that has text, with its Stopped status', () => {
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'A question' }),
      message({ _id: 'a', messageType: 'bot_response', content: 'Partial answer', status: 'stopped' }),
    ]);

    expect(messages).toHaveLength(2);
    expect(messages[1].metadata?.custom?.status).toBe('stopped');
  });

  it('keeps an empty stopped reply that has tool activity', () => {
    // A run stopped mid-tool has no answer text, but its activity transcript
    // is what the user saw and must survive a reload.
    const parts = [{ type: 'tool_call', toolCallId: 't1', toolName: 'search_knowledge_base', status: 'running' }];
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'A question' }),
      message({ _id: 'a', messageType: 'bot_response', content: '', status: 'stopped', parts } as Partial<ConversationMessage>),
    ]);

    expect(messages).toHaveLength(2);
    expect(messages[1].metadata?.custom?.persistedParts).toEqual(parts);
  });

  it('keeps an empty stopped reply that carries a pending question', () => {
    const payload = { name: 'ask_user_question', questions: [{ question: 'Which region?', options: ['EU', 'US'] }] };
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'A question' }),
      message({
        _id: 't',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: payload }],
      } as Partial<ConversationMessage>),
      message({ _id: 'a', messageType: 'bot_response', content: '', status: 'stopped' }),
    ]);

    expect(messages.map((m) => m.role)).toEqual(['user', 'assistant']);
    expect(unansweredAskUserQuestion).not.toBeNull();
  });

  it('keeps an empty reply that was not stopped', () => {
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'A question' }),
      message({ _id: 'a', messageType: 'bot_response', content: '' }),
    ]);

    expect(messages).toHaveLength(2);
  });

  it('hides User selections and merges the follow-up answer into the question row', () => {
    const payload = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q-region',
        question: 'Which region?',
        multiSelect: false,
        options: [
          { id: 'eu', label: 'EU', isUserInput: false },
          { id: 'us', label: 'US', isUserInput: false },
        ],
      }],
    };
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'A question' }),
      message({
        _id: 't',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: payload }],
      } as Partial<ConversationMessage>),
      message({
        _id: 'a1',
        messageType: 'bot_response',
        content: 'Which region?',
        parts: [{ type: 'tool_call', toolCallId: 't1', toolName: 'search' }],
      } as Partial<ConversationMessage>),
      message({
        _id: 'sel',
        messageType: 'user_query',
        content: 'User selections:\n1. "Which region?" → EU',
      }),
      message({
        _id: 'a2',
        messageType: 'bot_response',
        content: 'EU it is.',
        confidence: 'High',
        parts: [{ type: 'tool_call', toolCallId: 't2', toolName: 'write' }],
      } as Partial<ConversationMessage>),
    ]);

    expect(messages.map((m) => m.role)).toEqual(['user', 'assistant']);
    expect(messages[1].id).toBe('a1');
    expect(messages[1].content).toEqual([{ type: 'text', text: 'EU it is.' }]);
    expect(messages[1].metadata?.custom?.persistedAskUserQuestion).toEqual(payload);
    expect(messages[1].metadata?.custom?.persistedAskUserQuestionAnswers).toEqual({
      'q-region': {
        questionUuid: 'q-region',
        selectedOptionIds: ['eu'],
        userInputs: {},
      },
    });
    expect(messages[1].metadata?.custom?.confidence).toBe('High');
    expect(messages[1].metadata?.custom?.persistedParts).toEqual([
      { type: 'tool_call', toolCallId: 't1', toolName: 'search' },
      { type: 'tool_call', toolCallId: 't2', toolName: 'write' },
    ]);
    expect(unansweredAskUserQuestion).toBeNull();
  });

  it('merges a follow-up into an empty waiting_input question row', () => {
    const payload = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q-topic',
        question: 'What would you like to talk about?',
        multiSelect: false,
        options: [
          { id: 'work', label: 'Work or career', isUserInput: false },
        ],
      }],
    };
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'hi i ask questions' }),
      message({
        _id: 't',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: payload }],
      } as Partial<ConversationMessage>),
      message({
        _id: 'a1',
        messageType: 'bot_response',
        content: '',
        parts: [{ type: 'tool_call', toolCallId: 't1', toolName: 'internaltools__ask_user_question' }],
      } as Partial<ConversationMessage>),
      message({
        _id: 'sel',
        messageType: 'user_query',
        content: 'User selections:\n1. "What would you like to talk about?" → Work or career',
      }),
      message({
        _id: 'a2',
        messageType: 'bot_response',
        content: 'Here is career advice.',
      }),
    ]);

    expect(messages.map((m) => m.role)).toEqual(['user', 'assistant']);
    expect(messages[1].id).toBe('a1');
    expect(messages[1].content).toEqual([{ type: 'text', text: 'Here is career advice.' }]);
    expect(messages[1].metadata?.custom?.persistedAskUserQuestion).toEqual(payload);
    expect(unansweredAskUserQuestion).toBeNull();
  });

  it('keeps a later greeting turn separate when the card is answered after it', () => {
    const payload = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q-place',
        question: 'Which type of place would you most enjoy exploring?',
        multiSelect: false,
        options: [
          { id: 'snow', label: 'A snowy mountain region', isUserInput: false },
        ],
      }],
    };
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'ask me question' }),
      message({
        _id: 't',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: payload }],
      } as Partial<ConversationMessage>),
      message({ _id: 'a1', messageType: 'bot_response', content: '' }),
      message({ _id: 'later', messageType: 'user_query', content: 'hiii' }),
      message({ _id: 'a2', messageType: 'bot_response', content: 'Hi! How can I help you today?' }),
      message({
        _id: 'sel',
        messageType: 'user_query',
        content: 'User selections:\n1. "Which type of place would you most enjoy exploring?" → A snowy mountain region',
      }),
      message({
        _id: 'a3',
        messageType: 'bot_response',
        content: 'A snowy mountain region sounds great.',
      }),
    ]);

    expect(messages.map((m) => [m.role, m.id])).toEqual([
      ['user', 'q'],
      ['assistant', 'a1'],
      ['user', 'later'],
      ['assistant', 'a2'],
    ]);
    expect(messages[1].content).toEqual([{ type: 'text', text: 'A snowy mountain region sounds great.' }]);
    expect(messages[1].metadata?.custom?.persistedAskUserQuestion).toEqual(payload);
    expect(messages[3].content).toEqual([{ type: 'text', text: 'Hi! How can I help you today?' }]);
    expect(messages[3].metadata?.custom?.persistedAskUserQuestion).toBeUndefined();
    expect(unansweredAskUserQuestion).toBeNull();
  });

  it('treats a message typed while the card was open as its answer and closes the card', () => {
    const payload = {
      name: 'ask_user_question',
      questions: [{ question: 'Which region?', options: ['EU', 'US'] }],
    };
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'ask me question' }),
      message({
        _id: 't',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: payload }],
      } as Partial<ConversationMessage>),
      message({ _id: 'a1', messageType: 'bot_response', content: '' }),
      message({ _id: 'later', messageType: 'user_query', content: 'hiii' }),
      message({ _id: 'a2', messageType: 'bot_response', content: 'Hi!' }),
    ]);

    expect(unansweredAskUserQuestion).toBeNull();
    expect(messages[1].id).toBe('a1');
    expect(messages[1].metadata?.custom?.persistedAskUserQuestion).toMatchObject({ repliedInChat: true });
    expect(messages.map((m) => m.role)).toEqual(['user', 'assistant', 'user', 'assistant']);
  });

  it('keeps an earlier unanswered card visible when a later card becomes pending', () => {
    const first = {
      name: 'ask_user_question',
      questions: [{ question: 'First?', options: ['A', 'B'] }],
    };
    const second = {
      name: 'ask_user_question',
      questions: [{ question: 'Second?', options: ['C', 'D'] }],
    };
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q1', messageType: 'user_query', content: 'ask me question' }),
      message({
        _id: 't1',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: first }],
      } as Partial<ConversationMessage>),
      message({ _id: 'a1', messageType: 'bot_response', content: "I wasn't able to generate a response. Please try rephrasing." }),
      message({ _id: 'q2', messageType: 'user_query', content: 'ask me one question' }),
      message({
        _id: 't2',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: second }],
      } as Partial<ConversationMessage>),
      message({ _id: 'a2', messageType: 'bot_response', content: '' }),
    ]);

    expect(messages[1].id).toBe('a1');
    // The second request was typed while the first card was open, so it answered that card.
    expect(messages[1].metadata?.custom?.persistedAskUserQuestion).toEqual({ ...first, repliedInChat: true });
    expect(messages[3].id).toBe('a2');
    expect(messages[3].metadata?.custom?.persistedAskUserQuestion).toEqual(second);
    expect(unansweredAskUserQuestion).toMatchObject({
      assistantMessageId: 'a2',
      payload: second,
      status: 'pending',
    });
  });

  it('keeps the card pending when User selections has no follow-up answer', () => {
    const payload = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q-region',
        question: 'Which region?',
        multiSelect: false,
        options: [
          { id: 'eu', label: 'EU', isUserInput: false },
          { id: 'us', label: 'US', isUserInput: false },
        ],
      }],
    };
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'ask me one question' }),
      message({
        _id: 't',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: payload }],
      } as Partial<ConversationMessage>),
      message({ _id: 'a1', messageType: 'bot_response', content: '' }),
      message({
        _id: 'sel',
        messageType: 'user_query',
        content: 'User selections:\n1. "Which region?" → EU',
      }),
    ]);

    expect(messages.map((m) => m.role)).toEqual(['user', 'assistant']);
    expect(messages[1].metadata?.custom?.persistedAskUserQuestionAnswers).toBeUndefined();
    expect(unansweredAskUserQuestion).toMatchObject({
      assistantMessageId: 'a1',
      status: 'pending',
      answers: {
        'q-region': {
          questionUuid: 'q-region',
          selectedOptionIds: ['eu'],
          userInputs: {},
        },
      },
    });
  });

  it('hides the error row of a failed resume and keeps the card pending', () => {
    const payload = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q-region',
        question: 'Which region?',
        multiSelect: false,
        options: [
          { id: 'eu', label: 'EU', isUserInput: false },
        ],
      }],
    };
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'ask me one question' }),
      message({
        _id: 't',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: payload }],
      } as Partial<ConversationMessage>),
      message({ _id: 'a1', messageType: 'bot_response', content: '' }),
      message({
        _id: 'sel',
        messageType: 'user_query',
        content: 'User selections:\n1. "Which region?" → EU',
      }),
      message({ _id: 'tc', messageType: 'tool_call', content: '' }),
      message({
        _id: 'err',
        messageType: 'error',
        content: 'Error Generating Response, Please try again',
      }),
    ]);

    expect(messages.map((m) => [m.role, m.id])).toEqual([
      ['user', 'q'],
      ['assistant', 'a1'],
    ]);
    expect(unansweredAskUserQuestion).toMatchObject({
      assistantMessageId: 'a1',
      status: 'pending',
      answers: {
        'q-region': {
          questionUuid: 'q-region',
          selectedOptionIds: ['eu'],
          userInputs: {},
        },
      },
    });
  });

  it('hides a failed resume and merges the retried answer into the card', () => {
    const payload = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q-region',
        question: 'Which region?',
        multiSelect: false,
        options: [
          { id: 'eu', label: 'EU', isUserInput: false },
        ],
      }],
    };
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'ask me one question' }),
      message({
        _id: 't',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: payload }],
      } as Partial<ConversationMessage>),
      message({ _id: 'a1', messageType: 'bot_response', content: '' }),
      message({
        _id: 'sel-fail',
        messageType: 'user_query',
        content: 'User selections:\n1. "Which region?" → EU',
      }),
      message({
        _id: 'err',
        messageType: 'error',
        content: 'Error Generating Response, Please try again',
      }),
      message({
        _id: 'sel-ok',
        messageType: 'user_query',
        content: 'User selections:\n1. "Which region?" → EU',
      }),
      message({ _id: 'a2', messageType: 'bot_response', content: 'EU it is.' }),
    ]);

    expect(messages.map((m) => [m.role, m.id])).toEqual([
      ['user', 'q'],
      ['assistant', 'a1'],
    ]);
    expect(messages[1].content).toEqual([{ type: 'text', text: 'EU it is.' }]);
    expect(messages[1].metadata?.custom?.persistedAskUserQuestionAnswers).toEqual({
      'q-region': {
        questionUuid: 'q-region',
        selectedOptionIds: ['eu'],
        userInputs: {},
      },
    });
    expect(unansweredAskUserQuestion).toBeNull();
  });

  it('keeps the card interactive when the resume asked another question', () => {
    const first = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q-proceed',
        question: 'How would you like to proceed?',
        multiSelect: false,
        options: [{ id: 'kb', label: 'Search the knowledge base instead', isUserInput: false }],
      }],
    };
    const second = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q-topic',
        question: 'What topic should I search for?',
        multiSelect: false,
        options: [{ id: 'keywords', label: 'Enter a topic or keywords', isUserInput: false }],
      }],
    };
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'fetch a jira page' }),
      message({
        _id: 't1',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: first }],
      } as Partial<ConversationMessage>),
      message({ _id: 'a1', messageType: 'bot_response', content: '' }),
      message({
        _id: 'sel',
        messageType: 'user_query',
        content: 'User selections:\n1. "How would you like to proceed?" → Search the knowledge base instead',
      }),
      message({
        _id: 'a2',
        messageType: 'bot_response',
        content: "I'll use the knowledge base. Please specify the topic.",
      }),
      message({
        _id: 't2',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: second }],
      } as Partial<ConversationMessage>),
    ]);

    expect(messages.map((m) => [m.role, m.id])).toEqual([
      ['user', 'q'],
      ['assistant', 'a1'],
    ]);
    expect(unansweredAskUserQuestion).toMatchObject({
      assistantMessageId: 'a1',
      status: 'pending',
      answers: {
        'q-proceed': {
          questionUuid: 'q-proceed',
          selectedOptionIds: ['kb'],
          userInputs: {},
        },
      },
    });
    expect(
      unansweredAskUserQuestion?.payload.questions.map((q) => q.uuid),
    ).toEqual(['q-proceed', 'q-topic']);
  });

  it('keeps the card on its own row, closed, when the user chats on past an unanswered question', () => {
    // The live path saves the new question's `tool_call` BEFORE the bot row, so
    // the resume's reply is merged away into the card row. The card must stay
    // addressed to the surviving row: a resume that binds to the merged-away id
    // cannot find it and lands the answer on the newest turn instead.
    const first = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q-help',
        question: 'What would you like help with today?',
        multiSelect: false,
        options: [{ id: 'web', label: 'Search the public web', isUserInput: false }],
      }],
    };
    const second = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q-topic',
        question: 'What should I search for?',
        multiSelect: false,
        options: [{ id: 'weather', label: 'Weather in Pune', isUserInput: false }],
      }],
    };
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q1', messageType: 'user_query', content: 'ask me one question' }),
      message({
        _id: 't1',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: first }],
      } as Partial<ConversationMessage>),
      message({ _id: 'a1', messageType: 'bot_response', content: 'Here are a few ways I can help.' }),
      message({
        _id: 'sel',
        messageType: 'user_query',
        content: 'User selections:\n1. "What would you like help with today?" → Search the public web',
      }),
      message({
        _id: 't2',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: second }],
      } as Partial<ConversationMessage>),
      message({ _id: 'a2', messageType: 'bot_response', content: 'What should I search for?' }),
      message({ _id: 'q2', messageType: 'user_query', content: 'you know ki i am in pune' }),
      message({ _id: 'a3', messageType: 'bot_response', content: 'Noted, you are in Pune.' }),
      message({ _id: 'q3', messageType: 'user_query', content: 'where i am living' }),
      message({ _id: 'a4', messageType: 'bot_response', content: 'Pune.' }),
    ]);

    const rowIds = messages.filter((m) => m.role === 'assistant').map((m) => m.id);
    expect(rowIds).toEqual(['a1', 'a3', 'a4']);
    // "you know ki i am in pune" was typed while the follow-up question was open: it answered it.
    expect(unansweredAskUserQuestion).toBeNull();
    const cardRow = messages.find((m) => m.id === 'a1');
    expect(cardRow?.metadata?.custom?.persistedAskUserQuestion).toMatchObject({ repliedInChat: true });
    expect(cardRow?.metadata?.custom?.persistedAskUserQuestionAnswers).toMatchObject({
      'q-help': { questionUuid: 'q-help', selectedOptionIds: ['web'], userInputs: {} },
    });
  });

  it('still shows an error row that follows a real user message', () => {
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'hello' }),
      message({
        _id: 'err',
        messageType: 'error',
        content: 'Error Generating Response, Please try again',
      }),
    ]);

    expect(messages.map((m) => [m.role, m.id])).toEqual([
      ['user', 'q'],
      ['assistant', 'err'],
    ]);
    expect(messages[1].content).toEqual([{
      type: 'text',
      text: 'Error Generating Response, Please try again',
    }]);
  });

  it('absorbs an empty resume follow-up and keeps the card pending', () => {
    const payload = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q-region',
        question: 'Which region?',
        multiSelect: false,
        options: [
          { id: 'eu', label: 'EU', isUserInput: false },
        ],
      }],
    };
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'ask me one question' }),
      message({
        _id: 't',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: payload }],
      } as Partial<ConversationMessage>),
      message({ _id: 'a1', messageType: 'bot_response', content: '' }),
      message({
        _id: 'sel',
        messageType: 'user_query',
        content: 'User selections:\n1. "Which region?" → EU',
      }),
      message({ _id: 'a2', messageType: 'bot_response', content: '' }),
    ]);

    expect(messages.map((m) => [m.role, m.id])).toEqual([
      ['user', 'q'],
      ['assistant', 'a1'],
    ]);
    expect(unansweredAskUserQuestion).toMatchObject({
      assistantMessageId: 'a1',
      status: 'pending',
    });
  });

  it('does not treat the empty-answer fallback as a completed follow-up', () => {
    const payload = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q-region',
        question: 'Which region?',
        multiSelect: false,
        options: [
          { id: 'eu', label: 'EU', isUserInput: false },
        ],
      }],
    };
    const { unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'ask me question' }),
      message({
        _id: 't',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: payload }],
      } as Partial<ConversationMessage>),
      message({ _id: 'a1', messageType: 'bot_response', content: '' }),
      message({
        _id: 'sel',
        messageType: 'user_query',
        content: 'User selections:\n1. "Which region?" → EU',
      }),
      message({
        _id: 'a2',
        messageType: 'bot_response',
        content: "I wasn't able to generate a response. Please try rephrasing.",
      }),
    ]);

    expect(unansweredAskUserQuestion).toMatchObject({
      assistantMessageId: 'a1',
      status: 'pending',
      answers: {
        'q-region': {
          questionUuid: 'q-region',
          selectedOptionIds: ['eu'],
          userInputs: {},
        },
      },
    });
  });

  it('attaches a regenerate tool_call that was saved after the bot row', () => {
    const payload = {
      name: 'ask_user_question',
      questions: [{ question: 'What would you choose?', options: ['A', 'B'] }],
    };
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'ask me question' }),
      message({
        _id: 'a1',
        messageType: 'bot_response',
        content: "I'll ask you one quickly, fun question.",
        parts: [{ type: 'tool_call', toolCallId: 't1', toolName: 'internaltools__ask_user_question' }],
      } as Partial<ConversationMessage>),
      message({
        _id: 't1',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: payload }],
      } as Partial<ConversationMessage>),
    ]);

    expect(messages[1].id).toBe('a1');
    expect(messages[1].metadata?.custom?.persistedAskUserQuestion).toEqual(payload);
    expect(unansweredAskUserQuestion).toMatchObject({
      assistantMessageId: 'a1',
      payload,
      status: 'pending',
    });
  });

  it('restores a regenerate card from tools saved on the bot itself', () => {
    const payload = {
      name: 'ask_user_question',
      questions: [{ question: 'Which region?', options: ['EU', 'US'] }],
    };
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'ask me question' }),
      message({
        _id: 'a1',
        messageType: 'bot_response',
        content: '',
        tools: [{ toolName: 'ask_user_question', toolResult: payload }],
      } as Partial<ConversationMessage>),
    ]);

    expect(messages[1].metadata?.custom?.persistedAskUserQuestion).toEqual(payload);
    expect(unansweredAskUserQuestion).toMatchObject({
      assistantMessageId: 'a1',
      payload,
      status: 'pending',
    });
  });

  it('restores a regenerate card when the tool_call is wrapped as toolData', () => {
    const inner = {
      name: 'ask_user_question',
      questions: [{ question: 'What kind of day are you having?', options: ['Productive', 'Busy'] }],
    };
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'ask me question' }),
      message({ _id: 'a1', messageType: 'bot_response', content: '' }),
      message({
        _id: 't1',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: { status: 'success', toolData: inner } }],
      } as Partial<ConversationMessage>),
    ]);

    expect(messages[1].metadata?.custom?.persistedAskUserQuestion).toEqual(inner);
    expect(unansweredAskUserQuestion).toMatchObject({
      assistantMessageId: 'a1',
      payload: inner,
      status: 'pending',
    });
  });

  it('keeps a stopped empty bot when a regenerate tool_call follows it', () => {
    const payload = {
      name: 'ask_user_question',
      questions: [{ question: 'Which region?', options: ['EU', 'US'] }],
    };
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'ask me question' }),
      message({ _id: 'a1', messageType: 'bot_response', content: '', status: 'stopped' }),
      message({
        _id: 't1',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: payload }],
      } as Partial<ConversationMessage>),
    ]);

    expect(messages.map((m) => m.role)).toEqual(['user', 'assistant']);
    expect(messages[1].metadata?.custom?.persistedAskUserQuestion).toEqual(payload);
    expect(unansweredAskUserQuestion).toMatchObject({
      assistantMessageId: 'a1',
      status: 'pending',
    });
  });

  it('keeps every question when the model asks them in separate tool_calls', () => {
    const first = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q-one',
        question: 'What should I analyze?',
        multiSelect: true,
        options: [
          { id: 'opt_summarize', label: 'Summarize the dataset', isUserInput: false },
          { id: 'opt_totals', label: 'Calculate totals', isUserInput: false },
        ],
      }],
    };
    const second = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q-two',
        question: 'Which format do you want?',
        multiSelect: false,
        options: [
          { id: 'opt_table', label: 'Table', isUserInput: false },
          { id: 'opt_chart', label: 'Chart', isUserInput: false },
        ],
      }],
    };
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'analyze xyz' }),
      message({
        _id: 't1',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: first }],
      } as Partial<ConversationMessage>),
      message({ _id: 'a1', messageType: 'bot_response', content: '' }),
      message({
        _id: 'sel1',
        messageType: 'user_query',
        content: 'User selections:\n1. "What should I analyze?" → Summarize the dataset',
      }),
      message({
        _id: 't2',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: second }],
      } as Partial<ConversationMessage>),
      message({ _id: 'a2', messageType: 'bot_response', content: '' }),
      message({
        _id: 'sel2',
        messageType: 'user_query',
        content: 'User selections:\n1. "Which format do you want?" → Table',
      }),
      message({
        _id: 'a3',
        messageType: 'bot_response',
        content: 'Please paste the xyz dataset here.',
      }),
    ]);

    const payload = messages[1].metadata?.custom?.persistedAskUserQuestion as {
      questions: Array<{ question: string }>;
    };
    expect(payload.questions.map((q) => q.question)).toEqual([
      'What should I analyze?',
      'Which format do you want?',
    ]);
    expect(messages[1].metadata?.custom?.persistedAskUserQuestionAnswers).toEqual({
      'q-one': {
        questionUuid: 'q-one',
        selectedOptionIds: ['opt_summarize'],
        userInputs: {},
      },
      'q-two': {
        questionUuid: 'q-two',
        selectedOptionIds: ['opt_table'],
        userInputs: {},
      },
    });
    expect(unansweredAskUserQuestion).toBeNull();
  });
});


describe('an agent that re-asks a question the user already answered', () => {
  const question = (uuid: string, text: string, labels: string[]) => ({
    uuid,
    question: text,
    multiSelect: false,
    options: labels.map((label, i) => ({ id: `${uuid}-o${i}`, label, isUserInput: false })),
  });
  const fiscalYear = question('q-fy', 'Which fiscal year should I use?', ['FY2024', 'Most recent matching pair']);
  const scope = question('q-scope', 'Compare revenue or headcount?', ['Revenue', 'Headcount']);
  const payload = (...questions: ReturnType<typeof question>[]) => ({ name: 'ask_user_question', questions });
  const asked = (id: string, p: ReturnType<typeof payload>) =>
    message({ _id: id, messageType: 'tool_call', tools: [{ toolName: 'ask_user_question', toolResult: p }] } as Partial<ConversationMessage>);
  const said = (id: string, content: string) => message({ _id: id, messageType: 'user_query', content });
  const replied = (id: string, content = '') => message({ _id: id, messageType: 'bot_response', content });
  const picked = 'User selections:\n1. "Which fiscal year should I use?" → Most recent matching pair';

  it('keeps the card closed when the same question comes back', () => {
    const { unansweredAskUserQuestion, messages } = loadHistoricalMessages([
      said('u1', 'Compare Asana Q3 vs Q4'), asked('t1', payload(fiscalYear)), replied('b1'),
      said('u2', picked), asked('t2', payload(fiscalYear)), replied('b2'),
    ]);

    expect(unansweredAskUserQuestion).toBeNull();
    const card = messages.find((m) => m.id === 'b1')?.metadata?.custom as Record<string, unknown>;
    expect(Object.keys(card.persistedAskUserQuestionAnswers as object)).toEqual(['q-fy']);
  });

  it('keeps it closed when the model words the same question slightly differently', () => {
    const reworded = question('q-fy-2', '  WHICH fiscal year should I use ', ['FY2024', 'Most recent matching pair']);
    const { unansweredAskUserQuestion, messages } = loadHistoricalMessages([
      said('u1', 'Compare Asana Q3 vs Q4'), asked('t1', payload(fiscalYear)), replied('b1'),
      said('u2', picked), asked('t2', payload(reworded)), replied('b2'),
    ]);

    expect(unansweredAskUserQuestion).toBeNull();
    const card = messages.find((m) => m.id === 'b1')?.metadata?.custom as { persistedAskUserQuestion: { questions: unknown[] } };
    expect(card.persistedAskUserQuestion.questions).toHaveLength(1);
  });

  it('keeps it closed when the same question comes back more than once', () => {
    const { unansweredAskUserQuestion } = loadHistoricalMessages([
      said('u1', 'Compare Asana Q3 vs Q4'), asked('t1', payload(fiscalYear)), replied('b1'),
      said('u2', picked), asked('t2', payload(fiscalYear)), replied('b2'),
      said('u3', picked), asked('t3', payload(fiscalYear)), replied('b3'),
    ]);

    expect(unansweredAskUserQuestion).toBeNull();
  });

  it('keeps it closed when only one question of a two-question card comes back', () => {
    const both =
      'User selections:\n1. "Which fiscal year should I use?" → FY2024\n2. "Compare revenue or headcount?" → Revenue';
    const { unansweredAskUserQuestion } = loadHistoricalMessages([
      said('u1', 'Compare Asana Q3 vs Q4'), asked('t1', payload(fiscalYear, scope)), replied('b1'),
      said('u2', both), asked('t2', payload(fiscalYear)), replied('b2'),
    ]);

    expect(unansweredAskUserQuestion).toBeNull();
  });

  it('still reopens the card for a question the user has not answered', () => {
    const { unansweredAskUserQuestion } = loadHistoricalMessages([
      said('u1', 'Compare Asana Q3 vs Q4'), asked('t1', payload(fiscalYear)), replied('b1'),
      said('u2', picked), asked('t2', payload(scope)), replied('b2'),
    ]);

    expect(unansweredAskUserQuestion?.assistantMessageId).toBe('b1');
    expect(unansweredAskUserQuestion?.payload.questions).toHaveLength(2);
    expect(Object.keys(unansweredAskUserQuestion?.answers ?? {})).toEqual(['q-fy']);
  });

  it('still lets the user retry when the resume came back empty without asking again', () => {
    const { unansweredAskUserQuestion } = loadHistoricalMessages([
      said('u1', 'Compare Asana Q3 vs Q4'), asked('t1', payload(fiscalYear)), replied('b1'),
      said('u2', picked), replied('b2'),
    ]);

    expect(unansweredAskUserQuestion?.assistantMessageId).toBe('b1');
  });

  it('treats the same question in a later turn as a new question', () => {
    const { unansweredAskUserQuestion } = loadHistoricalMessages([
      said('u1', 'first'), asked('t1', payload(fiscalYear)), replied('b1'),
      said('u2', picked), replied('b2', 'first answer'),
      said('u3', 'second'), asked('t3', payload(fiscalYear)), replied('b3'),
    ]);

    expect(unansweredAskUserQuestion?.assistantMessageId).toBe('b3');
  });
});
