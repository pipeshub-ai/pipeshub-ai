/**
 * Screen readers hear toasts through one live region that is on the page before any
 * toast arrives: a region inserted together with its text is often not announced.
 */
import React from 'react';
import { act } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/components/theme-provider', () => ({ useThemeAppearance: () => ({ appearance: 'light' }) }));
vi.mock('../toast', () => ({ Toast: ({ toast }: { toast: { title: string } }) => <span>{toast.title}</span> }));

import { ToastContainer } from '../toast-container';
import { useToastStore } from '@/lib/store/toast-store';

const announcer = () => document.querySelector('[data-ph-toast-announcer]') as HTMLElement;

afterEach(() => {
  cleanup();
  useToastStore.setState({ toasts: [] });
});

describe('toast announcer', () => {
  it('is a polite live region that exists before any toast', () => {
    render(<ToastContainer />);
    expect(announcer().getAttribute('role')).toBe('status');
    expect(announcer().getAttribute('aria-live')).toBe('polite');
    expect(announcer().textContent).toBe('');
  });

  it('announces a new toast with its description', () => {
    render(<ToastContainer />);
    act(() => {
      useToastStore.getState().addToast({ variant: 'loading', title: 'Turning on sync for Wiki', description: 'This can take a few seconds.' });
    });
    expect(announcer().textContent).toBe('Turning on sync for Wiki. This can take a few seconds.');
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
    expect(announcer().textContent).toBe('Sync is on for Wiki');
  });

  it('announces the newest toast when an older one is still open', () => {
    render(<ToastContainer />);
    let newer = '';
    act(() => {
      useToastStore.getState().addToast({ variant: 'loading', title: 'Turning on sync for Wiki' });
      newer = useToastStore.getState().addToast({ variant: 'loading', title: 'Turning on sync for Docs' });
    });
    act(() => {
      useToastStore.getState().updateToast(newer, { variant: 'error', title: "Couldn't change sync for Docs" });
    });
    expect(announcer().textContent).toBe("Couldn't change sync for Docs");
  });

  it('announces a second toast with the same text by clearing the region first', async () => {
    vi.useFakeTimers();
    try {
      render(<ToastContainer />);
      const seen: string[] = [];
      const observer = new MutationObserver(() => seen.push(announcer().textContent ?? ''));
      observer.observe(announcer(), { childList: true, characterData: true, subtree: true });

      act(() => {
        useToastStore.getState().addToast({ variant: 'success', title: 'Saved', duration: null });
      });
      await act(async () => {
        await vi.runAllTimersAsync();
      });
      act(() => {
        useToastStore.getState().addToast({ variant: 'success', title: 'Saved', duration: null });
      });
      await act(async () => {
        await vi.runAllTimersAsync();
      });
      observer.disconnect();

      expect(announcer().textContent).toBe('Saved');
      // Saved, then cleared, then Saved again: the second toast is a change a screen reader hears.
      expect(seen.filter((t) => t === 'Saved').length).toBeGreaterThanOrEqual(2);
      expect(seen.indexOf('', seen.indexOf('Saved'))).toBeGreaterThan(seen.indexOf('Saved'));
    } finally {
      vi.useRealTimers();
    }
  });

  it('announces an older toast when it is the one that just changed', () => {
    render(<ToastContainer />);
    let older = '';
    act(() => {
      older = useToastStore.getState().addToast({ variant: 'loading', title: 'Turning on sync for Team wiki' });
      useToastStore.getState().addToast({ variant: 'loading', title: 'Turning on sync for Docs wiki' });
    });
    expect(announcer().textContent).toBe('Turning on sync for Docs wiki');
    act(() => {
      useToastStore.getState().updateToast(older, { variant: 'success', title: 'Sync is on for Team wiki' });
    });
    expect(announcer().textContent).toBe('Sync is on for Team wiki');
  });

  it('says nothing new when a toast is only dismissed', () => {
    render(<ToastContainer />);
    let newer = '';
    act(() => {
      useToastStore.getState().addToast({ variant: 'success', title: 'Sync is on for Team wiki', duration: null });
      newer = useToastStore.getState().addToast({ variant: 'success', title: 'Sync is on for Docs wiki', duration: null });
    });
    act(() => {
      useToastStore.getState().removeToast(newer);
    });
    expect(announcer().textContent).toBe('Sync is on for Docs wiki');
  });

  it('still puts repeated words back when the stack re-renders during the gap', async () => {
    vi.useFakeTimers();
    try {
      render(<ToastContainer />);
      act(() => {
        useToastStore.getState().addToast({ variant: 'success', title: 'Saved', duration: null });
      });
      act(() => {
        useToastStore.getState().addToast({ variant: 'success', title: 'Saved', duration: null });
      });
      expect(announcer().textContent).toBe('');
      // Hovering the stack re-renders it without changing any toast.
      act(() => useToastStore.getState().setHovered(true));
      await act(async () => {
        await vi.runAllTimersAsync();
      });
      expect(announcer().textContent).toBe('Saved');
    } finally {
      vi.useRealTimers();
    }
  });
});

