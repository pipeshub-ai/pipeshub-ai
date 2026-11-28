import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';

import { ToastContainer } from '../toast-container';
import { toast, useToastStore } from '@/lib/store/toast-store';

vi.mock('@/app/components/theme-provider', () => ({ useThemeAppearance: () => ({ appearance: 'light' }) }));

// The corner a bottom toast takes on a 1280 × 720 screen. jsdom lays nothing out, so the region reports it.
const CORNER = { left: 904, top: 620, right: 1264, bottom: 704 };
const IN_THE_CORNER = { x: 1100, y: 680 };
const ELSEWHERE = { x: 300, y: 200 };

beforeEach(() => {
  vi.useFakeTimers();
  window.matchMedia = vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() });
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const box = this.hasAttribute('data-ph-toast-region') ? CORNER : { left: 0, top: 0, right: 0, bottom: 0 };
    return { ...box, x: box.left, y: box.top, width: box.right - box.left, height: box.bottom - box.top, toJSON: () => box };
  });
});
afterEach(() => {
  cleanup();
  act(() => useToastStore.getState().clearAll());
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const status = () => screen.getByRole('status');
const shown = () => useToastStore.getState().toasts.find((t) => !t.isExiting && !t.isWaiting)?.title;
const paused = () => useToastStore.getState().isHovered;
const advance = (ms: number) =>
  act(() => {
    vi.advanceTimersByTime(ms);
  });
/** The pointer comes to rest at `at`: the pointer events a browser sends as it moves there. */
const pointerTo = (at: { x: number; y: number }) =>
  window.dispatchEvent(new MouseEvent('pointermove', { clientX: at.x, clientY: at.y }));
/** The hand moves over the card, as on its way to the button under it: the enter and moves a browser reports. */
const moveOver = (el: HTMLElement) => {
  fireEvent.mouseEnter(el);
  fireEvent.mouseMove(el);
  fireEvent.mouseMove(el);
};

describe('a toast that comes up under the resting pointer', () => {
  it('does not pause while the pointer moves on it, and closes on its own clock', () => {
    render(<ToastContainer />);
    pointerTo(IN_THE_CORNER);
    act(() => {
      toast.success('Connector created', { duration: 3000 });
    });
    moveOver(screen.getByText('Connector created'));
    expect(paused()).toBe(false);
    advance(2999);
    expect(shown()).toBe('Connector created');
    advance(1);
    expect(shown()).toBeUndefined();
  });

  it('pauses once the pointer has left it and come back, and leaving again spends only the time it had left', () => {
    render(<ToastContainer />);
    pointerTo(IN_THE_CORNER);
    act(() => {
      toast.success('Connector created', { duration: 3000 });
    });
    moveOver(status());
    advance(1000);
    fireEvent.mouseLeave(status());
    pointerTo(ELSEWHERE);
    expect(paused()).toBe(false);
    moveOver(status());
    expect(paused()).toBe(true);
    advance(10_000);
    expect(shown()).toBe('Connector created');
    fireEvent.mouseLeave(status());
    advance(1999);
    expect(shown()).toBe('Connector created');
    advance(1);
    expect(shown()).toBeUndefined();
  });

  it('still pauses while it has focus', () => {
    render(<ToastContainer />);
    pointerTo(IN_THE_CORNER);
    act(() => {
      toast.success('Connector created', { duration: 3000 });
    });
    act(() => screen.getByRole('button', { name: 'Close' }).focus());
    expect(paused()).toBe(true);
    advance(10_000);
    expect(shown()).toBe('Connector created');
  });
});

describe('a toast that comes up away from the pointer', () => {
  it('pauses when the pointer goes to it', () => {
    render(<ToastContainer />);
    pointerTo(ELSEWHERE);
    act(() => {
      toast.success('Saved');
    });
    moveOver(status());
    expect(paused()).toBe(true);
    advance(10_000);
    expect(shown()).toBe('Saved');
  });

  it('keeps Undo within reach: pointing at the toast holds it past its clock, and Undo works', () => {
    const onClick = vi.fn();
    render(<ToastContainer />);
    pointerTo(ELSEWHERE);
    act(() => {
      toast.success('Chat archived', { action: { label: 'Undo', onClick } });
    });
    moveOver(screen.getByRole('button', { name: 'Undo' }));
    advance(10_000);
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(shown()).toBeUndefined();
  });
});

describe('a press on a toast', () => {
  it('on its text closes it, title or description', () => {
    render(<ToastContainer />);
    act(() => {
      toast.success('Connector created', { description: 'Configure it next.' });
    });
    fireEvent.click(screen.getByText('Connector created'));
    expect(shown()).toBeUndefined();
    act(() => {
      toast.info('Copied link', { description: 'Paste it anywhere.' });
    });
    fireEvent.click(screen.getByText('Paste it anywhere.'));
    expect(shown()).toBeUndefined();
  });

  it('on Undo runs Undo, also on a toast that came up under the pointer', () => {
    const onClick = vi.fn();
    render(<ToastContainer />);
    pointerTo(IN_THE_CORNER);
    act(() => {
      toast.success('Chat archived', { action: { label: 'Undo', onClick } });
    });
    moveOver(screen.getByRole('button', { name: 'Undo' }));
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(shown()).toBeUndefined();
  });

  it('leaves a toast that has no close button: a loading one, or one whose caller passed false', () => {
    render(<ToastContainer />);
    act(() => {
      toast.loading('Saving…');
    });
    fireEvent.click(screen.getByText('Saving…'));
    expect(shown()).toBe('Saving…');
    act(() => {
      toast.info('Link copied', { showCloseButton: false });
    });
    fireEvent.click(screen.getByText('Link copied'));
    expect(shown()).toBe('Link copied');
  });

  it('does not close it while its text is being selected', () => {
    render(<ToastContainer />);
    act(() => {
      toast.success('Connector created');
    });
    vi.spyOn(window, 'getSelection').mockReturnValue({ toString: () => 'Connector' } as Selection);
    fireEvent.click(screen.getByText('Connector created'));
    expect(shown()).toBe('Connector created');
  });
});
