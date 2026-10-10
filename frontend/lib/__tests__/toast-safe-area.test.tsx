import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { createRef } from 'react';
import {
  DRAWER_TOAST_INSET_PX,
  composerBottomInset,
  TOAST_SAFE_BOTTOM_VAR,
  getCompactOverlayOpen,
  TOAST_SAFE_RIGHT_VAR,
  publishToastInset,
  resolveToastPlacement,
  useToastComposerInset,
  useToastDrawerInset,
  useToastElementInset,
} from '../toast-safe-area';

const read = (v: string) => document.documentElement.style.getPropertyValue(v);

function Composer({ ref }: { ref: React.RefObject<HTMLDivElement | null> }) {
  useToastComposerInset(ref);
  return <div ref={ref} />;
}
function Floating({ el }: { el: HTMLElement | null }) {
  useToastElementInset(el);
  return null;
}
function Drawer({ open }: { open: boolean }) {
  useToastDrawerInset(open, DRAWER_TOAST_INSET_PX);
  return null;
}

afterEach(() => {
  cleanup();
  document.documentElement.removeAttribute('style');
});

describe('resolveToastPlacement', () => {
  it('forces top on mobile regardless of the request, and bottom while a full-screen overlay is open', () => {
    expect(resolveToastPlacement('bottom', true)).toBe('top');
    expect(resolveToastPlacement(undefined, true)).toBe('top');
    expect(resolveToastPlacement('top', true, true)).toBe('bottom');
  });
  it('honours the request on desktop, defaulting to bottom', () => {
    expect(resolveToastPlacement('top', false)).toBe('top');
    expect(resolveToastPlacement('bottom', false)).toBe('bottom');
    expect(resolveToastPlacement(undefined, false)).toBe('bottom');
  });
});

describe('composerBottomInset', () => {
  it('is zero for a centered composer and top-edge distance plus gap when docked', () => {
    expect(composerBottomInset({ top: 300 }, 900)).toBe(0);
    expect(composerBottomInset({ top: 772 }, 900)).toBe(136);
  });
});

describe('publishToastInset', () => {
  it('keeps the largest contribution and removes the variable when all withdraw', () => {
    const a = publishToastInset(TOAST_SAFE_BOTTOM_VAR, 100);
    const b = publishToastInset(TOAST_SAFE_BOTTOM_VAR, 140);
    expect(read(TOAST_SAFE_BOTTOM_VAR)).toBe('140px');
    b();
    expect(read(TOAST_SAFE_BOTTOM_VAR)).toBe('100px');
    a();
    expect(read(TOAST_SAFE_BOTTOM_VAR)).toBe('');
  });
});

describe('composer and drawer insets', () => {
  it('publishes nothing for a composer in the upper half and cleans up on unmount', () => {
    const ref = createRef<HTMLDivElement>();
    const { unmount } = render(<Composer ref={ref} />);
    expect(read(TOAST_SAFE_BOTTOM_VAR)).toBe('');
    unmount();
    expect(read(TOAST_SAFE_BOTTOM_VAR)).toBe('');
  });

  it('publishes the distance to the composer top while docked and withdraws on unmount', () => {
    Object.defineProperty(window, 'innerHeight', { value: 900, configurable: true });
    const orig = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = () => ({ top: 772, height: 90 }) as DOMRect;
    try {
      const ref = createRef<HTMLDivElement>();
      const { unmount } = render(<Composer ref={ref} />);
      expect(read(TOAST_SAFE_BOTTOM_VAR)).toBe('136px');
      unmount();
      expect(read(TOAST_SAFE_BOTTOM_VAR)).toBe('');
    } finally {
      Element.prototype.getBoundingClientRect = orig;
    }
  });

  it('publishes an element that mounts later (a popover above the composer) and withdraws it when it goes', () => {
    Object.defineProperty(window, 'innerHeight', { value: 900, configurable: true });
    const el = document.createElement('div');
    el.getBoundingClientRect = () => ({ top: 640 }) as DOMRect;
    const { rerender, unmount } = render(<Floating el={null} />);
    expect(read(TOAST_SAFE_BOTTOM_VAR)).toBe('');
    rerender(<Floating el={el} />);
    expect(read(TOAST_SAFE_BOTTOM_VAR)).toBe('268px');
    rerender(<Floating el={null} />);
    expect(read(TOAST_SAFE_BOTTOM_VAR)).toBe('');
    rerender(<Floating el={el} />);
    unmount();
    expect(read(TOAST_SAFE_BOTTOM_VAR)).toBe('');
  });

  it('a card above the composer raises the stack past the composer and gives it back when dismissed', () => {
    Object.defineProperty(window, 'innerHeight', { value: 900, configurable: true });
    const composer = publishToastInset(TOAST_SAFE_BOTTOM_VAR, 136);
    const card = document.createElement('div');
    card.getBoundingClientRect = () => ({ top: 640 }) as DOMRect;
    const { unmount } = render(<Floating el={card} />);
    expect(read(TOAST_SAFE_BOTTOM_VAR)).toBe('268px');
    unmount();
    expect(read(TOAST_SAFE_BOTTOM_VAR)).toBe('136px');
    composer();
  });

  it('shifts left of an open drawer on wide viewports and cleans up on close', () => {
    Object.defineProperty(window, 'innerWidth', { value: 1440, configurable: true });
    const { rerender, unmount } = render(<Drawer open />);
    expect(read(TOAST_SAFE_RIGHT_VAR)).toBe(`${DRAWER_TOAST_INSET_PX}px`);
    rerender(<Drawer open={false} />);
    expect(read(TOAST_SAFE_RIGHT_VAR)).toBe('');
    rerender(<Drawer open />);
    unmount();
    expect(read(TOAST_SAFE_RIGHT_VAR)).toBe('');
  });

  it('does not shift when the drawer leaves no room for a toast', () => {
    Object.defineProperty(window, 'innerWidth', { value: 900, configurable: true });
    render(<Drawer open />);
    expect(read(TOAST_SAFE_RIGHT_VAR)).toBe('');
  });

  it('on phones holds the compact-overlay flag (toasts go to the bottom) and cleans up', () => {
    Object.defineProperty(window, 'innerWidth', { value: 375, configurable: true });
    const { unmount } = render(<Drawer open />);
    expect(getCompactOverlayOpen()).toBe(true);
    expect(read(TOAST_SAFE_RIGHT_VAR)).toBe('');
    unmount();
    expect(getCompactOverlayOpen()).toBe(false);
  });
});
