import { describe, it, expect } from 'vitest';
import { loadHistoricalMessages } from '../runtime';
import type { ConversationMessage, UiCardPayload } from '../types';

function base(overrides: Partial<ConversationMessage>): ConversationMessage {
  return {
    _id: 'm-0',
    messageType: 'bot_response',
    content: '',
    contentFormat: 'MARKDOWN',
    citations: [],
    followUpQuestions: [],
    referenceData: [],
    modelInfo: {} as ConversationMessage['modelInfo'],
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    feedback: [],
    ...overrides,
  };
}

function card(cardId: string, cardType: string): UiCardPayload {
  return { cardType, cardId, payload: {}, actions: [] };
}

function cardMessage(id: string, payload: UiCardPayload): ConversationMessage {
  return base({
    _id: id,
    messageType: 'tool_call',
    tools: [
      {
        toolName: 'ui_card',
        toolResult: payload as unknown as Record<string, unknown>,
      },
    ] as ConversationMessage['tools'],
  });
}

describe('loadHistoricalMessages — ui cards', () => {
  it('keeps every card of a turn rather than only the last one', () => {
    // A single turn can emit a prereq-check card, a dry-run card and a
    // workflow-updated card; keying by assistant id used to drop all but one.
    const { uiCards } = loadHistoricalMessages([
      base({ _id: 'u-1', messageType: 'user_query', content: 'schedule it' }),
      cardMessage('t-1', card('c-1', 'prereq_check')),
      cardMessage('t-2', card('c-2', 'workflow_dry_run')),
      cardMessage('t-3', card('c-3', 'workflow_updated')),
      base({ _id: 'a-1', content: 'done' }),
    ]);

    expect(uiCards['a-1']?.map((c) => c.cardId)).toEqual(['c-1', 'c-2', 'c-3']);
  });

  it('attaches cards to the assistant message that follows them', () => {
    const { uiCards } = loadHistoricalMessages([
      cardMessage('t-1', card('c-1', 'prereq_check')),
      base({ _id: 'a-1', content: 'first' }),
      cardMessage('t-2', card('c-2', 'workflow_updated')),
      base({ _id: 'a-2', content: 'second' }),
    ]);

    expect(uiCards['a-1']?.map((c) => c.cardId)).toEqual(['c-1']);
    expect(uiCards['a-2']?.map((c) => c.cardId)).toEqual(['c-2']);
  });

  it('ignores malformed card payloads', () => {
    const { uiCards } = loadHistoricalMessages([
      cardMessage('t-1', { cardType: '', cardId: '', payload: {}, actions: [] }),
      base({ _id: 'a-1', content: 'done' }),
    ]);

    expect(uiCards['a-1']).toBeUndefined();
  });

  it('carries run metadata on a workflow-run bot_response', () => {
    const { messages } = loadHistoricalMessages([
      base({
        _id: 'a-1',
        content: '## Report\n\nBody',
        tools: [
          {
            toolName: 'workflow_run_result',
            toolResult: { runId: 'run-1', status: 'succeeded' },
          },
        ] as ConversationMessage['tools'],
      }),
    ]);

    const runMessage = messages.find((m) => m.metadata?.custom?.workflowRun);
    expect(runMessage?.id).toBe('a-1');
    expect((runMessage?.content as Array<{ text: string }>)[0].text).toBe('## Report\n\nBody');
  });

  it('recovers the answer text from a legacy tool_call run result', () => {
    const { messages, uiCards } = loadHistoricalMessages([
      base({
        _id: 't-1',
        messageType: 'tool_call',
        content: '',
        tools: [
          {
            toolName: 'workflow_run_result',
            toolResult: { runId: 'run-1', status: 'succeeded', outputSummary: '# Old' },
          },
        ] as ConversationMessage['tools'],
      }),
      base({ _id: 'a-1', content: 'done' }),
    ]);

    const runMessage = messages.find((m) => m.metadata?.custom?.workflowRun);
    expect(runMessage?.id).toBe('t-1');
    expect((runMessage?.content as Array<{ text: string }>)[0].text).toBe('# Old');
    expect(uiCards['a-1']).toBeUndefined();
  });
});
