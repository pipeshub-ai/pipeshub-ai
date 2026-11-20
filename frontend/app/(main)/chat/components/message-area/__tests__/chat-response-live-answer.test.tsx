/**
 * The answer's words show the moment they arrive. Replayed through the real stream handler as the server sends it (a
 * word or a few characters at a time, then any tool call), with the row redrawn after every event as the chat draws it
 * while it streams.
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

const SHORT = 'Hi. How can I help?';
const ASK = 'I’ll ask which team you’re on first, then pause here for your answer.';
const SEARCH = 'I’ll search your collections for the code freeze rules and pull the clearest sources.';
const UPDATE = 'I found the release guide; I’m checking its full procedure before I answer.';
const ANSWER =
  'Code freeze starts ten days before each release. The release captain cuts the branch. QA signs the checklist.\n\n' +
  'Staging runs for two days before the release goes out.';

/** What one step of the replay drew, and the text the handler had streamed as the answer by then. */
interface Step {
  view: HTMLElement;
  name: string;
  streamed: string;
}

/** The row after every event, drawn from what the handler gave the chat so far (as `streamMessageForSlot` keeps it). */
function replay(events: SSEEvent[], onEach: (step: Step) => void, final = ANSWER) {
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
  // Saved as the server saves the turn: text the run ends on is the answer, not one of its steps.
  const saved = () =>
    parts.map((part, i) => (i === parts.length - 1 && part.type === 'text' ? { ...part, isFinal: true } : part));
  const draw = (streaming: boolean) => (
    <Theme>
      <ChatResponse
        question="What should my team check before a release?"
        answer={streaming ? '' : final}
        isStreaming={streaming}
        streamingContent={content}
        streamingParts={parts}
        persistedParts={streaming ? parts : saved()}
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
    onEach({ view: container, name: `event ${i} ${event.event}`, streamed: content });
  });
  return { container, done: () => rerender(draw(false)) };
}

/** Where `sentence` is drawn: in the run's box, outside it (as the answer), both, or nowhere. */
function placesOf(view: HTMLElement, sentence: string): string {
  const start = sentence.slice(0, 16);
  const holders = [...view.querySelectorAll('*')].filter(
    (el) => [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && (n.nodeValue ?? '').includes(start)),
  );
  const places = new Set(holders.map((el) => (el.closest('[data-testid="chat-activity-card"]') ? 'box' : 'outside')));
  return places.size === 0 ? 'none' : [...places].sort().join('+');
}

const answerText = (view: HTMLElement) => view.querySelector('[data-testid="answer"]')?.textContent ?? null;

afterEach(cleanup);

describe('the answer while it streams', () => {
  it('shows a short one-line answer from the first frame that carries a word, and each word as it arrives', () => {
    let drawnSteps = 0;
    replay([...start, ...turn('m1', SHORT)], ({ view, name, streamed }) => {
      if (!streamed) return;
      drawnSteps += 1;
      expect(answerText(view), name).toBe(streamed);
    });
    expect(drawnSteps).toBeGreaterThan(5);
  });

  it('says it is thinking until the first words arrive, and not while they do', () => {
    let wordSteps = 0;
    replay([...start, ...turn('m1', SHORT)], ({ view, name, streamed }) => {
      if (!streamed) {
        expect(view.textContent, name).toContain('Thinking…');
      } else if (name.endsWith('TEXT_MESSAGE_CONTENT')) {
        wordSteps += 1;
        expect(view.textContent, name).not.toContain('Thinking…');
      }
    });
    expect(wordSteps).toBeGreaterThan(5);
  });

  it('keeps the words it showed when the run ends', () => {
    const { container, done } = replay([...start, ...turn('m1', SHORT)], () => {}, SHORT);
    done();
    expect(answerText(container)).toBe(SHORT);
  });
});

describe('a sentence said before a tool call', () => {
  it('shows as the answer until the call arrives, then only in the run’s box', () => {
    const seen: string[] = [];
    replay([...start, ...turn('m1', ASK), ...tool('call-1', 'internaltools__ask_user_question', false)], ({ view, name, streamed }) => {
      const place = placesOf(view, ASK);
      seen.push(place);
      expect(place, name).not.toBe('box+outside');
      if (name.endsWith('TEXT_MESSAGE_CONTENT') && streamed.length >= 16) expect(place, name).toBe('outside');
    });
    expect(seen).toContain('outside');
    expect(seen.at(-1)).toBe('box');
  });

  it('searches, an update, then the answer: each step’s sentence ends in the box, the answer under it', () => {
    const events = [
      ...start,
      ...turn('m1', SEARCH),
      ...tool('call-1', 'knowledgegraph__search'),
      ...turn('m2', UPDATE),
      ...tool('call-2', 'knowledgegraph__fetch_record'),
      ...turn('m3', ANSWER),
    ];
    const { container, done } = replay(events, ({ view, name }) => {
      for (const sentence of [SEARCH, UPDATE, ANSWER]) expect(placesOf(view, sentence), name).not.toBe('box+outside');
    });
    expect(placesOf(container, SEARCH)).toBe('box');
    expect(placesOf(container, UPDATE)).toBe('box');
    expect(placesOf(container, ANSWER)).toBe('outside');
    done();
    expect(placesOf(container, ANSWER)).toBe('outside');
  });
});
