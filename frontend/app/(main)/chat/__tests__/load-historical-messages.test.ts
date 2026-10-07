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
const { maxSeq } = await import('../utils/merge-messages');

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

  it('keeps the card on its own row when the user chats on past an unanswered question', () => {
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
    expect(unansweredAskUserQuestion?.assistantMessageId).toBe('a1');
    expect(unansweredAskUserQuestion?.answers).toMatchObject({
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

  it('stamps an error row with its seq and the feed rev, so a later row is merged after it', () => {
    const { messages } = loadHistoricalMessages(
      [
        message({ _id: 'q', messageType: 'user_query', content: 'hello', seq: 1 } as Partial<ConversationMessage>),
        message({ _id: 'err', messageType: 'error', content: 'boom', seq: 2 } as Partial<ConversationMessage>),
      ],
      { rev: 7 },
    );
    expect(messages[1].metadata?.custom).toMatchObject({ failed: true, seq: 2, rev: 7 });
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

describe('loadHistoricalMessages, collaboration fields', () => {
  const alice = { userId: 'a', displayName: 'Alice' };
  const bob = { userId: 'b', displayName: 'Bob' };
  const ASK = { name: 'ask_user_question', questions: [{ uuid: 'q1', question: 'Which region?', options: [{ id: 'eu', label: 'EU' }] }] };

  it('copies author, seq, clientMessageId and filesShared onto the user row, and requestedBy onto the answer (FE-08)', () => {
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'Hi', seq: 1, author: alice, clientMessageId: 'c1', filesShared: true }),
      message({ _id: 'a', messageType: 'bot_response', content: 'Hello', seq: 2, requestedBy: alice }),
    ]);

    expect(messages[0].metadata?.custom).toMatchObject({ seq: 1, author: alice, clientMessageId: 'c1', filesShared: true });
    expect(messages[1].metadata?.custom).toMatchObject({ seq: 2, requestedBy: alice });
  });

  it('names the people on a detail-shaped payload (bare requestedBy id plus author) and gives the tab a baseSeq', () => {
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q1', messageType: 'user_query', content: 'Hi', seq: 1, author: alice }),
      message({ _id: 'a1', messageType: 'bot_response', content: 'Hello', seq: 2, author: alice, requestedBy: 'a' as never }),
      message({ _id: 'q2', messageType: 'user_query', content: 'Me too', seq: 3, author: bob }),
      message({ _id: 'a2', messageType: 'bot_response', content: 'Sure', seq: 4, author: bob, requestedBy: 'b' as never }),
    ]);

    expect(messages[1].metadata?.custom).toMatchObject({ requestedBy: alice });
    expect(messages[3].metadata?.custom).toMatchObject({ requestedBy: bob });
    expect(messages.map((m) => (m.metadata?.custom as { author?: unknown }).author).filter(Boolean)).not.toContain(null);
    expect(maxSeq(messages)).toBe(4);
  });

  it('keeps a null author so the row renders "Former member"', () => {
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'Hi', author: null }),
      message({ _id: 'a', messageType: 'bot_response', content: 'Hello', requestedBy: null }),
    ]);

    expect(messages[0].metadata?.custom).toHaveProperty('author', null);
    expect(messages[1].metadata?.custom).toHaveProperty('requestedBy', null);
  });

  it('adds no collaboration keys to a solo conversation', () => {
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'Hi' }),
      message({ _id: 'a', messageType: 'bot_response', content: 'Hello' }),
    ]);

    for (const m of messages) {
      expect(Object.keys(m.metadata?.custom ?? {})).not.toEqual(
        expect.arrayContaining(['author']),
      );
      expect(m.metadata?.custom).not.toHaveProperty('requestedBy');
      expect(m.metadata?.custom).not.toHaveProperty('seq');
    }
  });

  it('binds an open card to its tool_call row and to the person it was put to', () => {
    const { unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'Plan it', author: alice }),
      message({ _id: 'b', messageType: 'bot_response', content: '' }),
      message({ _id: 'tc', messageType: 'tool_call', requestedBy: bob, tools: [{ toolName: 'ask_user_question', toolResult: ASK }] } as Partial<ConversationMessage>),
    ]);

    expect(unansweredAskUserQuestion).toMatchObject({ toolCallMessageId: 'tc', requestedBy: bob, status: 'pending' });
  });

  it('binds a card that precedes its bot row to the tool_call row', () => {
    const { unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'Plan it', author: alice }),
      message({ _id: 'tc', messageType: 'tool_call', requestedBy: alice, tools: [{ toolName: 'ask_user_question', toolResult: ASK }] } as Partial<ConversationMessage>),
      message({ _id: 'b', messageType: 'bot_response', content: '' }),
    ]);

    expect(unansweredAskUserQuestion).toMatchObject({ toolCallMessageId: 'tc', requestedBy: alice });
  });

  it('binds a card carried by the bot row itself to that row', () => {
    const { unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'Plan it', author: alice }),
      message({ _id: 'b', messageType: 'bot_response', content: '', requestedBy: alice, tools: [{ toolName: 'ask_user_question', toolResult: ASK }] } as Partial<ConversationMessage>),
    ]);

    expect(unansweredAskUserQuestion).toMatchObject({ toolCallMessageId: 'b', requestedBy: alice });
  });

  it('falls back to the question author for a legacy card with no requestedBy', () => {
    const { unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'Plan it', author: bob }),
      message({ _id: 'b', messageType: 'bot_response', content: '' }),
      message({ _id: 'tc', messageType: 'tool_call', tools: [{ toolName: 'ask_user_question', toolResult: ASK }] } as Partial<ConversationMessage>),
    ]);

    expect(unansweredAskUserQuestion).toMatchObject({ requestedBy: bob });
  });

  it('reads a bare user id in requestedBy (what the API stores) as that person, so the asker is not shown as a former member', () => {
    const { unansweredAskUserQuestion, messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'Plan it' }),
      message({ _id: 'b', messageType: 'bot_response', content: 'Done', requestedBy: 'b' as unknown as ConversationMessage['requestedBy'] }),
      message({ _id: 'tc', messageType: 'tool_call', requestedBy: 'b', tools: [{ toolName: 'ask_user_question', toolResult: ASK }] } as unknown as Partial<ConversationMessage>),
    ]);

    expect(unansweredAskUserQuestion).toMatchObject({ toolCallMessageId: 'tc', requestedBy: { userId: 'b', displayName: null } });
    expect(messages[1].metadata?.custom).toMatchObject({ requestedBy: { userId: 'b', displayName: null } });
  });

  it('leaves the asker unknown in a solo conversation', () => {
    const { unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'Plan it' }),
      message({ _id: 'b', messageType: 'bot_response', content: '' }),
      message({ _id: 'tc', messageType: 'tool_call', tools: [{ toolName: 'ask_user_question', toolResult: ASK }] } as Partial<ConversationMessage>),
    ]);

    expect(unansweredAskUserQuestion).not.toHaveProperty('requestedBy');
  });

  it('a note becomes its own system row with its author and mentions, between the question and its answer', () => {
    const author = { userId: 'u-bob', displayName: 'Bob' };
    const { messages } = loadHistoricalMessages(
      [
        message({ _id: 'q', messageType: 'user_query', content: 'Question', seq: 1 }),
        message({ _id: 'n', messageType: 'note', content: 'fyi <@user:u-x>', mentions: [{ type: 'user', id: 'u-x' }], author, seq: 2 }),
        message({ _id: 'a', messageType: 'bot_response', content: 'Answer', seq: 3 }),
      ],
      { rev: 4 },
    );
    expect(messages.map((m) => [m.id, m.role])).toEqual([['q', 'user'], ['n', 'system'], ['a', 'assistant']]);
    expect(messages[1].metadata?.custom).toMatchObject({
      messageType: 'note',
      seq: 2,
      author,
      mentions: [{ type: 'user', id: 'u-x' }],
    });
  });

  describe('agent drafts', () => {
    const draft = { draftId: 'd1', name: 'Offer drafter', handleSuggestion: 'offer-drafter', toolsets: [] };
    const draftRow = (toolResult: unknown, extra: Partial<ConversationMessage> = {}) =>
      message({
        _id: 'card',
        messageType: 'tool_call',
        tools: [{ toolName: 'draft_agent', toolResult }],
        author: { userId: 'u-a', displayName: 'Alice' },
        ...extra,
      } as Partial<ConversationMessage>);

    it('stamps the draft saved before an answer onto that answer, not onto its own row', () => {
      const { messages } = loadHistoricalMessages([
        message({ _id: 'q', messageType: 'user_query', content: 'make an agent' }),
        draftRow(draft),
        message({ _id: 'a', messageType: 'bot_response', content: 'Drafted.' }),
      ]);

      expect(messages.map((m) => m.role)).toEqual(['user', 'assistant']);
      const custom = messages[1].metadata?.custom as Record<string, unknown>;
      expect(custom.persistedAgentDraft).toEqual(draft);
      expect(custom.agentDraftAuthor).toBe('Alice');
    });

    it('keeps the redacted placeholder as it came', () => {
      const { messages } = loadHistoricalMessages([
        message({ _id: 'q', messageType: 'user_query', content: 'make an agent' }),
        draftRow({ redacted: true, authorId: 'u-a' }),
        message({ _id: 'a', messageType: 'bot_response', content: 'Drafted.' }),
      ]);
      expect((messages[1].metadata?.custom as Record<string, unknown>).persistedAgentDraft).toEqual({
        redacted: true,
        authorId: 'u-a',
      });
    });

    it('a regenerated turn stores the draft after its answer; it still lands on that answer', () => {
      const { messages } = loadHistoricalMessages([
        message({ _id: 'q', messageType: 'user_query', content: 'make an agent' }),
        message({ _id: 'a', messageType: 'bot_response', content: 'Drafted.' }),
        draftRow(draft),
      ]);
      expect((messages[1].metadata?.custom as Record<string, unknown>).persistedAgentDraft).toEqual(draft);
    });

    it('a draft with no answer does not leak onto the next turn', () => {
      const { messages } = loadHistoricalMessages([
        message({ _id: 'q1', messageType: 'user_query', content: 'make an agent' }),
        draftRow(draft),
        message({ _id: 'q2', messageType: 'user_query', content: 'something else' }),
        message({ _id: 'a2', messageType: 'bot_response', content: 'Sure.' }),
      ]);
      const answer = messages.find((m) => m.id === 'a2');
      expect((answer?.metadata?.custom as Record<string, unknown>).persistedAgentDraft).toBeUndefined();
    });

    it('ignores a draft_agent row without a draft in it', () => {
      const { messages } = loadHistoricalMessages([
        message({ _id: 'q', messageType: 'user_query', content: 'x' }),
        draftRow('not a draft'),
        message({ _id: 'a', messageType: 'bot_response', content: 'ok' }),
      ]);
      expect((messages[1].metadata?.custom as Record<string, unknown>).persistedAgentDraft).toBeUndefined();
    });
  });
});
