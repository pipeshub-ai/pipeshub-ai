import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, act, fireEvent } from '@testing-library/react';
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
  useThread: () => ({ messages: thread.messages }),
  useThreadRuntime: () => ({ append: thread.append }),
}));

vi.mock('../../../streaming', () => ({
  loadOlderMessagesForSlot: (slotId: string) => loadOlder(slotId),
}));

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

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

vi.mock('../chat-response', () => ({
  ChatResponse: (props: {
    question: string;
    answer: string;
    isStreaming: boolean;
    streamingContent?: string;
    unanswered?: boolean;
  }) => (
    <article aria-label={props.question} data-streaming={props.isStreaming ? 'yes' : 'no'}>
      <h3>{props.question}</h3>
      {props.unanswered ? null : <p>{props.answer}</p>}
      {props.streamingContent ? <p>{props.streamingContent}</p> : null}
    </article>
  ),
}));

import { MessageList } from '../message-list';
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
  loadOlder.mockReset();
  loadOlder.mockResolvedValue(undefined);
  geometry.viewportHeight = 500;
  geometry.contentHeight = 2000;
  geometry.rowTop = {};
  geometry.rowHeight = {};
  frames = [];
  resizeCallbacks.length = 0;

  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => frames.push(cb));
  vi.stubGlobal('cancelAnimationFrame', () => {});
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });

  HTMLElement.prototype.scrollTo = function scrollTo(this: HTMLElement, arg?: ScrollToOptions | number) {
    if (typeof arg === 'object' && arg?.top !== undefined) this.scrollTop = arg.top;
  } as HTMLElement['scrollTo'];
  HTMLElement.prototype.getBoundingClientRect = function rect(this: HTMLElement) {
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

describe('MessageList — scrolling', () => {
  it('opens a conversation with the latest question at the top of the view', () => {
    thread.messages = TWO_TURNS;
    geometry.rowTop = { 'What is our refund policy?': 0, 'Who approves exceptions?': 600 };
    openConversation('conv-1');

    render(<MessageList />);
    settle();

    expect(scroller().scrollTop).toBe(600);
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

  it('does not jump when the streamed answer is replaced by the saved one', () => {
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
    scroller().scrollTop = 1100;
    act(() => {
      useChatStore.getState().updateSlot(slotId, { streamingContent: 'Part one. Part two' });
    });

    act(() => {
      geometry.contentHeight = 800;
      thread.messages = [...TWO_TURNS, user('u3', 'Summarise the policy'), assistant('a3-final', 'Final.')];
      useChatStore.getState().updateSlot(slotId, { isStreaming: false, streamingContent: '' });
    });

    expect(scroller().scrollTop).toBe(1100);
    const spacer = scroller().querySelector('[aria-hidden="true"]') as HTMLElement;
    expect(parseFloat(spacer.style.minHeight)).toBeGreaterThan(0);
  });

  it('sizes the bottom spacer so the last question can reach the top of the view', () => {
    thread.messages = TWO_TURNS;
    geometry.rowTop = { 'What is our refund policy?': 0, 'Who approves exceptions?': 600 };
    geometry.rowHeight = { 'Who approves exceptions?': 120 };
    openConversation('conv-1');

    render(<MessageList />);
    settle();

    const spacer = scroller().querySelector('[aria-hidden="true"]') as HTMLElement;
    expect(spacer.style.minHeight).toBe(`${500 - 120}px`);

    act(() => {
      geometry.rowHeight['Who approves exceptions?'] = 300;
      for (const cb of [...resizeCallbacks]) cb([], {} as ResizeObserver);
      flushFrames();
    });
    expect(spacer.style.minHeight).toBe(`${500 - 300}px`);
  });
});
