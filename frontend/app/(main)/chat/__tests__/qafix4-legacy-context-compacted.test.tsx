/**
 * QAFIX4: today's (non-durable) loop tells the person when it cut a request's history with the frame the durable loop
 * sends for `x.pipeshub.context.compacted`, so the chat's existing row renders it from the token counts in the
 * reader's language; the server's English `message` on the frame is never shown by this client. The answer's saved
 * parts keep the same part, so the row is there after a reload.
 */
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { createAGUIEventHandler } from '../agui-event-handler';
import { ContextCompactedRow } from '../components/message-area/context-compacted-row';
import { loadHistoricalMessages } from '../runtime';
import type { ConversationMessage, MessagePart } from '../types';

// What `AGUIFormatter.context_compacted` sends (backend `test_qafix4_legacy_history_shapers.py` pins the same value).
const LEGACY_FRAME = {
  type: 'CUSTOM',
  name: 'context_compacted',
  runId: 'legacy-run',
  value: {
    executor: 'loop',
    outcome: 'compacted',
    beforeTokens: 108_000,
    afterTokens: 36_000,
    userMessagesKept: false,
    message: 'Context compacted · 108k → 36k tokens',
  },
};

function partsFor(frame: Record<string, unknown>): MessagePart[] {
  let parts: MessagePart[] = [];
  const handle = createAGUIEventHandler({ onParts: (next) => (parts = [...next]) });
  handle({ id: 'a', event: 'CUSTOM', data: frame });
  return parts;
}

afterEach(async () => {
  cleanup();
  await i18n.changeLanguage('en-US');
});

describe('QAFIX4: the legacy loop\'s context_compacted frame', () => {
  it('renders the existing row from the token counts', () => {
    render(<Theme><ContextCompactedRow parts={partsFor(LEGACY_FRAME)} run={null} /></Theme>);
    const row = screen.getByTestId('context-compacted-row').textContent ?? '';
    expect(row).toContain('Context compacted · 108k → 36k tokens');
    expect(row).not.toContain('your messages kept');
  });

  it('a saved legacy answer replays the row after a reload', () => {
    // What `TranscriptCollector.record_context_compacted` saves first among the answer's parts.
    const saved = { type: 'context_compacted', compacted: { ...LEGACY_FRAME.value } } as unknown as MessagePart;
    delete (saved.compacted as unknown as Record<string, unknown>).message;
    const { messages } = loadHistoricalMessages([
      { _id: 'u1', messageType: 'user_query', content: 'Tally these labels', createdAt: '2026-10-20T06:30:00Z' },
      {
        _id: 'b1',
        messageType: 'bot_response',
        content: 'Tallied.',
        parts: [saved, { type: 'text', content: 'Tallied.', isFinal: true }],
        createdAt: '2026-10-20T06:37:00Z',
      },
    ] as ConversationMessage[]);
    const custom = messages[1]?.metadata?.custom as { persistedParts?: MessagePart[] } | undefined;
    render(<Theme><ContextCompactedRow parts={custom?.persistedParts} run={null} /></Theme>);
    expect(screen.getByTestId('context-compacted-row').textContent).toContain('Context compacted · 108k → 36k tokens');
  });

  it('renders in the reader\'s language, not the server\'s English text', async () => {
    await i18n.changeLanguage('de-DE');
    render(<Theme><ContextCompactedRow parts={partsFor(LEGACY_FRAME)} run={null} /></Theme>);
    const row = screen.getByTestId('context-compacted-row').textContent ?? '';
    expect(row).toContain('Kontext komprimiert · 108k → 36k Tokens');
    expect(row).not.toContain('Context compacted');
  });
});
