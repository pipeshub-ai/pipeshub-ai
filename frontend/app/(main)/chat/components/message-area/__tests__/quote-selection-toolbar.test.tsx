import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { QuoteSelectionLayer } from '../quote-selection-toolbar';
import { useCommandStore } from '@/lib/store/command-store';

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

describe('QuoteSelectionLayer', () => {
  it('shows Quote after selecting text inside a finished answer', () => {
    render(
      <Theme>
        <QuoteSelectionLayer enabled>
          <p>The SLA is 99.9 percent.</p>
        </QuoteSelectionLayer>
      </Theme>,
    );
    const root = screen.getByTestId('answer-quotable');
    mockSelection('The SLA is 99.9 percent.', root);
    fireEvent.pointerUp(root.firstChild as Element);
    expect(screen.getByTestId('quote-selection-toolbar')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Ask about this' })).toBeTruthy();
  });

  it('does not show Quote while quoting is disabled (streaming / read-only)', () => {
    render(
      <Theme>
        <QuoteSelectionLayer enabled={false}>
          <p>The SLA is 99.9 percent.</p>
        </QuoteSelectionLayer>
      </Theme>,
    );
    const root = screen.getByTestId('answer-quotable');
    mockSelection('The SLA is 99.9 percent.', root);
    fireEvent.pointerUp(root.firstChild as Element);
    expect(screen.queryByTestId('quote-selection-toolbar')).toBeNull();
  });

  it('dispatches showQuoteBar with the selected excerpt', () => {
    const received: unknown[] = [];
    useCommandStore.getState().register('showQuoteBar', (payload) => received.push(payload));
    render(
      <Theme>
        <QuoteSelectionLayer enabled>
          <p>Invoice 441 is overdue.</p>
        </QuoteSelectionLayer>
      </Theme>,
    );
    const root = screen.getByTestId('answer-quotable');
    mockSelection('Invoice 441 is overdue.', root);
    fireEvent.pointerUp(root.firstChild as Element);
    fireEvent.click(screen.getByTestId('quote-selection-button'));
    expect(received).toEqual([{ text: 'Invoice 441 is overdue.', truncated: false }]);
    expect(screen.queryByTestId('quote-selection-toolbar')).toBeNull();
  });

  it('hides Quote when the click is outside the answer', () => {
    render(
      <Theme>
        <div>
          <QuoteSelectionLayer enabled>
            <p>Keep this sentence.</p>
          </QuoteSelectionLayer>
          <button type="button">composer</button>
        </div>
      </Theme>,
    );
    const root = screen.getByTestId('answer-quotable');
    mockSelection('Keep this sentence.', root);
    fireEvent.pointerUp(root.firstChild as Element);
    expect(screen.getByTestId('quote-selection-toolbar')).toBeTruthy();
    fireEvent.pointerUp(screen.getByRole('button', { name: 'composer' }));
    expect(screen.queryByTestId('quote-selection-toolbar')).toBeNull();
  });

  it('hides Quote on Escape', () => {
    render(
      <Theme>
        <QuoteSelectionLayer enabled>
          <p>Keep this sentence.</p>
        </QuoteSelectionLayer>
      </Theme>,
    );
    const root = screen.getByTestId('answer-quotable');
    mockSelection('Keep this sentence.', root);
    fireEvent.pointerUp(root.firstChild as Element);
    expect(screen.getByTestId('quote-selection-toolbar')).toBeTruthy();
    act(() => {
      fireEvent.keyDown(document, { key: 'Escape' });
    });
    expect(screen.queryByTestId('quote-selection-toolbar')).toBeNull();
  });
});
