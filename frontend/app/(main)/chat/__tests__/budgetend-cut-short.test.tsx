/**
 * BUDGETEND: an answer a limit cut short (its spending limit, turn limit or working time) shows the answer and a
 * notice naming the limit. The saved message names it (durable and legacy runs alike); a durable answer's final text
 * part names it too. The existing "This reply stopped because it reached its spending limit." stays for a run that
 * has no answer at all.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import type { ConversationMessage, MessagePart } from '../types';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  streamSSERequest: vi.fn(),
}));

const { cutShortOf, cutShortReason } = await import('../cut-short');
const { CutShortNotice } = await import('../components/message-area/cut-short-notice');
const { loadHistoricalMessages } = await import('../runtime');
const { buildMessagePairs } = await import('../components/message-area/message-pairs');
const { answerErrorText } = await import('../harness-errors');

const h = React.createElement;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(cleanup);

function message(overrides: Partial<ConversationMessage>): ConversationMessage {
  return {
    _id: 'm',
    messageType: 'user_query',
    content: '',
    contentFormat: 'MARKDOWN',
    citations: [],
    followUpQuestions: [],
    feedback: [],
    createdAt: '2026-10-22T00:00:00.000Z',
    updatedAt: '2026-10-22T00:00:00.000Z',
    ...overrides,
  } as ConversationMessage;
}

describe('BUDGETEND: an answer a limit cut short', () => {
  it('names the limit from the message, else from its final text part', () => {
    expect(cutShortReason('budget')).toBe('budget');
    expect(cutShortReason('tokens')).toBeNull();
    expect(cutShortOf('time', undefined)).toBe('time');
    const parts: MessagePart[] = [
      { type: 'text', content: 'Checking Slack…' },
      { type: 'text', content: 'Here is what I found.', isFinal: true, truncatedBy: 'turns' },
    ];
    expect(cutShortOf(undefined, parts)).toBe('turns');
    expect(cutShortOf(undefined, [{ type: 'text', content: 'Done.', isFinal: true }])).toBeNull();
  });

  it.each([
    ['budget', 'Stopped early: spending limit reached'],
    ['turns', 'Stopped early: turn limit reached'],
    ['time', 'Stopped early: time limit reached'],
    // BU-06: steps that kept repeating without progress end the answer the way a limit does.
    ['stall', 'Stopped early: its steps kept repeating without progress'],
  ] as const)('shows the %s notice with how to carry on', (reason, text) => {
    render(h(Theme, null, h(CutShortNotice, { reason })));
    const notice = screen.getByTestId('cut-short-notice');
    expect(notice.getAttribute('role')).toBe('note');
    expect(notice.textContent).toContain(text);
    expect(notice.textContent).toContain('Send “continue” to carry on.');
  });

  it('is worded in every shipped language', async () => {
    await i18n.changeLanguage('de-DE');
    render(h(Theme, null, h(CutShortNotice, { reason: 'budget' })));
    expect(screen.getByTestId('cut-short-notice').textContent).toContain('Ausgabenlimit');
    await i18n.changeLanguage('en-US');
  });

  it('a reloaded conversation keeps the limit on the answer row, and the pair hands it to the answer', () => {
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: "what's happening in slack?" }),
      message({ _id: 'a', messageType: 'bot_response', content: 'Here is what I found so far.', truncatedBy: 'budget' }),
    ]);
    const answer = messages.find((m) => m.role === 'assistant');
    expect((answer?.metadata?.custom as Record<string, unknown>)?.truncatedBy).toBe('budget');
    const pairs = buildMessagePairs(messages as never, {
      isStreaming: false,
      streamingQuestion: '',
      pendingCollections: [],
      regenerateMessageId: null,
      emptyCitationMaps: { citations: {}, sources: {}, sourcesOrder: [], citationsOrder: {} } as never,
    });
    expect(pairs[0].truncatedBy).toBe('budget');
  });

  it('an answer no limit cut short carries nothing', () => {
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'hi' }),
      message({ _id: 'a', messageType: 'bot_response', content: 'Hello.' }),
    ]);
    const answer = messages.find((m) => m.role === 'assistant');
    expect((answer?.metadata?.custom as Record<string, unknown>)?.truncatedBy).toBeUndefined();
  });

  it('the no-answer error keeps its own wording', () => {
    expect(answerErrorText('budget_exhausted')).toBe('This reply stopped because it reached its spending limit. Ask your admin to raise the limit, or ask for less at a time.');
  });
});
