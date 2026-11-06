import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, act, fireEvent, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

type ThreadMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: { type: 'text'; text: string }[];
  metadata?: { custom?: Record<string, unknown> };
};

const thread = vi.hoisted(() => ({
  messages: [] as ThreadMessage[],
  append: vi.fn(),
}));
const loadOlder = vi.hoisted(() => vi.fn<(slotId: string) => Promise<void>>());

vi.mock('@assistant-ui/react', () => ({
  useAuiState: (select: (state: unknown) => unknown) => select({ thread: { messages: thread.messages } }),
  useAui: () => ({ thread: () => ({ append: thread.append }) }),
}));

vi.mock('../../../streaming', () => ({
  loadOlderMessagesForSlot: (slotId: string) => loadOlder(slotId),
}));

const viewport = vi.hoisted(() => ({ isMobile: false }));

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => viewport.isMobile, useIsBelowDesktop: () => viewport.isMobile }));

vi.mock('@/app/components/ui/lottie-loader', () => ({
  LottieLoader: ({ size }: { size?: number }) => (
    <div role="status" data-size={size}>
      Loading
    </div>
  ),
}));

vi.mock('../response-tabs/citations', async () => {
  const utils = await vi.importActual<typeof import('../response-tabs/citations/utils')>(
    '../response-tabs/citations/utils',
  );
  const control = await vi.importActual<
    typeof import('../response-tabs/citations/citation-popover-control')
  >('../response-tabs/citations/citation-popover-control');
  return {
    emptyCitationMaps: utils.emptyCitationMaps,
    isCitationPopoverKeyStillValid: control.isCitationPopoverKeyStillValid,
    useCitationActions: () => ({}),
  };
});

vi.mock('../response-tabs/citations/inline-citation-popover-host', () => ({
  InlineCitationPopoverHost: () => null,
}));

const renderedPairs = vi.hoisted(() => [] as unknown[]);
const renderedKeys = vi.hoisted(() => [] as (string | undefined)[]);

vi.mock('../chat-response', () => ({
  ChatResponse: (props: {
    question: string;
    answer: string;
    isStreaming: boolean;
    streamingContent?: string;
    currentStatusMessage?: unknown;
    unanswered?: boolean;
    latestArtifactVersions?: Map<string, number>;
    attachments?: unknown;
    collections?: unknown;
    appliedFilters?: unknown;
    pair?: unknown;
    renderKey?: string;
    onRetry?: (turn: object) => void;
  }) => {
    renderedPairs.push(props.pair);
    renderedKeys.push(props.renderKey);
    const { question, attachments, collections, appliedFilters, onRetry } = props;
    return (
      <article
        aria-label={question}
        data-streaming={props.isStreaming ? 'yes' : 'no'}
        data-latest-versions={JSON.stringify([...(props.latestArtifactVersions ?? [])])}
      >
        <h3>{question}</h3>
        {props.unanswered ? null : <p>{props.answer}</p>}
        {props.streamingContent ? <p>{props.streamingContent}</p> : null}
        {props.answer || props.streamingContent ? <div className="answer-prose" /> : null}
        {/* A plain answer's status line, drawn under its text. */}
        {props.currentStatusMessage ? <div data-status-line="" /> : null}
        {onRetry ? (
          <button type="button" onClick={() => onRetry({ question, attachments, collections, appliedFilters })}>
            Retry
          </button>
        ) : null}
      </article>
    );
  },
}));

import { MessageList } from '../message-list';
import { useAnswerPanelStore } from '../../answer-panel/answer-panel-store';
import { useChatStore } from '../../../store';
import { useInlineCitationPopoverStore } from '../response-tabs/citations/citation-popover-store';

const initialChatState = useChatStore.getState();

// jsdom has no layout, so each test describes the geometry the browser would
// report: the scroll viewport and where each question row sits inside it.
const geometry = {
  viewportHeight: 500,
  contentHeight: 2000,
  rowTop: {} as Record<string, number>,
  rowHeight: {} as Record<string, number>,
  /** Height of the transcript column (the box holding every turn). */
  columnHeight: 0,
  /** Height of what the column holds, when it differs from the column (it is holding a taller height). */
  columnContentHeight: undefined as number | undefined,
  /** Where the newest answer's text starts in the scrolled content. */
  answerTop: 0,
  /** How much higher the answer's text starts once its row is drawn finished (its trail's status line gone). */
  savedAnswerShift: 0,
};

let frames: FrameRequestCallback[] = [];
function flushFrames() {
  for (let guard = 0; guard < 20 && frames.length > 0; guard += 1) {
    const pending = frames;
    frames = [];
    for (const cb of pending) cb(performance.now());
  }
}

const resizeCallbacks: ResizeObserverCallback[] = [];
class FakeResizeObserver {
  constructor(private readonly cb: ResizeObserverCallback) {
    resizeCallbacks.push(cb);
  }
  observe() {}
  unobserve() {}
  disconnect() {
    const i = resizeCallbacks.indexOf(this.cb);
    if (i >= 0) resizeCallbacks.splice(i, 1);
  }
}

function rowQuestion(el: Element): string | null {
  const article = el.querySelector(':scope > article');
  return article?.getAttribute('aria-label') ?? null;
}

const originalRect = HTMLElement.prototype.getBoundingClientRect;
const originalScrollHeight = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollHeight');
const originalClientHeight = Object.getOwnPropertyDescriptor(Element.prototype, 'clientHeight');

beforeEach(() => {
  useChatStore.setState(initialChatState, true);
  useInlineCitationPopoverStore.setState({ activeKey: null });
  thread.messages = [];
  thread.append.mockReset();
  renderedPairs.length = 0;
  renderedKeys.length = 0;
  loadOlder.mockReset();
  loadOlder.mockResolvedValue(undefined);
  geometry.viewportHeight = 500;
  geometry.contentHeight = 2000;
  geometry.rowTop = {};
  geometry.rowHeight = {};
  geometry.columnHeight = 0;
  geometry.columnContentHeight = undefined;
  geometry.answerTop = 0;
  geometry.savedAnswerShift = 0;
  frames = [];
  resizeCallbacks.length = 0;

  // The send glide runs on the page motion role; at 0 ms (as under reduced motion) it is one step.
  document.documentElement.style.setProperty('--motion-page', '0ms');
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => frames.push(cb));
  vi.stubGlobal('cancelAnimationFrame', () => {});
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });

  HTMLElement.prototype.scrollTo = function scrollTo(this: HTMLElement, arg?: ScrollToOptions | number) {
    if (typeof arg === 'object' && arg?.top !== undefined) this.scrollTop = arg.top;
  } as HTMLElement['scrollTo'];
  HTMLElement.prototype.getBoundingClientRect = function rect(this: HTMLElement) {
    if (this.classList.contains('chat-message-scroll')) {
      return {
        top: 0, bottom: geometry.viewportHeight, height: geometry.viewportHeight,
        left: 0, right: 0, width: 0, x: 0, y: 0, toJSON: () => ({}),
      };
    }
    if (this.parentElement?.classList.contains('chat-column-gutter') && this.closest('.chat-message-scroll')) {
      // The column's content, without the height it may be holding.
      const h = geometry.columnContentHeight ?? geometry.columnHeight;
      return { top: 0, bottom: h, height: h, left: 0, right: 0, width: 0, x: 0, y: 0, toJSON: () => ({}) };
    }
    if (this.classList.contains('chat-column-gutter') && this.closest('.chat-message-scroll')) {
      const h = Math.max(geometry.columnHeight, parseFloat(this.style.minHeight) || 0);
      return { top: 0, bottom: h, height: h, left: 0, right: 0, width: 0, x: 0, y: 0, toJSON: () => ({}) };
    }
    if (this.classList.contains('answer-prose')) {
      const finished = this.closest('article')?.getAttribute('data-streaming') === 'no';
      const top = geometry.answerTop - (finished ? geometry.savedAnswerShift : 0)
        - (this.closest('.chat-message-scroll') as HTMLElement).scrollTop;
      return { top, bottom: top + 100, height: 100, left: 0, right: 0, width: 0, x: 0, y: top, toJSON: () => ({}) };
    }
    const question = rowQuestion(this);
    const scroller = this.closest('.chat-message-scroll');
    const offset = scroller instanceof HTMLElement ? scroller.scrollTop : 0;
    if (question !== null && question in geometry.rowTop) {
      const top = geometry.rowTop[question] - offset;
      const height = geometry.rowHeight[question] ?? 200;
      return { top, bottom: top + height, height, left: 0, right: 0, width: 0, x: 0, y: top, toJSON: () => ({}) };
    }
    return { top: 0, bottom: 0, height: 0, left: 0, right: 0, width: 0, x: 0, y: 0, toJSON: () => ({}) };
  };
  Object.defineProperty(Element.prototype, 'scrollHeight', {
    configurable: true,
    get(this: Element) {
      return this.classList.contains('chat-message-scroll') ? geometry.contentHeight : 0;
    },
  });
  Object.defineProperty(Element.prototype, 'clientHeight', {
    configurable: true,
    get(this: Element) {
      return this.classList.contains('chat-message-scroll') ? geometry.viewportHeight : 0;
    },
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  HTMLElement.prototype.getBoundingClientRect = originalRect;
  if (originalScrollHeight) Object.defineProperty(Element.prototype, 'scrollHeight', originalScrollHeight);
  if (originalClientHeight) Object.defineProperty(Element.prototype, 'clientHeight', originalClientHeight);
  delete (HTMLElement.prototype as Partial<HTMLElement>).scrollTo;
});

function user(id: string, text: string): ThreadMessage {
  return { id, role: 'user', content: [{ type: 'text', text }] };
}
function assistant(id: string, text: string): ThreadMessage {
  return { id, role: 'assistant', content: [{ type: 'text', text }] };
}

function openConversation(
  convId: string,
  patch: Partial<ReturnType<typeof useChatStore.getState>['slots'][string]> = {},
) {
  const store = useChatStore.getState();
  const slotId = store.createSlot(convId);
  store.updateSlot(slotId, { isInitialized: true, ...patch });
  store.setActiveSlot(slotId);
  return slotId;
}

function scroller(): HTMLElement {
  const el = document.querySelector('.chat-message-scroll');
  if (!(el instanceof HTMLElement)) throw new Error('message list scroll container not rendered');
  return el;
}

function scrollTo(top: number) {
  const el = scroller();
  el.scrollTop = top;
  fireEvent.scroll(el);
}

function settle() {
  act(() => {
    flushFrames();
    vi.advanceTimersByTime(600);
    flushFrames();
  });
}

const TWO_TURNS = [
  user('u1', 'What is our refund policy?'),
  assistant('a1', 'Refunds are issued within 30 days.'),
  user('u2', 'Who approves exceptions?'),
  assistant('a2', 'The finance lead approves exceptions.'),
];

describe('MessageList — what the reader sees', () => {
  it('shows every question with its answer, oldest first', () => {
    thread.messages = TWO_TURNS;
    openConversation('conv-1');

    render(<MessageList />);

    const rows = screen.getAllByRole('article');
    expect(rows.map((r) => r.getAttribute('aria-label'))).toEqual([
      'What is our refund policy?',
      'Who approves exceptions?',
    ]);
    expect(screen.getByText('Refunds are issued within 30 days.')).toBeTruthy();
    expect(screen.getByText('The finance lead approves exceptions.')).toBeTruthy();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('shows a loader while the conversation is still being fetched', () => {
    thread.messages = [];
    const store = useChatStore.getState();
    const slotId = store.createSlot('conv-1');
    store.setActiveSlot(slotId);

    render(<MessageList />);

    expect(screen.getByRole('status')).toBeTruthy();

    act(() => {
      thread.messages = TWO_TURNS;
      useChatStore.getState().updateSlot(slotId, { isInitialized: true });
    });

    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.getAllByRole('article')).toHaveLength(2);
  });

  it('shows a small loader at the top while older messages are fetched', () => {
    thread.messages = TWO_TURNS;
    openConversation('conv-1', {
      messagePagination: { currentPage: 1, hasOlderMessages: true, isLoadingOlder: true },
    });

    render(<MessageList />);

    expect(screen.getByRole('status').getAttribute('data-size')).toBe('32');
  });

  it('gives the live answer only to the question being answered', () => {
    thread.messages = [
      ...TWO_TURNS,
      user('u3', 'And for enterprise plans?'),
      assistant('a3', ''),
    ];
    openConversation('conv-1', {
      isStreaming: true,
      streamingQuestion: 'And for enterprise plans?',
      streamingContent: 'Enterprise refunds follow the contract',
    });

    render(<MessageList />);

    const live = screen.getByRole('article', { name: 'And for enterprise plans?' });
    expect(live.getAttribute('data-streaming')).toBe('yes');
    expect(live.textContent).toContain('Enterprise refunds follow the contract');
    const earlier = screen.getByRole('article', { name: 'Who approves exceptions?' });
    expect(earlier.getAttribute('data-streaming')).toBe('no');
    expect(earlier.textContent).not.toContain('Enterprise refunds follow the contract');
  });

  it('counts artifact versions from saved answers and live events, never from the streaming row', () => {
    thread.messages = [
      user('u1', 'Make the report'),
      assistant('a1', 'Done.\n\n::artifact[report.xlsx](record:rec-1){text/csv|d-1|rec-1|SPREADSHEET|1}'),
      user('u2', 'Update it'),
      // Model text on the row still streaming: not a saved answer, so its
      // marker must not feed the version map.
      assistant('a2', '::artifact[report.xlsx](https://attacker.test/x.exe){text/csv||rec-1||9}'),
    ];
    openConversation('conv-1', {
      isStreaming: true,
      streamingQuestion: 'Update it',
      artifacts: [
        { id: 'rec-1', fileName: 'report.xlsx', mimeType: 'text/csv', sizeBytes: 0, downloadUrl: '', artifactType: 'SPREADSHEET', recordId: 'rec-1', version: 2 },
      ],
    });

    render(<MessageList />);

    const row = screen.getByRole('article', { name: 'Make the report' });
    expect(JSON.parse(row.getAttribute('data-latest-versions') ?? '[]')).toEqual([['rec-1', 2]]);
  });

  it('keeps a question that was stopped before any answer arrived', () => {
    thread.messages = [...TWO_TURNS, user('u3', 'Is this question still visible?')];
    openConversation('conv-1');

    render(<MessageList />);

    expect(screen.getByRole('article', { name: 'Is this question still visible?' })).toBeTruthy();
  });

  it('keeps a citation popover open until its message row is replaced', () => {
    thread.messages = TWO_TURNS;
    openConversation('conv-1');
    const { rerender } = render(<MessageList />);

    act(() => useInlineCitationPopoverStore.setState({ activeKey: 'a2::1' }));
    expect(useInlineCitationPopoverStore.getState().activeKey).toBe('a2::1');

    thread.messages = [...TWO_TURNS.slice(0, 3), assistant('a2-saved', 'The finance lead approves exceptions.')];
    rerender(<MessageList />);

    expect(useInlineCitationPopoverStore.getState().activeKey).toBeNull();
  });
});

describe('MessageList — retrying a failed answer', () => {
  const ATTACHMENT = {
    recordId: 'r1',
    recordName: 'plan.pdf',
    mimeType: 'application/pdf',
    extension: 'pdf',
    virtualRecordId: 'v1',
  };
  const failedTurn = (text: string): ThreadMessage[] => [
    { ...user('u1', text), metadata: { custom: { attachments: [ATTACHMENT] } } },
    { ...assistant('a1', 'The model is not responding.'), metadata: { custom: { failed: true } } },
  ];

  it('resends the question with its attachments', () => {
    thread.messages = failedTurn('Summarise this');
    openConversation('conv-1');
    render(<MessageList />);

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    expect(thread.append).toHaveBeenCalledWith(
      expect.objectContaining({
        content: [{ type: 'text', text: 'Summarise this' }],
        metadata: { custom: expect.objectContaining({ attachments: [ATTACHMENT] }) },
        startRun: true,
      }),
    );
  });

  it('resends a turn that had only attachments', () => {
    thread.messages = failedTurn('');
    openConversation('conv-1');
    render(<MessageList />);

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    expect(thread.append).toHaveBeenCalledTimes(1);
    expect(thread.append.mock.calls[0][0].metadata.custom.attachments).toEqual([ATTACHMENT]);
  });

  it('offers no retry to someone who cannot send in this conversation', () => {
    thread.messages = failedTurn('Summarise this');
    openConversation('conv-1', { isOwner: false });
    render(<MessageList />);

    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
  });
});

describe('MessageList — row identity', () => {
  it('hands an unchanged row the same pair object when another row changes', () => {
    thread.messages = TWO_TURNS;
    openConversation('conv-1');
    const { rerender } = render(<MessageList />);
    const firstRender = renderedPairs.slice(0, 2);

    thread.messages = [...TWO_TURNS.slice(0, 3), assistant('a2', 'Edited answer.')];
    rerender(<MessageList />);

    expect(renderedPairs.at(-2)).toBe(firstRender[0]);
    expect(renderedPairs.at(-1)).not.toBe(firstRender[1]);
  });
});

describe('MessageList — scrolling', () => {
  it('opens a finished conversation at its end, in one step, with no blank space under the last answer', () => {
    thread.messages = TWO_TURNS;
    geometry.rowTop = { 'What is our refund policy?': 0, 'Who approves exceptions?': 600 };
    openConversation('conv-1');

    render(<MessageList />);
    const tops: number[] = [];
    const el = scroller();
    const record = () => tops.push(el.scrollTop);
    el.addEventListener('scroll', record);
    settle();

    expect(el.scrollTop).toBe(2000 - 500);
    const spacer = el.querySelector('[aria-hidden="true"]') as HTMLElement;
    expect(parseFloat(spacer.style.minHeight || '0')).toBe(0);
    // Never a smooth slide: the scroller itself never scrolls smoothly.
    expect(el.style.scrollBehavior).toBe('auto');
    el.removeEventListener('scroll', record);
  });

  it('glides a newly sent question to the top over the page motion role, without a jump in any one frame', () => {
    document.documentElement.style.setProperty('--motion-page', '320ms');
    thread.messages = TWO_TURNS;
    geometry.rowTop = { 'What is our refund policy?': 0, 'Who approves exceptions?': 600 };
    openConversation('conv-1');
    let now = 1000;
    const clock = vi.spyOn(performance, 'now').mockImplementation(() => now);
    try {
      const { rerender } = render(<MessageList />);
      settle();
      const start = scroller().scrollTop;

      geometry.rowTop['A brand new question'] = 1200;
      thread.messages = [...TWO_TURNS, user('u3', 'A brand new question'), assistant('a3', 'Sure.')];
      rerender(<MessageList />);
      const seen: number[] = [];
      for (let i = 0; i < 30 && frames.length > 0; i += 1) {
        act(() => {
          now += 16;
          flushFrames();
        });
        seen.push(scroller().scrollTop);
      }
      expect(seen.at(-1)).toBe(1200);
      // Lands within the role's 320 ms (20 frames of 16 ms), and moves in many small steps, not one.
      expect(seen.findIndex((top) => top === 1200)).toBeLessThanOrEqual(21);
      const steps = [start, ...seen].slice(1).map((top, i) => Math.abs(top - [start, ...seen][i]));
      expect(Math.max(...steps)).toBeLessThan(Math.abs(1200 - start));
    } finally {
      clock.mockRestore();
    }
  });

  it('puts the reader back where they were when they return to a chat', () => {
    thread.messages = TWO_TURNS;
    geometry.rowTop = { 'What is our refund policy?': 0, 'Who approves exceptions?': 600 };
    const first = openConversation('conv-1');
    const second = useChatStore.getState().createSlot('conv-2');
    useChatStore.getState().updateSlot(second, { isInitialized: true });

    render(<MessageList />);
    settle();
    scrollTo(321);

    act(() => {
      thread.messages = [user('v1', 'Another chat'), assistant('b1', 'Another answer')];
      useChatStore.getState().setActiveSlot(second);
    });
    expect(useChatStore.getState().slots[first].savedScrollTop).toBe(321);
    settle();

    act(() => {
      thread.messages = TWO_TURNS;
      useChatStore.getState().setActiveSlot(first);
    });
    settle();

    expect(scroller().scrollTop).toBe(321);
  });

  it('shows the finished answer when returning to a chat that completed while away', () => {
    thread.messages = TWO_TURNS;
    geometry.contentHeight = 1800;
    openConversation('conv-1', { savedScrollTop: 150, savedScrollWasStreaming: true });

    render(<MessageList />);
    settle();

    expect(scroller().scrollTop).toBe(1800 - 500);
  });

  it('loads older messages when the reader scrolls to the top', () => {
    thread.messages = TWO_TURNS;
    const slotId = openConversation('conv-1', {
      messagePagination: { currentPage: 1, hasOlderMessages: true, isLoadingOlder: false },
    });

    render(<MessageList />);
    settle();
    scrollTo(40);

    expect(loadOlder).toHaveBeenCalledTimes(1);
    expect(loadOlder).toHaveBeenCalledWith(slotId);
  });

  it('asks for older messages only once while a page is still loading', async () => {
    thread.messages = TWO_TURNS;
    let finish: () => void = () => {};
    loadOlder.mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
    openConversation('conv-1', {
      messagePagination: { currentPage: 1, hasOlderMessages: true, isLoadingOlder: false },
    });

    render(<MessageList />);
    settle();
    scrollTo(40);
    scrollTo(20);
    expect(loadOlder).toHaveBeenCalledTimes(1);

    await act(async () => {
      finish();
      await Promise.resolve();
    });
    scrollTo(10);
    expect(loadOlder).toHaveBeenCalledTimes(2);
  });

  it('does not ask for older messages when the conversation has none', () => {
    thread.messages = TWO_TURNS;
    openConversation('conv-1', {
      messagePagination: { currentPage: 1, hasOlderMessages: false, isLoadingOlder: false },
    });

    render(<MessageList />);
    settle();
    scrollTo(0);

    expect(loadOlder).not.toHaveBeenCalled();
  });

  it('brings a newly sent question into view even if the reader had scrolled up', () => {
    thread.messages = TWO_TURNS;
    geometry.rowTop = { 'What is our refund policy?': 0, 'Who approves exceptions?': 600 };
    openConversation('conv-1');

    const { rerender } = render(<MessageList />);
    settle();
    scrollTo(100);

    geometry.rowTop['A brand new question'] = 1200;
    thread.messages = [...TWO_TURNS, user('u3', 'A brand new question'), assistant('a3', 'Sure.')];
    rerender(<MessageList />);
    settle();

    expect(scroller().scrollTop).toBe(1200);
  });

  it('follows a tall answer as it streams, and stops once the reader scrolls up', () => {
    thread.messages = [...TWO_TURNS, user('u3', 'Summarise the policy'), assistant('a3', '')];
    geometry.rowTop = { 'Summarise the policy': 900 };
    geometry.rowHeight = { 'Summarise the policy': 900 };
    const slotId = openConversation('conv-1', {
      isStreaming: true,
      streamingQuestion: 'Summarise the policy',
      streamingContent: 'Part one',
    });

    render(<MessageList />);
    settle();
    expect(scroller().scrollTop).toBe(2000 - 500);

    act(() => {
      geometry.contentHeight = 2600;
      useChatStore.getState().updateSlot(slotId, { streamingContent: 'Part one. Part two' });
    });
    expect(scroller().scrollTop).toBe(2600 - 500);

    fireEvent.wheel(scroller(), { deltaY: -40 });
    act(() => {
      geometry.contentHeight = 3200;
      useChatStore.getState().updateSlot(slotId, { streamingContent: 'Part one. Part two. Part three' });
    });
    expect(scroller().scrollTop).toBe(2600 - 500);
  });

  it("keeps a chat's first question where it rests while its answer streams (no 16px slide under the column padding)", () => {
    // The column's top padding, as the browser resolves it.
    const realStyle = window.getComputedStyle;
    vi.spyOn(window, 'getComputedStyle').mockImplementation((el: Element, pseudo?: string | null) => {
      const style = realStyle(el, pseudo);
      if (el instanceof HTMLElement && el.classList.contains('chat-column-gutter')) {
        return new Proxy(style, { get: (target, prop) => (prop === 'paddingTop' ? '16px' : Reflect.get(target, prop)) });
      }
      return style;
    });
    try {
      thread.messages = [user('u1', 'First question'), assistant('a1', '')];
      // The first row sits under the column's 16px padding; the answer is short enough to rest with it at the top.
      geometry.rowTop = { 'First question': 16 };
      geometry.rowHeight = { 'First question': 300 };
      geometry.contentHeight = 600;
      const slotId = openConversation('conv-1', { isStreaming: true, streamingQuestion: 'First question', streamingContent: 'Part one' });
      render(<MessageList />);
      settle();
      act(() => {
        geometry.rowHeight['First question'] = 320;
        useChatStore.getState().updateSlot(slotId, { streamingContent: 'Part one. Part two' });
      });
      settle();
      expect(scroller().scrollTop).toBe(0);
    } finally {
      vi.mocked(window.getComputedStyle).mockRestore();
    }
  });

  it('hands over from the question at the top to following the answer\'s end where they meet, with no jump', () => {
    thread.messages = [user('u1', 'First question'), assistant('a1', '')];
    // The first row rests under a 60px top inset; the content is the row and nothing else.
    geometry.rowTop = { 'First question': 60 };
    const grow = (height: number) => {
      geometry.rowHeight['First question'] = height;
      geometry.contentHeight = 60 + height;
    };
    grow(400);
    const slotId = openConversation('conv-1', { isStreaming: true, streamingQuestion: 'First question', streamingContent: 'a' });
    render(<MessageList topInset="60px" />);
    settle();
    const tops: number[] = [];
    for (const [height, text] of [[430, 'ab'], [450, 'abc'], [470, 'abcd'], [520, 'abcde']] as const) {
      act(() => {
        grow(height);
        useChatStore.getState().updateSlot(slotId, { streamingContent: text });
      });
      tops.push(scroller().scrollTop);
    }
    // Still at rest while the end fits, then each step only as far as the answer grew past the bottom; a step over
    // 40px goes on in the next frame.
    expect(tops).toEqual([0, 10, 30, 70]);
    act(() => flushFrames());
    expect(scroller().scrollTop).toBe(80);
  });

  it('follows text that grows between store updates in the frame it grew in, not a frame later', () => {
    thread.messages = [user('u1', 'First question'), assistant('a1', '')];
    geometry.rowTop = { 'First question': 60 };
    const grow = (height: number) => {
      geometry.rowHeight['First question'] = height;
      geometry.contentHeight = 60 + height;
    };
    grow(400);
    const slotId = openConversation('conv-1', { isStreaming: true, streamingQuestion: 'First question', streamingContent: 'a' });
    render(<MessageList topInset="60px" />);
    settle();
    act(() => {
      grow(430);
      useChatStore.getState().updateSlot(slotId, { streamingContent: 'ab' });
    });
    expect(scroller().scrollTop).toBe(0);
    // The answer reveals more of what it already has: its row grows with no store update, and only its size
    // observer sees it. The scroll follows in that same frame, before any further frame runs (40px of the 80px; the
    // rest in the next frame).
    act(() => {
      grow(520);
      for (const cb of [...resizeCallbacks]) cb([], {} as ResizeObserver);
    });
    expect(scroller().scrollTop).toBe(40);
    act(() => flushFrames());
    expect(scroller().scrollTop).toBe(80);
  });

  it('says on the scroller that it follows the answer\'s end only while it does', () => {
    thread.messages = [user('u1', 'First question'), assistant('a1', '')];
    geometry.rowTop = { 'First question': 60 };
    const grow = (height: number) => {
      geometry.rowHeight['First question'] = height;
      geometry.contentHeight = 60 + height;
    };
    grow(300);
    const slotId = openConversation('conv-1', { isStreaming: true, streamingQuestion: 'First question', streamingContent: 'a' });
    render(<MessageList topInset="60px" />);
    settle();
    // The question rests at the top: steps that join the trail open, moving the text down with them.
    expect(scroller().hasAttribute('data-follows-end')).toBe(false);
    act(() => {
      grow(700);
      useChatStore.getState().updateSlot(slotId, { streamingContent: 'ab' });
    });
    settle();
    expect(scroller().hasAttribute('data-follows-end')).toBe(true);
    act(() => useChatStore.getState().updateSlot(slotId, { isStreaming: false }));
    expect(scroller().hasAttribute('data-follows-end')).toBe(false);
  });

  describe('a block that lands whole (a table, a code block)', () => {
    /** One frame: the callbacks already queued, not the ones they queue. */
    const oneFrame = () => {
      const pending = frames;
      frames = [];
      for (const cb of pending) cb(performance.now());
    };
    function landTable() {
      thread.messages = [user('u1', 'First question'), assistant('a1', '')];
      geometry.rowTop = { 'First question': 60 };
      const grow = (height: number) => {
        geometry.rowHeight['First question'] = height;
        geometry.contentHeight = 60 + height;
      };
      grow(500);
      const slotId = openConversation('conv-1', { isStreaming: true, streamingQuestion: 'First question', streamingContent: 'a' });
      render(<MessageList topInset="60px" />);
      settle();
      act(() => {
        grow(520);
        useChatStore.getState().updateSlot(slotId, { streamingContent: 'ab' });
      });
      const start = scroller().scrollTop;
      // A table lands: the row grows 105px in one commit.
      act(() => {
        grow(625);
        useChatStore.getState().updateSlot(slotId, { streamingContent: 'ab | table' });
      });
      const tops = [scroller().scrollTop];
      for (let i = 0; i < 4; i += 1) {
        act(() => oneFrame());
        tops.push(scroller().scrollTop);
      }
      return { start, tops, slotId };
    }

    it('is followed over the next frames, at most 40px in each, to the same place', () => {
      const { start, tops } = landTable();
      expect(start).toBe(80);
      expect(tops).toEqual([120, 160, 185, 185, 185]);
    });

    it('is followed in one step with reduced motion', () => {
      vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('reduce'), media: query, addEventListener() {}, removeEventListener() {} }));
      const { tops } = landTable();
      expect(tops).toEqual([185, 185, 185, 185, 185]);
    });

    it('stops following over frames once the reader scrolls up', () => {
      const { tops } = landTable();
      expect(tops[tops.length - 1]).toBe(185);
      // Lands again; the reader scrolls up after the first step.
      act(() => {
        geometry.rowHeight['First question'] = 730;
        geometry.contentHeight = 790;
        useChatStore.getState().updateSlot(Object.keys(useChatStore.getState().slots)[0], { streamingContent: 'ab | table | more' });
      });
      expect(scroller().scrollTop).toBe(225);
      fireEvent.wheel(scroller(), { deltaY: -40 });
      act(() => oneFrame());
      expect(scroller().scrollTop).toBe(225);
    });
  });

  it('resumes following the answer when the reader scrolls back to the bottom', () => {
    thread.messages = [...TWO_TURNS, user('u3', 'Summarise the policy'), assistant('a3', '')];
    geometry.rowTop = { 'Summarise the policy': 900 };
    geometry.rowHeight = { 'Summarise the policy': 900 };
    const slotId = openConversation('conv-1', {
      isStreaming: true,
      streamingQuestion: 'Summarise the policy',
      streamingContent: 'Part one',
    });

    render(<MessageList />);
    settle();
    fireEvent.wheel(scroller(), { deltaY: -40 });
    scrollTo(1500);
    settle();

    act(() => {
      geometry.contentHeight = 2600;
      useChatStore.getState().updateSlot(slotId, { streamingContent: 'Part one. Part two' });
    });
    // 600px landed at once: followed over the next frames.
    act(() => flushFrames());
    expect(scroller().scrollTop).toBe(2600 - 500);
  });

  it('stops following when the reader drags the answer down on a touch screen', () => {
    thread.messages = [...TWO_TURNS, user('u3', 'Summarise the policy'), assistant('a3', '')];
    geometry.rowTop = { 'Summarise the policy': 900 };
    geometry.rowHeight = { 'Summarise the policy': 900 };
    const slotId = openConversation('conv-1', {
      isStreaming: true,
      streamingQuestion: 'Summarise the policy',
      streamingContent: 'Part one',
    });

    render(<MessageList />);
    settle();
    const el = scroller();
    fireEvent.touchStart(el);
    el.scrollTop = 1200;
    fireEvent.touchEnd(el);

    act(() => {
      geometry.contentHeight = 2600;
      useChatStore.getState().updateSlot(slotId, { streamingContent: 'Part one. Part two' });
    });
    expect(el.scrollTop).toBe(1200);
  });

  it('sizes the bottom spacer, once a question is sent, so it can reach the top of the view', () => {
    thread.messages = TWO_TURNS;
    geometry.rowTop = { 'What is our refund policy?': 0, 'Who approves exceptions?': 600 };
    geometry.rowHeight = { 'Who approves exceptions?': 120 };
    openConversation('conv-1');

    const { rerender } = render(<MessageList />);
    settle();

    const spacer = scroller().querySelector('[aria-hidden="true"]') as HTMLElement;
    // Opened: no blank space under the last answer.
    expect(parseFloat(spacer.style.minHeight || '0')).toBe(0);

    geometry.rowTop['A brand new question'] = 1200;
    geometry.rowHeight['A brand new question'] = 120;
    thread.messages = [...TWO_TURNS, user('u3', 'A brand new question'), assistant('a3', 'Sure.')];
    rerender(<MessageList />);
    settle();
    expect(spacer.style.minHeight).toBe(`${500 - 120}px`);

    act(() => {
      geometry.rowHeight['A brand new question'] = 300;
      for (const cb of [...resizeCallbacks]) cb([], {} as ResizeObserver);
      flushFrames();
    });
    expect(spacer.style.minHeight).toBe(`${500 - 300}px`);
  });

  it('outside a run, never shrinks the spacer under the reader (the saved answer replacing the streamed one)', () => {
    thread.messages = TWO_TURNS;
    geometry.rowTop = { 'What is our refund policy?': 0, 'Who approves exceptions?': 600 };
    openConversation('conv-1');
    const { rerender } = render(<MessageList />);
    settle();
    geometry.rowTop['A brand new question'] = 1200;
    geometry.rowHeight['A brand new question'] = 120;
    thread.messages = [...TWO_TURNS, user('u3', 'A brand new question'), assistant('a3', 'Sure.')];
    rerender(<MessageList />);
    settle();
    const spacer = scroller().querySelector('[aria-hidden="true"]') as HTMLElement;
    expect(spacer.style.minHeight).toBe(`${500 - 120}px`);
    // The reader sits at the very end: the spacer is part of what they see.
    act(() => scrollTo(1500));

    // The answer row grows (its toolbar arrives) and the plain rule would shrink the spacer to 100px,
    // which would clamp the position. It keeps what holds the reader where they are.
    act(() => {
      geometry.rowHeight['A brand new question'] = 400;
      for (const cb of [...resizeCallbacks]) cb([], {} as ResizeObserver);
      flushFrames();
    });
    expect(spacer.style.minHeight).toBe(`${500 - 120}px`);
    expect(scroller().scrollTop).toBe(1500);

    // Once the reader moves up, the spacer may shrink again (nothing on screen depends on it).
    act(() => scrollTo(1000));
    act(() => {
      geometry.rowHeight['A brand new question'] = 410;
      for (const cb of [...resizeCallbacks]) cb([], {} as ResizeObserver);
      flushFrames();
    });
    expect(spacer.style.minHeight).toBe(`${500 - 410}px`);
  });
});

describe('MessageList — the end of a run', () => {
  const LIVE = [...TWO_TURNS, user('u3', 'Summarise the policy'), assistant('a3', '')];
  const SAVED = [...TWO_TURNS, user('u3', 'Summarise the policy'), assistant('a3-final', 'Final.')];
  const column = () => scroller().querySelector('.chat-column-gutter') as HTMLElement;
  const spacer = () => column().nextElementSibling as HTMLElement;
  const live = () => screen.getByRole('article', { name: 'Summarise the policy' });

  function streaming(composer?: React.ReactNode) {
    thread.messages = LIVE;
    geometry.rowTop = { 'Summarise the policy': 900 };
    geometry.rowHeight = { 'Summarise the policy': 900 };
    const slotId = openConversation('conv-1', {
      isStreaming: true,
      streamingQuestion: 'Summarise the policy',
      streamingContent: 'Part one',
    });
    const view = render(<MessageList composer={composer} />);
    settle();
    // The column's height in the answer's last running frame, as its observer reports it.
    act(() => {
      geometry.columnHeight = 1500;
      for (const cb of [...resizeCallbacks]) cb([], {} as ResizeObserver);
      flushFrames();
    });
    return { slotId, ...view };
  }

  function finish(slotId: string) {
    act(() => {
      thread.messages = SAVED;
      useChatStore.getState().updateSlot(slotId, { isStreaming: false, streamingContent: '' });
    });
  }

  it('keeps the same row on screen when the saved reply replaces the streamed one', () => {
    const { slotId } = streaming();
    const row = live();
    const keyWhileLive = renderedKeys[renderedKeys.length - 1];
    expect(keyWhileLive).toBe('a3');
    finish(slotId);
    // Same DOM node: the turn (and its trail) did not mount afresh.
    expect(live()).toBe(row);
    expect(live().getAttribute('data-streaming')).toBe('no');
    // The saved row is drawn under the running row's key, which also names its run.
    expect(renderedKeys[renderedKeys.length - 1]).toBe('a3');
  });

  it('keeps the streamed row drawn until the saved one arrives, though the run is already marked over', () => {
    const { slotId, rerender } = streaming();
    const row = live();
    // The slot says the run is over a commit before the thread brings the saved rows.
    act(() => {
      useChatStore.getState().updateSlot(slotId, { isStreaming: false, streamingContent: '' });
    });
    expect(live()).toBe(row);
    expect(live().getAttribute('data-streaming')).toBe('yes');
    expect(live().textContent).toContain('Part one');
    act(() => {
      thread.messages = SAVED;
      rerender(<MessageList />);
    });
    expect(live()).toBe(row);
    expect(live().getAttribute('data-streaming')).toBe('no');
    expect(live().textContent).toContain('Final.');
  });

  describe('the answer stays where the reader has it while the trail above it folds', () => {
    let now = 0;
    beforeEach(() => {
      now = 1000;
      vi.spyOn(performance, 'now').mockImplementation(() => now);
    });
    const frame = (ms = 16) => {
      now += ms;
      act(() => {
        const pending = frames;
        frames = [];
        for (const cb of pending) cb(now);
      });
    };

    function readingAt(scrollTop: number, composer?: React.ReactNode) {
      const view = streaming(composer);
      fireEvent.wheel(scroller(), { deltaY: -40 });
      act(() => scrollTo(scrollTop));
      return view;
    }

    it('moves the scroll position with the answer each frame, so its text does not move on screen', () => {
      const { slotId } = readingAt(1100);
      geometry.answerTop = 1300;
      finish(slotId);
      expect(scroller().style.overflowAnchor).toBe('none');
      // The trail folds: the answer's text rises 60px, then 40px more.
      geometry.answerTop = 1240;
      frame();
      expect(scroller().scrollTop).toBe(1040);
      geometry.answerTop = 1200;
      frame();
      expect(scroller().scrollTop).toBe(1000);
      // On screen it stayed at 200px the whole time.
      expect(1200 - scroller().scrollTop).toBe(200);
      // Once nothing has moved for a moment it lets go, and the browser's own anchoring is back.
      frame(250);
      frame(16);
      expect(frames).toHaveLength(0);
      expect(scroller().style.overflowAnchor).toBe('auto');
    });

    it('puts back what a later commit moved before it is painted, without waiting for a frame', () => {
      const { slotId, rerender } = readingAt(1100);
      geometry.answerTop = 1300;
      finish(slotId);
      // A commit after the end (the saved answer drawn with something above it gone) moves the answer up 31px.
      act(() => {
        geometry.answerTop = 1269;
        rerender(<MessageList />);
      });
      expect(scroller().scrollTop).toBe(1069);
    });

    it('puts back what the very commit that ends the run moved, before it is painted', () => {
      const { slotId } = readingAt(1100);
      // Drawn live with the answer's text 200px down the view.
      geometry.answerTop = 1300;
      // The commit that ends the run draws the saved row at once, and that already moves the answer up 36px (its
      // trail's last "Thinking" line is gone) before the hold can see where it is.
      geometry.savedAnswerShift = 36;
      finish(slotId);
      expect(live().getAttribute('data-streaming')).toBe('no');
      expect(geometry.answerTop - geometry.savedAnswerShift - scroller().scrollTop).toBe(200);
      expect(scroller().scrollTop).toBe(1064);
    });

    // A row saved under the key it ran with left the list waiting for a saved row that had already come, so every
    // later run's end was drawn without its rows for a commit: its answer gone, then drawn afresh and not held.
    it.each([
      ['a regenerated answer', { regenerateMessageId: 'a3' }],
      ["a card's reply, run on the card's own row", {}],
    ])('holds the next answer at its run end after %s', (_, ranOnRow) => {
      const asked = (answer: string) => [...TWO_TURNS, user('u3', 'Summarise the policy'), assistant('a3', answer)];
      thread.messages = asked('Which region?');
      geometry.rowTop = { 'Summarise the policy': 900, 'Who signs it off?': 1100 };
      const slotId = openConversation('conv-1', {
        isStreaming: true,
        streamingQuestion: 'Summarise the policy',
        streamingContent: 'Part one',
        ...ranOnRow,
      });
      const { rerender } = render(<MessageList />);
      settle();
      // Its run ends, and the thread brings the row back saved under the same key.
      act(() => {
        useChatStore.getState().updateSlot(slotId, { isStreaming: false, regenerateMessageId: null, streamingContent: '' });
      });
      act(() => {
        thread.messages = asked('Rewritten.');
        rerender(<MessageList />);
      });
      settle();

      const next = [...asked('Rewritten.'), user('u4', 'Who signs it off?')];
      act(() => {
        thread.messages = [...next, assistant('a4', '')];
        useChatStore.getState().updateSlot(slotId, {
          isStreaming: true,
          streamingQuestion: 'Who signs it off?',
          streamingContent: 'Part one',
        });
      });
      settle();
      fireEvent.wheel(scroller(), { deltaY: -40 });
      act(() => scrollTo(1100));
      geometry.answerTop = 1300;
      geometry.savedAnswerShift = 36;
      const row = screen.getByRole('article', { name: 'Who signs it off?' });

      act(() => {
        useChatStore.getState().updateSlot(slotId, { isStreaming: false, streamingContent: '' });
      });
      expect(row.getAttribute('data-streaming')).toBe('yes');
      expect(row.textContent).toContain('Part one');
      act(() => {
        thread.messages = [...next, assistant('a4-final', 'Done.')];
        rerender(<MessageList />);
      });
      expect(screen.getByRole('article', { name: 'Who signs it off?' })).toBe(row);
      expect(geometry.answerTop - geometry.savedAnswerShift - scroller().scrollTop).toBe(200);
    });

    // The slot runs a new question a commit before the thread holds it: for that commit an earlier row asking the same
    // thing is drawn running, and it was left waiting for a saved row that never comes.
    it('holds the answer at its run end when its question repeats an earlier one', () => {
      thread.messages = TWO_TURNS;
      geometry.rowTop = { 'What is our refund policy?': 1100 };
      const slotId = openConversation('conv-1');
      const { rerender } = render(<MessageList />);
      settle();
      const asked = [...TWO_TURNS, user('u3', 'What is our refund policy?')];
      act(() => {
        useChatStore.getState().updateSlot(slotId, { isStreaming: true, streamingQuestion: 'What is our refund policy?' });
      });
      act(() => {
        thread.messages = [...asked, assistant('a3', '')];
        useChatStore.getState().updateSlot(slotId, { streamingContent: 'Part one' });
      });
      settle();
      fireEvent.wheel(scroller(), { deltaY: -40 });
      act(() => scrollTo(1100));
      geometry.answerTop = 1300;
      geometry.savedAnswerShift = 36;
      const rows = () => screen.getAllByRole('article', { name: 'What is our refund policy?' });
      const row = rows()[1];

      act(() => {
        useChatStore.getState().updateSlot(slotId, { isStreaming: false, streamingContent: '' });
      });
      expect(row.getAttribute('data-streaming')).toBe('yes');
      expect(row.textContent).toContain('Part one');
      act(() => {
        thread.messages = [...asked, assistant('a3-final', 'Done.')];
        rerender(<MessageList />);
      });
      expect(rows()[1]).toBe(row);
      expect(geometry.answerTop - geometry.savedAnswerShift - scroller().scrollTop).toBe(200);
    });

    it('does not follow what grows once the run has ended for the row, though the slot still runs', () => {
      thread.messages = [...TWO_TURNS, user('u3', 'Summarise the policy'), assistant('a3', 'Part one')];
      geometry.rowTop = { 'Summarise the policy': 900 };
      geometry.rowHeight = { 'Summarise the policy': 900 };
      const slotId = openConversation('conv-1', {
        isStreaming: true,
        streamingQuestion: 'Summarise the policy',
        streamingContent: 'Part one',
      });
      const { rerender } = render(<MessageList />);
      settle();
      // Following the answer's end, at the bottom of the transcript.
      expect(scroller().scrollTop).toBe(1500);
      act(() => {
        geometry.answerTop = 1700;
        rerender(<MessageList />);
      });
      // The row's run ends while the slot still says it runs: its answer is held where it is.
      act(() => useChatStore.getState().updateSlot(slotId, { streamingQuestion: 'Another question' }));
      // Then the finished row grows 36px at its end, in a commit that still counts the run as streaming.
      act(() => {
        geometry.contentHeight = 2036;
        useChatStore.getState().updateSlot(slotId, { streamingContent: 'Part one, and the rest.' });
      });
      expect(scroller().scrollTop).toBe(1500);
    });

    // In an answer with no tool steps, "Thinking…" shows under its text when the text turn ends, a commit before the
    // run does. Followed, it moved the answer up 30px, and the run's end kept it there.
    const THINKING = { id: 's1', status: 'calling_llm', message: 'Thinking…', timestamp: '' } as const;
    function thinkingThenEnd(slotId: string, row: number, content: number) {
      act(() => {
        // The text turn ends: the line under the answer's text makes its row 30px taller.
        geometry.rowHeight['Summarise the policy'] = row + 30;
        geometry.contentHeight = content + 30;
        useChatStore.getState().updateSlot(slotId, { currentStatusMessage: THINKING });
      });
      const whileThinking = geometry.answerTop - scroller().scrollTop;
      act(() => {
        // The run ends, and the line goes with it.
        geometry.rowHeight['Summarise the policy'] = row;
        geometry.contentHeight = content;
        thread.messages = SAVED;
        useChatStore.getState().updateSlot(slotId, { isStreaming: false, streamingContent: '', currentStatusMessage: null });
      });
      frame();
      return [whileThinking, geometry.answerTop - scroller().scrollTop];
    }

    it('does not follow the status line under an answer with no tool steps, so the run ends with the answer still', () => {
      const { slotId } = streaming();
      // Following the answer's end at the bottom: its text 190px down the view, the row 10px past it.
      act(() => {
        geometry.answerTop = 1690;
        useChatStore.getState().updateSlot(slotId, { streamingContent: 'Part one. Part two' });
      });
      expect(scroller().scrollTop).toBe(1500);
      expect(thinkingThenEnd(slotId, 900, 2000)).toEqual([190, 190]);
    });

    it('on a phone, keeps a short answer that rests under its question still too', () => {
      viewport.isMobile = true;
      const native = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollTop')!;
      // As the browser does: no further down than the content goes. With no blank space under it on a phone, the
      // question's rest is held at the bottom, so the bottom moving with the status line would move the answer.
      Object.defineProperty(Element.prototype, 'scrollTop', {
        configurable: true,
        get(this: Element) {
          return native.get!.call(this);
        },
        set(this: Element, v: number) {
          native.set!.call(this, Math.min(v, Math.max(0, geometry.contentHeight - geometry.viewportHeight)));
        },
      });
      try {
        thread.messages = LIVE;
        geometry.rowTop = { 'Summarise the policy': 900 };
        geometry.rowHeight = { 'Summarise the policy': 300 };
        geometry.contentHeight = 1300;
        geometry.answerTop = 1090;
        const slotId = openConversation('conv-1', {
          isStreaming: true,
          streamingQuestion: 'Summarise the policy',
          streamingContent: 'Part one',
        });
        render(<MessageList />);
        settle();
        act(() => useChatStore.getState().updateSlot(slotId, { streamingContent: 'Part one. Part two' }));
        expect(scroller().scrollTop).toBe(800);
        expect(thinkingThenEnd(slotId, 300, 1300)).toEqual([290, 290]);
      } finally {
        Object.defineProperty(Element.prototype, 'scrollTop', native);
        viewport.isMobile = false;
      }
    });

    it('with no room above, moves only by what cannot be scrolled', () => {
      const { slotId } = readingAt(30);
      geometry.answerTop = 330;
      finish(slotId);
      geometry.answerTop = 230;
      frame();
      expect(scroller().scrollTop).toBe(0);
    });

    it('leaves a reader up-thread where they are: an answer off screen is not held', () => {
      const { slotId } = readingAt(100);
      geometry.answerTop = 1300;
      finish(slotId);
      geometry.answerTop = 1200;
      frame();
      expect(scroller().scrollTop).toBe(100);
      expect(scroller().style.overflowAnchor).toBe('auto');
    });

    describe('with the composer docked over the last 120px of the 500px view', () => {
      const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight');
      beforeEach(() => {
        Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
          configurable: true,
          get(this: HTMLElement) {
            return this.style.position === 'sticky' && this.querySelector('[data-testid="composer"]') ? 120 : 0;
          },
        });
      });
      afterEach(() => {
        if (original) Object.defineProperty(HTMLElement.prototype, 'offsetHeight', original);
        else delete (HTMLElement.prototype as { offsetHeight?: number }).offsetHeight;
      });

      it('leaves the trail the reader is on where it is: an answer still behind the composer is not held', () => {
        const { slotId } = readingAt(1000, <div data-testid="composer" />);
        // On screen at 400px: under the composer, which starts at 380px.
        geometry.answerTop = 1400;
        finish(slotId);
        // The trail folds and the answer rises into view instead.
        geometry.answerTop = 1300;
        frame();
        expect(scroller().scrollTop).toBe(1000);
        expect(scroller().style.overflowAnchor).toBe('auto');
      });

      it('holds an answer that starts above the composer', () => {
        const { slotId } = readingAt(1000, <div data-testid="composer" />);
        geometry.answerTop = 1360;
        finish(slotId);
        geometry.answerTop = 1300;
        frame();
        expect(scroller().scrollTop).toBe(940);
      });
    });

    it('lets the reader take over: their own scrolling ends it', () => {
      const { slotId } = readingAt(1100);
      geometry.answerTop = 1300;
      finish(slotId);
      fireEvent.wheel(scroller(), { deltaY: 40 });
      geometry.answerTop = 1200;
      frame();
      expect(scroller().scrollTop).toBe(1100);
    });
  });

  it('puts the reader back when a layout in the same commit already pulled the position in', () => {
    const { slotId } = streaming();
    fireEvent.wheel(scroller(), { deltaY: -40 });
    act(() => scrollTo(1100));
    act(() => {
      thread.messages = SAVED;
      // The browser clamped during this commit, before the hold could apply.
      scroller().scrollTop = 900;
      useChatStore.getState().updateSlot(slotId, { isStreaming: false, streamingContent: '' });
    });
    expect(scroller().scrollTop).toBe(1100);
  });

  it('makes room under the reader when something outside the column shortens the range as the run ends', () => {
    const { slotId } = streaming();
    fireEvent.wheel(scroller(), { deltaY: -40 });
    act(() => scrollTo(1100));
    const before = parseFloat(spacer().style.minHeight || '0');
    act(() => {
      thread.messages = SAVED;
      // The composer gives up its Stop: the range now ends at 1000, short of where the reader is.
      geometry.contentHeight = 1500;
      scroller().scrollTop = 1000;
      useChatStore.getState().updateSlot(slotId, { isStreaming: false, streamingContent: '' });
    });
    expect(parseFloat(spacer().style.minHeight)).toBe(before + 100);
    expect(scroller().scrollTop).toBe(1100);
  });

  it('keeps the rows on screen through a moment with no messages, then shows the saved row in the same place', () => {
    const { slotId, rerender } = streaming();
    const row = live();
    act(() => {
      thread.messages = [];
      useChatStore.getState().updateSlot(slotId, { isStreaming: false, streamingContent: '' });
    });
    expect(live()).toBe(row);
    act(() => {
      thread.messages = SAVED;
      rerender(<MessageList />);
    });
    expect(live()).toBe(row);
    expect(live().getAttribute('data-streaming')).toBe('no');
  });

  it('shows a thread that stays empty as empty after a moment', () => {
    const { slotId } = streaming();
    act(() => {
      thread.messages = [];
      useChatStore.getState().updateSlot(slotId, { isStreaming: false, streamingContent: '' });
    });
    expect(screen.queryByRole('article', { name: 'Summarise the policy' })).not.toBeNull();
    act(() => {
      flushFrames();
    });
    expect(screen.queryByRole('article', { name: 'Summarise the policy' })).toBeNull();
  });

  it('does not carry a row over to an edited question, nor to another chat', () => {
    const { slotId, rerender } = streaming();
    const row = live();
    act(() => {
      thread.messages = [...TWO_TURNS, user('u3', 'Summarise the refund policy'), assistant('a3-final', 'Final.')];
      useChatStore.getState().updateSlot(slotId, { isStreaming: false, streamingContent: '' });
    });
    expect(screen.getByRole('article', { name: 'Summarise the refund policy' })).not.toBe(row);
    expect(renderedKeys[renderedKeys.length - 1]).toBe('a3-final');
    cleanup();

    // Another chat with the same question gets its own keys.
    renderedKeys.length = 0;
    const other = openConversation('conv-2', { isStreaming: true, streamingQuestion: 'Summarise the policy' });
    thread.messages = LIVE;
    render(<MessageList />);
    act(() => {
      thread.messages = SAVED;
      useChatStore.getState().updateSlot(other, { isStreaming: false });
    });
    expect(renderedKeys[renderedKeys.length - 1]).toBe('a3');
    void rerender;
  });

  it('gives the same question asked twice two runs of its own', () => {
    const { slotId } = streaming();
    finish(slotId);
    act(() => {
      thread.messages = [...SAVED, user('u4', 'Summarise the policy'), assistant('a4', '')];
      useChatStore.getState().updateSlot(slotId, { isStreaming: true, streamingQuestion: 'Summarise the policy' });
    });
    const rows = screen.getAllByRole('article', { name: 'Summarise the policy' });
    expect(rows).toHaveLength(2);
    const keys = new Set(renderedKeys.slice(-2));
    expect(keys.size).toBe(2);
  });
});

describe('MessageList — jump to newest', () => {
  const jumpButton = () => screen.queryByRole('button', { name: 'Latest messages' });

  function openTwoTurns() {
    thread.messages = TWO_TURNS;
    geometry.rowTop = { 'What is our refund policy?': 0, 'Who approves exceptions?': 600 };
    openConversation('conv-1');
    render(<MessageList />);
    settle();
  }

  it('is not offered while the latest question is in view', () => {
    openTwoTurns();

    expect(jumpButton()).toBeNull();
  });

  it('keeps a 44px target on a touch screen around its 28px pill', () => {
    viewport.isMobile = true;
    try {
      openTwoTurns();
      scrollTo(100);

      const button = jumpButton() as HTMLElement;
      // The shared touch target (globals.css) grows a 44px box around it on a coarse pointer.
      expect(button.classList.contains('touch-hit')).toBe(true);
      expect(button.classList.contains('rt-BaseButton')).toBe(true);
    } finally {
      viewport.isMobile = false;
    }
  });

  it('sits 8px above the composer card (under the column\'s 12px top padding) so it never covers the text box', () => {
    const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight');
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
      configurable: true,
      get(this: HTMLElement) {
        return this.style.position === 'sticky' && this.querySelector('[data-testid="composer"]') ? 120 : 0;
      },
    });
    try {
      thread.messages = TWO_TURNS;
      geometry.rowTop = { 'What is our refund policy?': 0, 'Who approves exceptions?': 600 };
      openConversation('conv-1');
      render(<MessageList composer={<div data-testid="composer" />} />);
      settle();
      scrollTo(100);

      const holder = (jumpButton() as HTMLElement).parentElement as HTMLElement;
      expect(holder.style.bottom).toBe('calc(120px - var(--space-3) + var(--space-2))');
    } finally {
      if (original) Object.defineProperty(HTMLElement.prototype, 'offsetHeight', original);
    }
  });

  it('does not force a minimum size on desktop', () => {
    openTwoTurns();
    scrollTo(100);

    const button = jumpButton() as HTMLElement;
    expect(button.style.minWidth).toBe('');
    expect(button.style.minHeight).toBe('');
  });

  it('is not offered in an empty conversation', () => {
    openConversation('conv-1');
    render(<MessageList />);
    settle();
    scrollTo(300);

    expect(jumpButton()).toBeNull();
  });

  it('appears when the reader scrolls up away from the latest message', () => {
    openTwoTurns();

    scrollTo(100);

    expect(jumpButton()).not.toBeNull();
  });

  it('stays hidden for a small scroll that keeps the latest message within reach', () => {
    openTwoTurns();

    scrollTo(560);

    expect(jumpButton()).toBeNull();
  });

  it('goes away again once the reader scrolls back down', () => {
    openTwoTurns();
    scrollTo(100);
    expect(jumpButton()).not.toBeNull();

    scrollTo(600);

    expect(jumpButton()).toBeNull();
  });

  it('takes the reader to the end of the conversation and then hides', () => {
    openTwoTurns();
    scrollTo(100);

    fireEvent.click(jumpButton() as HTMLElement);
    settle();
    expect(scroller().scrollTop).toBe(2000 - 500);

    fireEvent.scroll(scroller());
    expect(jumpButton()).toBeNull();
  });

  it('does not send anything when used', () => {
    openTwoTurns();
    scrollTo(100);

    fireEvent.click(jumpButton() as HTMLElement);

    expect(thread.append).not.toHaveBeenCalled();
  });

  it('is offered while an answer streams once the reader scrolls up, and following resumes after it is used', () => {
    thread.messages = [...TWO_TURNS, user('u3', 'Summarise the policy'), assistant('a3', '')];
    geometry.rowTop = { 'Summarise the policy': 900 };
    geometry.rowHeight = { 'Summarise the policy': 900 };
    const slotId = openConversation('conv-1', {
      isStreaming: true,
      streamingQuestion: 'Summarise the policy',
      streamingContent: 'Part one',
    });
    render(<MessageList />);
    settle();
    expect(jumpButton()).toBeNull();

    fireEvent.wheel(scroller(), { deltaY: -40 });
    scrollTo(1000);
    expect(jumpButton()).not.toBeNull();

    fireEvent.click(jumpButton() as HTMLElement);
    settle();
    act(() => {
      geometry.contentHeight = 2600;
      useChatStore.getState().updateSlot(slotId, { streamingContent: 'Part one. Part two' });
    });

    expect(scroller().scrollTop).toBe(2600 - 500);
  });

  it('appears when the latest answer grows below the view while the reader is up-thread', () => {
    thread.messages = [...TWO_TURNS, user('u3', 'Summarise the policy'), assistant('a3', 'Short')];
    geometry.rowTop = { 'Summarise the policy': 900 };
    geometry.rowHeight = { 'Summarise the policy': 200 };
    openConversation('conv-1');
    render(<MessageList />);
    settle();
    scrollTo(1000);
    expect(jumpButton()).toBeNull();

    geometry.rowHeight['Summarise the policy'] = 1200;
    act(() => {
      for (const cb of [...resizeCallbacks]) cb([], {} as ResizeObserver);
    });
    settle();

    expect(jumpButton()).not.toBeNull();
  });

  it('is gone after switching to an empty conversation', () => {
    openTwoTurns();
    scrollTo(100);
    expect(jumpButton()).not.toBeNull();

    act(() => {
      thread.messages = [];
      openConversation('conv-2');
    });
    settle();

    expect(jumpButton()).toBeNull();
  });
});

describe('MessageList — opening a chat', () => {
  it('lands on the end of the conversation, with the jump button wrapper around the list', () => {
    thread.messages = [
      user('u1', 'What is our refund policy?'), assistant('a1', 'Refunds.'),
      user('u2', 'Who approves exceptions?'), assistant('a2', 'Finance.'),
    ];
    geometry.rowTop = { 'What is our refund policy?': 0, 'Who approves exceptions?': 600 };
    openConversation('conv-1');
    render(<MessageList />);
    settle();

    expect(scroller().scrollTop).toBe(2000 - 500);
  });
});

describe('MessageList — blur above the composer dock', () => {
  function withDockHeight(height: number, run: () => void) {
    const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight');
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
      configurable: true,
      get(this: HTMLElement) {
        return this.style.position === 'sticky' && this.querySelector('[data-testid="composer"]') ? height : 0;
      },
    });
    try {
      run();
    } finally {
      if (original) Object.defineProperty(HTMLElement.prototype, 'offsetHeight', original);
      else delete (HTMLElement.prototype as { offsetHeight?: number }).offsetHeight;
    }
  }

  function blurHeight(): string {
    const dock = screen.getByTestId('composer').closest('[style*="sticky"]') as HTMLElement;
    const blur = dock.querySelector(':scope > [aria-hidden="true"]') as HTMLElement;
    return blur.style.height;
  }

  function open(patch: Parameters<typeof openConversation>[1] = {}) {
    thread.messages = TWO_TURNS;
    geometry.rowTop = { 'What is our refund policy?': 0, 'Who approves exceptions?': 600 };
    openConversation('conv-1', patch);
    render(<MessageList composer={<div data-testid="composer" />} />);
    settle();
  }

  function blur(): HTMLElement {
    const dock = screen.getByTestId('composer').closest('[style*="sticky"]') as HTMLElement;
    return dock.querySelector(':scope > [aria-hidden="true"]') as HTMLElement;
  }

  it('stays inside a short dock, so a read-only viewer\'s last lines are not under the blur', () => {
    withDockHeight(40, () => {
      open({ isOwner: false });

      expect(blurHeight()).toBe('40px');
    });
  });

  it('stays inside the dock when the composer shows too, as a plain fade into the page surface with no blur', () => {
    withDockHeight(144, () => {
      open();

      expect(blurHeight()).toBe('144px');
      // Anchored to the pane's bottom, and fading into the page's own surface in either theme.
      expect(blur().style.bottom).toBe('0px');
      expect(blur().style.background).toContain('var(--surface-raised)');
      // A backdrop filter re-blurs the band on every frame the transcript moves under it.
      expect(blur().querySelector('[style*="backdrop-filter"]')).toBeNull();
      expect(blur().style.getPropertyValue('backdrop-filter')).toBe('');
      expect(blur().style.pointerEvents).toBe('none');
      expect(blur().style.transition).toBe('');
    });
  });
});

describe('MessageList — under the top bar', () => {
  it('starts its content below the top inset, and brings a sent question to rest there', () => {
    thread.messages = TWO_TURNS;
    geometry.rowTop = { 'What is our refund policy?': 0, 'Who approves exceptions?': 600 };
    geometry.rowHeight = { 'Who approves exceptions?': 120 };
    openConversation('conv-1');
    const { rerender } = render(<MessageList topInset="60px" />);
    settle();
    expect(scroller().style.paddingTop).toBe('60px');

    geometry.rowTop['A brand new question'] = 1200;
    geometry.rowHeight['A brand new question'] = 120;
    thread.messages = [...TWO_TURNS, user('u3', 'A brand new question'), assistant('a3', 'Sure.')];
    rerender(<MessageList topInset="60px" />);
    settle();
    // The inset plus the column's top padding (a token jsdom cannot resolve, so 0 here): where the first one rests.
    expect(scroller().scrollTop).toBe(1200 - 60);
  });

  it('keeps 48px of clearance under the last answer, so its actions end clear of the fade behind the composer', () => {
    thread.messages = TWO_TURNS;
    openConversation('conv-1');
    render(<MessageList />);
    const column = scroller().querySelector('.chat-column-gutter') as HTMLElement;
    expect(column.style.paddingBottom).toBe('var(--space-8)');
  });
});

describe('MessageList — the question rail and the column gutter', () => {
  function openTwoTurns() {
    thread.messages = TWO_TURNS;
    geometry.rowTop = { 'What is our refund policy?': 0, 'Who approves exceptions?': 600 };
    openConversation('conv-1');
    render(<MessageList />);
    settle();
  }

  it('outlines the chat from 1024px: a tick per question, each a button named by its question', () => {
    openTwoTurns();
    const rail = screen.getByRole('navigation', { name: 'Questions in this chat' });
    const ticks = within(rail).getAllByRole('button');
    expect(ticks.map((tick) => tick.getAttribute('aria-label'))).toEqual(['What is our refund policy?', 'Who approves exceptions?']);
    expect(ticks.every((tick) => tick.getAttribute('data-testid') === 'rail-tick')).toBe(true);
  });

  it('has no rail below 1024px', () => {
    viewport.isMobile = true;
    try {
      openTwoTurns();
      expect(screen.queryByRole('navigation', { name: 'Questions in this chat' })).toBeNull();
    } finally {
      viewport.isMobile = false;
    }
  });

  it("takes its side gutter from the shared column class, not an inline padding", () => {
    openTwoTurns();
    const column = document.querySelector('.chat-column-gutter') as HTMLElement;
    expect(column).toBeTruthy();
    expect(column.style.paddingLeft).toBe('');
    expect(column.style.paddingRight).toBe('');
  });
});

describe('MessageList: the answer the column beside it describes', () => {
  it('reads it again when older answers are added above while the list sits at its top, with no scroll', async () => {
    const [u1, a1, u2, a2] = TWO_TURNS;
    thread.messages = [u2, a2];
    geometry.rowTop = { 'Who approves exceptions?': 0 };
    const slotId = openConversation('conv-1', {
      messagePagination: { currentPage: 1, hasOlderMessages: true, isLoadingOlder: false },
    });
    const view = render(<MessageList />);
    settle();
    // The reader goes to the top of what has loaded.
    scrollTo(0);
    act(() => flushFrames());
    const keyOf = (i: number) => (document.querySelectorAll('[data-answer-id]')[i] as HTMLElement).dataset.answerId;
    expect(useAnswerPanelStore.getState().readingAnswer[slotId]).toBe(keyOf(0));

    // The older page lands above; the browser keeps the list at its top, so nothing scrolls.
    thread.messages = [u1, a1, u2, a2];
    geometry.rowTop = { 'What is our refund policy?': 0, 'Who approves exceptions?': 400 };
    view.rerender(<MessageList />);
    await act(async () => {});
    act(() => flushFrames());
    expect(document.querySelectorAll('[data-answer-id]')).toHaveLength(2);
    expect(scroller().scrollTop).toBe(0);
    expect(useAnswerPanelStore.getState().readingAnswer[slotId]).toBe(keyOf(0));
  });
});
