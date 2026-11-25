/**
 * WP-51: an answer stopped by a budget beyond the run's own names that budget's scope (saved on the message and on a
 * durable answer's final part). What it says is the `budgetStopLines` extension point's; this build's default says
 * nothing of it, so the run's own spending-limit notice shows, with "continue".
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import type { ConversationMessage, MessagePart } from '../types';

// This build's default words, whichever config.ts the run resolves (an edition's own are tested with it).
vi.mock('@/config', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  budgetStopLines: (await import('../budget-stop-lines')).budgetStopLines,
}));
vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  streamSSERequest: vi.fn(),
}));

const { budgetScopeOf } = await import('../cut-short');
const { budgetStopLines } = await import('../budget-stop-lines');
const { CutShortNotice } = await import('../components/message-area/cut-short-notice');
const { loadHistoricalMessages } = await import('../runtime');
const { buildMessagePairs } = await import('../components/message-area/message-pairs');

const h = React.createElement;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(cleanup);

const notice = (props: Record<string, unknown>): string => {
  render(h(Theme, null, h(CutShortNotice, { reason: 'budget', now: new Date(Date.UTC(2026, 9, 20)), ...props })));
  return screen.getByTestId('cut-short-notice').textContent ?? '';
};

describe('WP-51: a budget stop names its scope; the default words it as the spending limit', () => {
  it.each(['pool-a', 'pool_b', 'p'] as const)('the default has no words for the %s scope', (budgetScope) => {
    expect(budgetStopLines(budgetScope, (key) => key, 'en-US', new Date())).toBeNull();
    const text = notice({ budgetScope });
    expect(text).toContain('Stopped early: spending limit reached');
    expect(text).toContain('Send “continue” to carry on.');
    expect(screen.getByTestId('cut-short-notice').getAttribute('data-budget-scope')).toBeNull();
  });

  it('C9.06: the run’s own spending limit keeps its own notice', () => {
    const text = notice({});
    expect(text).toContain('Stopped early: spending limit reached');
    expect(text).toContain('Send “continue” to carry on.');
  });

  it('C9.06: a scope beside another limit, or a value not shaped as one, is ignored', () => {
    expect(budgetScopeOf('budget', 'pool-a', undefined)).toBe('pool-a');
    expect(budgetScopeOf('turns', 'pool-a', undefined)).toBeNull();
    expect(budgetScopeOf('budget', 'Not a scope', undefined)).toBeNull();
    expect(budgetScopeOf('budget', 7, undefined)).toBeNull();
    const parts: MessagePart[] = [
      { type: 'text', content: 'So far…', isFinal: true, truncatedBy: 'budget', budgetScope: 'pool_b' },
    ];
    expect(budgetScopeOf('budget', undefined, parts)).toBe('pool_b');
  });

  it('C9.06: a reloaded conversation keeps the budget scope on the answer row and the pair', () => {
    const { messages } = loadHistoricalMessages([
      { _id: 'q', messageType: 'user_query', content: 'pipeline?', contentFormat: 'MARKDOWN', citations: [], followUpQuestions: [], feedback: [], createdAt: '2026-10-22T00:00:00.000Z', updatedAt: '2026-10-22T00:00:00.000Z' } as ConversationMessage,
      { _id: 'a', messageType: 'bot_response', content: 'So far…', contentFormat: 'MARKDOWN', citations: [], followUpQuestions: [], feedback: [], createdAt: '2026-10-22T00:00:00.000Z', updatedAt: '2026-10-22T00:00:00.000Z', truncatedBy: 'budget', budgetScope: 'pool-a' } as ConversationMessage,
    ]);
    const answer = messages.find((m) => m.role === 'assistant');
    expect((answer?.metadata?.custom as Record<string, unknown>)?.budgetScope).toBe('pool-a');
    const pairs = buildMessagePairs(messages as never, {
      isStreaming: false,
      streamingQuestion: '',
      pendingCollections: [],
      regenerateMessageId: null,
      emptyCitationMaps: { citations: {}, sources: {}, sourcesOrder: [], citationsOrder: {} } as never,
    });
    expect(pairs[0].budgetScope).toBe('pool-a');
  });

});
