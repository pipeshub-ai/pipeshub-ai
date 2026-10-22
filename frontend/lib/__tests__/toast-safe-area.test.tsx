import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import {
  DRAWER_TOAST_INSET_PX,
  TOAST_STACKED_VAR,
  getCompactOverlayOpen,
  TOAST_SAFE_RIGHT_VAR,
  publishToastInset,
  resolveToastPlacement,
  useToastDrawerInset,
} from '../toast-safe-area';

const read = (v: string) => document.documentElement.style.getPropertyValue(v);

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

describe('publishToastInset', () => {
  it('keeps the largest contribution and removes the variable when all withdraw', () => {
    const a = publishToastInset(TOAST_STACKED_VAR, 100);
    const b = publishToastInset(TOAST_STACKED_VAR, 140);
    expect(read(TOAST_STACKED_VAR)).toBe('140px');
    b();
    expect(read(TOAST_STACKED_VAR)).toBe('100px');
    a();
    expect(read(TOAST_STACKED_VAR)).toBe('');
  });
});

describe('drawer insets', () => {
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
