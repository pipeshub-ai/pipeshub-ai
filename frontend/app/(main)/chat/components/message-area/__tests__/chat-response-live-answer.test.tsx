/**
 * The answer's words show in one place only. Text being written may be a sentence said before a step or the answer, and
 * nothing tells the two apart before the next event: it waits, with the run saying it is thinking, until a step settles
 * it into the run's box, it reads as the answer, or the run ends. Replayed through the real stream handler as the
 * server sends it (a word or a few characters at a time, then any tool call), with the row redrawn after every event
 * as the chat draws it while it streams.
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
/** The root run's end, as the server sends it before the saved turn: the text it ended on is the answer. */
const finished = [frame('RUN_FINISHED', { threadId: 't1', runId: RUN })];

const SHORT = 'Hi. How can I help?';
const ASK = 'I’ll ask which team you’re on first, then pause here for your answer.';
const SEARCH = 'I’ll search your collections for the code freeze rules and pull the clearest sources.';
const UPDATE = 'I found the release guide; I’m checking its full procedure before I answer.';
const ANSWER =
  'Code freeze starts ten days before each release. The release captain cuts the branch. QA signs the checklist.\n\n' +
  'Staging runs for two days before the release goes out.';
/** The run's first sentence from the report: said before a search, it read as the answer and then jumped into the box. */
const CHECK = 'I’ll check whether “multiplayer” refers to something in your organization’s materials and pull what it says.';
const LONG = `The freeze starts ten days before each release. ${'Then the captain cuts the branch and QA signs the list. '.repeat(12)}`;
const TITLED = '## Release checklist\n\nThe freeze starts ten days before each release.';

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

describe('the answer of a run with no step, while it streams', () => {
  it('holds a short answer while it may yet be a sentence said before a step, and shows it whole when the run ends', () => {
    let heldSteps = 0;
    const { container, done } = replay([...start, ...turn('m1', SHORT), ...finished], ({ view, name, streamed }) => {
      if (!streamed) return;
      if (name.endsWith('RUN_FINISHED')) {
        // Still streaming (the saved turn is on its way), but the run said its last text is the answer.
        expect(answerText(view), name).toBe(SHORT);
        return;
      }
      heldSteps += 1;
      expect(answerText(view), name).toBeNull();
      expect(placesOf(view, SHORT), name).toBe('none');
    }, SHORT);
    expect(heldSteps).toBeGreaterThan(5);
    done();
    expect(answerText(container)).toBe(SHORT);
  });

  it('says it is thinking until the words show, and not once they do: never neither', () => {
    let thinking = 0;
    let shown = 0;
    replay([...start, ...turn('m1', SHORT), ...finished], ({ view, name }) => {
      if (answerText(view) === null) {
        thinking += 1;
        expect(view.textContent, name).toContain('Thinking…');
      } else {
        shown += 1;
        // A status the run sends after (the turn's end, say) may show under the words; the words alone never do.
        if (name.endsWith('TEXT_MESSAGE_CONTENT')) expect(view.textContent, name).not.toContain('Thinking…');
      }
    }, SHORT);
    expect(thinking).toBeGreaterThan(5);
    expect(shown).toBeGreaterThan(0);
  });

  it('keeps no place for the confidence badge while the words wait, and keeps it once they show', () => {
    replay([...start, ...turn('m1', SHORT), ...finished], ({ view, name }) => {
      const answerShown = answerText(view) !== null;
      expect(view.querySelector('.level-slot') !== null, name).toBe(answerShown);
    }, SHORT);
  });

  it.each([['a heading', TITLED, 8], ['its length', LONG, 420]])('streams the answer word by word once %s tells it apart', (_why, answer, from) => {
    let streamedSteps = 0;
    const { container, done } = replay([...start, ...turn('m1', answer)], ({ view, name, streamed }) => {
      if (!streamed || streamed.trim().length < from) return;
      streamedSteps += 1;
      expect(answerText(view)?.trimEnd(), name).toBe(streamed.trimEnd());
      if (name.endsWith('TEXT_MESSAGE_CONTENT')) expect(view.textContent, name).not.toContain('Thinking…');
    }, answer);
    expect(streamedSteps).toBeGreaterThan(5);
    done();
    expect(answerText(container)).not.toBeNull();
  });

  it('keeps the words it showed when the run ends', () => {
    const { container, done } = replay([...start, ...turn('m1', SHORT)], () => {}, SHORT);
    done();
    expect(answerText(container)).toBe(SHORT);
  });
});

describe('a sentence said before a tool call', () => {
  it.each([
    ['a search', CHECK, 'knowledgegraph__search'],
    ['a question to the person', ASK, 'internaltools__ask_user_question'],
  ])('before %s is never drawn as the answer: it waits, saying the run is thinking, then shows only in the run’s box', (_what, sentence, toolName) => {
    const seen = new Set<string>();
    let waiting = 0;
    replay([...start, ...turn('m1', sentence), ...tool('call-1', toolName, false)], ({ view, name, streamed }) => {
      seen.add(placesOf(view, sentence));
      expect(answerText(view), name).toBeNull();
      if (!streamed.startsWith(sentence.slice(0, 16))) return;
      waiting += 1;
      expect(view.textContent, name).toContain('Thinking…');
      expect(view.querySelector('.level-slot'), name).toBeNull();
    });
    expect(waiting).toBeGreaterThan(5);
    expect([...seen].sort()).toEqual(['box', 'none']);
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
      for (const sentence of [SEARCH, UPDATE]) expect(placesOf(view, sentence), name).not.toBe('outside');
    });
    expect(placesOf(container, SEARCH)).toBe('box');
    expect(placesOf(container, UPDATE)).toBe('box');
    done();
    expect(placesOf(container, ANSWER)).toBe('outside');
  });
});

describe('text written after the run’s first step', () => {
  const first = [...start, ...turn('m1', SEARCH), ...tool('call-1', 'knowledgegraph__search')];

  it('never shows a sentence outside the box when a step follows it', () => {
    const seen = new Set<string>();
    replay([...first, ...turn('m2', UPDATE), ...tool('call-2', 'knowledgegraph__fetch_record')], ({ view }) => {
      seen.add(placesOf(view, UPDATE));
    });
    expect([...seen].sort()).toEqual(['box', 'none']);
  });

  it('says it is thinking while a sentence waits', () => {
    let waiting = 0;
    replay([...first, ...turn('m2', UPDATE)], ({ view, name, streamed }) => {
      if (!streamed.startsWith(UPDATE.slice(0, 8))) return;
      waiting += 1;
      expect(view.textContent, name).toContain('Thinking…');
      expect(answerText(view), name).toBeNull();
    });
    expect(waiting).toBeGreaterThan(5);
  });

  it.each([['a heading', TITLED, 8], ['its length', LONG, 420]])('streams the answer under the box once %s tells it apart, never in it', (_why, answer, from) => {
    const seen = new Set<string>();
    let streamedSteps = 0;
    const { container, done } = replay([...first, ...turn('m2', answer)], ({ view, name, streamed }) => {
      if (!answer.startsWith(streamed.slice(0, 8)) || !streamed) return;
      seen.add(placesOf(view, answer.replace(/^## /, '')));
      if (streamed.length < from) return;
      streamedSteps += 1;
      expect(answerText(view)?.trimEnd(), name).toBe(streamed.trimEnd());
    }, answer);
    expect(streamedSteps).toBeGreaterThan(5);
    expect(seen).not.toContain('box');
    done();
    expect(answerText(container)).not.toBeNull();
  });
});
