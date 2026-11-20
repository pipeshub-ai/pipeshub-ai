/**
 * The sentence an agent says before a tool call is first drawn in the run's box, never as the answer and then moved
 * there. Replayed through the real stream handler as the server sends it (a word or a few characters at a time, then
 * the tool call), with the row redrawn after every event as the chat draws it while it streams.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { MessagePart, StatusMessage } from '../../../types';
import type { StreamMessageCallbacks } from '../../../api';
import type { SSEEvent } from '@/lib/api';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: () => ({ query: 'q' }) }));
vi.mock('@/lib/hooks/use-chat-speech-config', () => ({
  useChatSpeechConfig: () => ({ hasTts: false, isLoading: false }),
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('../answer-content', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../answer-content')>()),
  AnswerContent: ({ content }: { content: string }) => <div data-testid="answer">{content}</div>,
}));
vi.mock('@/knowledge-base/api', () => ({ KnowledgeBaseApi: {} }));

import { ChatResponse } from '../chat-response';
import { createAGUIEventHandler } from '../../../agui-event-handler';

const RUN = 'run-1';
const frame = (type: string, fields: Record<string, unknown> = {}): SSEEvent => ({ event: type, data: { type, ...fields } });

/** A model turn's text in pieces of 1-4 characters, as the server streams it. */
function turn(messageId: string, text: string): SSEEvent[] {
  const out = [frame('TEXT_MESSAGE_START', { messageId, role: 'assistant', runId: RUN })];
  for (let at = 0, n = 0; at < text.length; n += 1) {
    const next = Math.min(text.length, at + 1 + (n % 4));
    out.push(frame('TEXT_MESSAGE_CONTENT', { messageId, delta: text.slice(at, next), runId: RUN }));
    at = next;
  }
  out.push(frame('TEXT_MESSAGE_END', { messageId, runId: RUN }));
  return out;
}
const tool = (id: string, name: string, result = true): SSEEvent[] => [
  frame('TOOL_CALL_START', { toolCallId: id, toolCallName: name, runId: RUN }),
  frame('TOOL_CALL_ARGS', { toolCallId: id, delta: '{}', runId: RUN }),
  ...(result ? [frame('TOOL_CALL_END', { toolCallId: id, runId: RUN }), frame('TOOL_CALL_RESULT', { toolCallId: id, content: 'ok', runId: RUN })] : []),
];
const start = [frame('RUN_STARTED', { threadId: 't1', runId: RUN }), frame('STATE_SNAPSHOT', { snapshot: { status: 'calling_llm' } })];

const ASK = 'I’ll ask which team you’re on first, then pause here for your answer.';
const SEARCH = 'I’ll search your collections for the code freeze rules and pull the clearest sources.';
const UPDATE = 'I found the release guide; I’m checking its full procedure before I answer.';
const ANSWER =
  'Code freeze starts ten days before each release. The release captain cuts the branch. QA signs the checklist.\n\n' +
  'Staging runs for two days before the release goes out.';

/** The row after every event, drawn from what the handler gave the chat so far (as `streamMessageForSlot` keeps it). */
function replay(events: SSEEvent[], onEach: (view: HTMLElement, step: string) => void, final = ANSWER) {
  let content = '';
  let parts: MessagePart[] = [];
  let status: StatusMessage | null = null;
  const callbacks: StreamMessageCallbacks = {
    onChunk: ({ accumulated }) => {
      content = accumulated;
      // Text flowing supersedes a status, as the chat does.
      if (accumulated) status = null;
    },
    onParts: (next) => {
      parts = next;
    },
    onStatus: ({ status: kind, message }) => {
      status = { id: `s-${kind}`, status: kind, message: message ?? '', timestamp: '' };
    },
  };
  const handle = createAGUIEventHandler(callbacks);
  const draw = (streaming: boolean) => (
    <Theme>
      <ChatResponse
        question="What should my team check before a release?"
        answer={streaming ? '' : final}
        isStreaming={streaming}
        streamingContent={content}
        streamingParts={parts}
        persistedParts={parts}
        currentStatusMessage={status}
        messageId="row-1"
        isLastMessage
      />
    </Theme>
  );
  const { container, rerender } = render(draw(true));
  events.forEach((event, i) => {
    handle(event);
    rerender(draw(true));
    onEach(container, `event ${i} ${event.event}`);
  });
  return { container, done: () => rerender(draw(false)) };
}

/** Where `sentence` is drawn: in the run's box, outside it, or nowhere. */
function placeOf(view: HTMLElement, sentence: string): 'box' | 'outside' | 'none' {
  const start = sentence.slice(0, 16);
  const holders = [...view.querySelectorAll('*')].filter(
    (el) => [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && (n.nodeValue ?? '').includes(start)),
  );
  if (holders.length === 0) return 'none';
  return holders.every((el) => el.closest('[data-testid="chat-activity-card"]')) ? 'box' : 'outside';
}

afterEach(cleanup);

describe('a sentence said before a tool call', () => {
  it('is first drawn in the run’s box, never as the answer: a question asked', () => {
    const seen: string[] = [];
    replay([...start, ...turn('m1', ASK), ...tool('call-1', 'internaltools__ask_user_question', false)], (view, step) => {
      const place = placeOf(view, ASK);
      seen.push(place);
      expect(place, step).not.toBe('outside');
    });
    expect(seen.at(-1)).toBe('box');
  });

  it('is first drawn in the run’s box, never as the answer: searches, an update, then the answer', () => {
    const events = [
      ...start,
      ...turn('m1', SEARCH),
      ...tool('call-1', 'knowledgegraph__search'),
      ...turn('m2', UPDATE),
      ...tool('call-2', 'knowledgegraph__fetch_record'),
      ...turn('m3', ANSWER),
    ];
    const answerShownAt: number[] = [];
    const { container, done } = replay(events, (view, step) => {
      expect(placeOf(view, SEARCH), step).not.toBe('outside');
      expect(placeOf(view, UPDATE), step).not.toBe('outside');
      if (placeOf(view, ANSWER) === 'outside') answerShownAt.push(answerShownAt.length);
    });
    expect(placeOf(container, SEARCH)).toBe('box');
    expect(placeOf(container, UPDATE)).toBe('box');
    // The answer streams as the answer once it reads as one, before the run ends.
    expect(answerShownAt.length).toBeGreaterThan(0);
    done();
    expect(placeOf(container, ANSWER)).toBe('outside');
  });

  it('keeps saying it is thinking while the sentence is held', () => {
    let held = 0;
    replay([...start, ...turn('m1', ASK)], (view, step) => {
      if (!(view.textContent ?? '').includes('I’ll ask')) {
        held += 1;
        expect(view.textContent, step).toContain('Thinking…');
      }
    });
    expect(held).toBeGreaterThan(10);
  });

  it('shows a short answer, held while it streams, once the run ends', () => {
    const SHORT = 'Hi. How can I help?';
    const { container, done } = replay(
      [...start, ...turn('m1', SHORT)],
      (view, step) => expect(placeOf(view, SHORT), step).toBe('none'),
      SHORT,
    );
    done();
    expect(placeOf(container, SHORT)).toBe('outside');
  });
});
