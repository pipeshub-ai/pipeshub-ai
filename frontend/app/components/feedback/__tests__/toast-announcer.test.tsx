/**
 * Screen readers hear toasts through live regions that are on the page before any toast
 * arrives (a region inserted together with its text is often not announced): the polite
 * `status` region, and the `alert` region for errors. One toast shows at a time, in one of them.
 */
import React from 'react';
import { act } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/components/theme-provider', () => ({ useThemeAppearance: () => ({ appearance: 'light' }) }));
vi.mock('../toast', () => ({
  TOAST_WIDTH: '360px',
  Toast: ({ toast }: { toast: { title: string; description?: string } }) => (
    <span>{[toast.title, toast.description].filter(Boolean).join('. ')}</span>
  ),
}));

import { ToastContainer } from '../toast-container';
import { useToastStore } from '@/lib/store/toast-store';

const region = (role: 'status' | 'alert') =>
  document.querySelector(`[data-ph-toast-region][role="${role}"]`) as HTMLElement;
const polite = () => region('status');

afterEach(() => {
  cleanup();
  useToastStore.setState({ toasts: [] });
});

describe('what screen readers hear of toasts', () => {
  it('a polite live region exists before any toast', () => {
    render(<ToastContainer />);
    expect(polite().getAttribute('aria-live')).toBe('polite');
    expect(polite().getAttribute('aria-atomic')).toBe('true');
    expect(polite().textContent).toBe('');
  });

  it('announces a new toast with its description', () => {
    render(<ToastContainer />);
    act(() => {
      useToastStore.getState().addToast({ variant: 'loading', title: 'Turning on sync for Wiki', description: 'This can take a few seconds.' });
    });
    expect(polite().textContent).toBe('Turning on sync for Wiki. This can take a few seconds.');
  });

  it('announces the result when a progress toast is updated in place', () => {
    render(<ToastContainer />);
    let id = '';
    act(() => {
      id = useToastStore.getState().addToast({ variant: 'loading', title: 'Turning on sync for Wiki' });
    });
    act(() => {
      useToastStore.getState().updateToast(id, { variant: 'success', title: 'Sync is on for Wiki' });
    });
    expect(polite().textContent).toBe('Sync is on for Wiki');
  });

  it('announces an error at once, in the alert region, when the newest progress toast fails', () => {
    render(<ToastContainer />);
    let newer = '';
    act(() => {
      useToastStore.getState().addToast({ variant: 'loading', title: 'Turning on sync for Wiki' });
      newer = useToastStore.getState().addToast({ variant: 'loading', title: 'Turning on sync for Docs' });
    });
    act(() => {
      useToastStore.getState().updateToast(newer, { variant: 'error', title: "Couldn't change sync for Docs" });
    });
    expect(region('alert').getAttribute('aria-live')).toBe('assertive');
    expect(region('alert').textContent).toBe("Couldn't change sync for Docs");
  });

  it('a second toast with the same text is new content in the region, which a screen reader hears', () => {
    render(<ToastContainer />);
    act(() => {
      useToastStore.getState().addToast({ variant: 'success', title: 'Saved', duration: null });
    });
    const first = polite().firstElementChild;
    act(() => {
      useToastStore.getState().addToast({ variant: 'success', title: 'Saved', duration: null });
    });
    expect(polite().textContent).toBe('Saved');
    expect(polite().firstElementChild).not.toBe(first);
  });

  it('announces an older progress toast when it is the one that just changed', () => {
    render(<ToastContainer />);
    let older = '';
    act(() => {
      older = useToastStore.getState().addToast({ variant: 'loading', title: 'Turning on sync for Team wiki' });
      useToastStore.getState().addToast({ variant: 'loading', title: 'Turning on sync for Docs wiki' });
    });
    expect(polite().textContent).toBe('Turning on sync for Docs wiki');
    act(() => {
      useToastStore.getState().updateToast(older, { variant: 'success', title: 'Sync is on for Team wiki' });
    });
    expect(polite().textContent).toBe('Sync is on for Team wiki');
  });

  it('says nothing new when a toast is only dismissed', async () => {
    vi.useFakeTimers();
    try {
      render(<ToastContainer />);
      let id = '';
      act(() => {
        id = useToastStore.getState().addToast({ variant: 'success', title: 'Sync is on for Docs wiki', duration: null });
      });
      act(() => {
        useToastStore.getState().removeToast(id);
      });
      await act(async () => {
        await vi.runAllTimersAsync();
      });
      // An emptied live region is not read out.
      expect(polite().textContent).toBe('');
      expect(region('alert').textContent).toBe('');
    } finally {
      vi.useRealTimers();
    }
  });

  it('a re-render that changes no toast leaves what the region holds', () => {
    render(<ToastContainer />);
    act(() => {
      useToastStore.getState().addToast({ variant: 'success', title: 'Saved', duration: null });
    });
    const card = polite().firstElementChild;
    // Pointing at the card re-renders the corner without changing any toast.
    act(() => useToastStore.getState().setHovered(true));
    expect(polite().textContent).toBe('Saved');
    expect(polite().firstElementChild).toBe(card);
  });
});
