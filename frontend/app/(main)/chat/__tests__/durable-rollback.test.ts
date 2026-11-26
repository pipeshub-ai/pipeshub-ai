/**
 * P1b T3 (MG-FE): with the harness flag off, a conversation a durable run
 * steered loads from the conversation GET like any other. Its saved parts
 * (the Node projection's vectors) reach the timeline unchanged: a message the
 * run took sits where it took it, one it never took after the answer, marked.
 */
import React from 'react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { AgentActivityTimeline } from '../components/message-area/agent-activity';
import type { ConversationMessage, MessagePart } from '../types';
import '@/lib/__tests__/test-i18n';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const { loadHistoricalMessages } = await import('../runtime');

afterEach(() => cleanup());

const JOURNALS = join(__dirname, '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'journals');
const savedParts = (scenario: string): MessagePart[] =>
  (JSON.parse(readFileSync(join(JOURNALS, `${scenario}.parts.json`), 'utf8')) as { parts: MessagePart[] }).parts;

function message(overrides: Partial<ConversationMessage>): ConversationMessage {
  return {
    _id: 'm',
    messageType: 'user_query',
    content: '',
    contentFormat: 'MARKDOWN',
    citations: [],
    followUpQuestions: [],
    feedback: [],
    createdAt: '2026-10-02T00:00:00.000Z',
    updatedAt: '2026-10-02T00:00:00.000Z',
    ...overrides,
  } as ConversationMessage;
}

/** The parts a reloaded conversation hands its answer's timeline. */
function reloaded(parts: MessagePart[], answer: string): MessagePart[] {
  const { messages } = loadHistoricalMessages([
    message({ _id: 'q', messageType: 'user_query', content: 'Export the Q3 report' }),
    message({ _id: 'a', messageType: 'bot_response', content: answer, parts } as Partial<ConversationMessage>),
  ]);
  expect(messages.map((m) => m.role)).toEqual(['user', 'assistant']);
  return messages[1].metadata?.custom?.persistedParts as MessagePart[];
}

const renderTimeline = (parts: MessagePart[]) =>
  render(React.createElement(Theme, null, React.createElement(AgentActivityTimeline, { parts, isStreaming: false })));

describe('MG-FE: a durable conversation loaded with the flag off', () => {
  for (const scenario of ['agent_steered_during_tool', 'agent_steered_after_answer']) {
    it(`${scenario}: the steering message renders where the run took it`, () => {
      const saved = savedParts(scenario);
      const answer = saved.find((part) => part.isFinal)?.content ?? '';
      const parts = reloaded(saved, answer);
      expect(parts).toEqual(saved);

      const { container } = renderTimeline(parts);
      const steering = screen.getAllByTestId('steering-message');
      expect(steering).toHaveLength(1);
      const taken = saved.find((part) => part.type === 'user_input')!;
      expect(steering[0].textContent).toContain(taken.content);
      expect(steering[0].textContent).not.toContain('The task ended before it could use this message.');
      if (scenario === 'agent_steered_during_tool') {
        // After the tool call it followed, as the run saw it.
        const text = container.textContent ?? '';
        expect(text.indexOf('Export report')).toBeGreaterThanOrEqual(0);
        expect(text.indexOf('Export report')).toBeLessThan(text.indexOf(taken.content!));
      }
    });
  }

  it('a message the run never took renders after the answer, marked as not taken', () => {
    const saved: MessagePart[] = [
      ...savedParts('agent_steered_during_tool'),
      { type: 'user_input', content: 'And Q1?', messageSeq: 2, consumed: false },
    ];
    const parts = reloaded(saved, 'The Q3 report is exported.');
    renderTimeline(parts);
    const steering = screen.getAllByTestId('steering-message');
    expect(steering).toHaveLength(2);
    expect(steering[1].textContent).toContain('And Q1?');
    expect(steering[1].textContent).toContain('The task ended before it could use this message.');
  });
});
