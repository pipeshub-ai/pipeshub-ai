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

  it('does not treat an interstitial user message as answering the card', () => {
    const payload = {
      name: 'ask_user_question',
      questions: [{ question: 'Which region?', options: ['EU', 'US'] }],
    };
    const { unansweredAskUserQuestion } = loadHistoricalMessages([
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

    expect(unansweredAskUserQuestion).toMatchObject({
      assistantMessageId: 'a1',
      status: 'pending',
    });
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
    expect(messages[1].metadata?.custom?.persistedAskUserQuestion).toEqual(first);
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
