import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, act, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { QuoteSelectionLayer } from '../quote-selection-toolbar';
import { useCommandStore } from '@/lib/store/command-store';
import { QUOTED_SELECTION_MAX_CHARS } from '../../../utils/quoted-selection';

beforeEach(() => {
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    cb(0);
    return 0;
  });
});

afterEach(() => {
  cleanup();
  useCommandStore.setState({ handlers: {}, pending: {} });
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function mockSelection(text: string, ancestor: Node) {
  const range = {
    collapsed: !text,
    commonAncestorContainer: ancestor,
    getBoundingClientRect: () => ({
      top: 80,
      left: 40,
      width: 120,
      height: 18,
      bottom: 98,
      right: 160,
      x: 40,
      y: 80,
      toJSON: () => ({}),
    }),
  };
  vi.spyOn(window, 'getSelection').mockReturnValue({
    isCollapsed: !text,
    rangeCount: text ? 1 : 0,
    toString: () => text,
    getRangeAt: () => range,
    removeAllRanges: vi.fn(),
  } as unknown as Selection);
}

/** The layer over a finished answer, for someone who may send, with `text` selected in it. */
function selected(text: string, props: Partial<React.ComponentProps<typeof QuoteSelectionLayer>> = {}) {
  render(
    <Theme>
      <QuoteSelectionLayer enabled canQuote {...props}>
        <p>{text}</p>
      </QuoteSelectionLayer>
      <button type="button">composer</button>
    </Theme>,
  );
  const root = screen.getByTestId('answer-quotable');
  mockSelection(text, root);
  fireEvent.pointerUp(root.firstChild as Element);
  return root;
}

const bar = () => screen.queryByTestId('quote-selection-toolbar');
const names = () => within(bar() as HTMLElement).getAllByRole('button').map((b) => b.querySelector('.rt-Text')?.textContent);
const press = (name: string) => fireEvent.click(within(bar() as HTMLElement).getByRole('button', { name: new RegExp(name) }));

describe('QuoteSelectionLayer', () => {
  it('offers Reply, Ask PipesHub and Copy after selecting text inside a finished answer, and no button named Quote', () => {
    selected('The SLA is 99.9 percent.');
    expect(names()).toEqual(['Reply', 'Ask PipesHub', 'Copy']);
    expect(screen.getByRole('group', { name: 'Selected text' })).toBe(bar());
    expect(screen.queryByRole('button', { name: /quote/i })).toBeNull();
  });

  it('shows nothing while the answer is being written', () => {
    selected('The SLA is 99.9 percent.', { enabled: false });
    expect(bar()).toBeNull();
  });

  it.each(['Reply', 'Ask PipesHub'])('%s puts the excerpt on the composer, as a quote, and closes the bar', (name) => {
    const received: unknown[] = [];
    useCommandStore.getState().register('showQuoteBar', (payload) => received.push(payload));
    selected('Invoice 441 is overdue.');
    press(name);
    expect(received).toEqual([{ text: 'Invoice 441 is overdue.', truncated: false }]);
    expect(window.getSelection()!.removeAllRanges).toHaveBeenCalled();
    expect(bar()).toBeNull();
  });

  it('a long excerpt is clipped and says so on the composer; Copy copies all of it', async () => {
    const received: Array<{ text: string; truncated: boolean }> = [];
    useCommandStore.getState().register('showQuoteBar', (payload) => received.push(payload as never));
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    const long = 'word '.repeat(1200).trim();
    selected(long);
    await act(async () => press('Copy'));
    expect(writeText).toHaveBeenCalledWith(long);
    press('Reply');
    expect(received[0].truncated).toBe(true);
    expect(received[0].text.length).toBeLessThanOrEqual(QUOTED_SELECTION_MAX_CHARS);
  });

  it('Copy copies the selected words and reads Copied; the composer takes nothing and the bar stays', async () => {
    const received: unknown[] = [];
    useCommandStore.getState().register('showQuoteBar', (payload) => received.push(payload));
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    const root = selected('Invoice 441 is overdue.');
    await act(async () => press('Copy'));
    expect(writeText).toHaveBeenCalledWith('Invoice 441 is overdue.');
    expect(names()).toEqual(['Reply', 'Ask PipesHub', 'Copied']);
    expect(received).toEqual([]);
    // Other words selected next have not been copied.
    mockSelection('441', root);
    fireEvent.pointerUp(root.firstChild as Element);
    expect(names()).toEqual(['Reply', 'Ask PipesHub', 'Copy']);
  });

  it('is Copy alone for someone who may not send', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    selected('The SLA is 99.9 percent.', { canQuote: false });
    expect(names()).toEqual(['Copy']);
    await act(async () => press('Copy'));
    expect(writeText).toHaveBeenCalledWith('The SLA is 99.9 percent.');
  });

  it('keeps the selection: pressing on the bar is not a click into the page', () => {
    selected('Keep this sentence.');
    expect(fireEvent.mouseDown(bar() as HTMLElement)).toBe(false);
  });

  it('hides when the click is outside the answer', () => {
    selected('Keep this sentence.');
    expect(bar()).toBeTruthy();
    fireEvent.pointerUp(screen.getByRole('button', { name: 'composer' }));
    expect(bar()).toBeNull();
  });

  it('stays when Tab moves focus onto it and the selection collapses', () => {
    const root = selected('The SLA is 99.9 percent.');
    const button = within(bar() as HTMLElement).getAllByRole('button')[0];
    button.focus();
    mockSelection('', root);
    fireEvent.keyUp(button, { key: 'Tab' });
    expect(bar()).toBeTruthy();
  });

  it('shows after a keyboard selection inside a finished answer', () => {
    const root = selected('The SLA is 99.9 percent.');
    act(() => {
      fireEvent.keyDown(document, { key: 'Escape' });
    });
    expect(bar()).toBeNull();
    mockSelection('99.9 percent.', root);
    fireEvent.keyUp(document, { key: 'ArrowRight', shiftKey: true });
    expect(bar()).toBeTruthy();
  });

  it('hides on Escape, and on a scroll', () => {
    const root = selected('Keep this sentence.');
    act(() => {
      fireEvent.keyDown(document, { key: 'Escape' });
    });
    expect(bar()).toBeNull();
    fireEvent.pointerUp(root.firstChild as Element);
    expect(bar()).toBeTruthy();
    act(() => {
      fireEvent.scroll(document);
    });
    expect(bar()).toBeNull();
  });
});
