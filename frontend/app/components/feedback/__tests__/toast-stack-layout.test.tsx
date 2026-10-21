import React from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';

import { ToastContainer } from '../toast-container';
import { useToastStore, toast } from '@/lib/store/toast-store';

vi.mock('@/app/components/theme-provider', () => ({ useThemeAppearance: () => ({ appearance: 'light' }) }));

beforeEach(() => {
  window.matchMedia = vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() });
});
afterEach(() => {
  cleanup();
  act(() => useToastStore.setState({ toasts: [] }));
});

describe('toast stack layout', () => {
  it('keeps a toast flush right in its row and lets clicks through the empty part of the row', async () => {
    render(<ToastContainer />);
    act(() => {
      toast.info('Stopping…');
    });
    const title = await screen.findByText('Stopping…');
    const region = document.querySelector('[data-ph-toast-region]') as HTMLElement;
    const row = region.firstElementChild as HTMLElement;
    expect(row.style.justifyContent).toBe('flex-end');
    expect(row.style.pointerEvents).toBe('none');
    const card = row.firstElementChild as HTMLElement;
    expect(card.contains(title)).toBe(true);
    expect(card.style.pointerEvents).toBe('auto');
  });

  it('sits 24 px from the bottom and right edges above the phone width, and 16 px on a phone, at the top', async () => {
    render(<ToastContainer />);
    act(() => {
      toast.info('Saved');
    });
    await screen.findByText('Saved');
    const region = () => document.querySelector('[data-ph-toast-region]') as HTMLElement;
    expect(region().dataset.phToastPlacement).toBe('bottom');
    expect(region().style.bottom).toContain('max(24px');
    expect(region().style.right).toContain('max(24px');
    cleanup();
    act(() => useToastStore.setState({ toasts: [] }));

    window.matchMedia = vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() });
    render(<ToastContainer />);
    act(() => {
      toast.info('Saved');
    });
    await screen.findByText('Saved');
    expect(region().dataset.phToastPlacement).toBe('top');
    expect(region().style.top).toContain('max(16px');
    expect(region().style.right).toContain('max(16px');
  });

  it('a toast drawn outside the stack lifts only the bottom stack, by the room it publishes; unset, nothing moves', async () => {
    render(<ToastContainer />);
    act(() => {
      toast.info('Below');
      toast.info('Above', { placement: 'top' });
    });
    await screen.findByText('Below');
    const stack = (placement: string) => document.querySelector(`[data-ph-toast-placement="${placement}"]`) as HTMLElement;
    // Unset, its fallback adds nothing: the bottom stack is where it was.
    expect(stack('bottom').style.bottom).toBe(
      'calc(max(24px, env(safe-area-inset-bottom, 0px)) + var(--ph-toast-stacked, 0px))',
    );
    expect(stack('top').style.top).not.toContain('--ph-toast-stacked');
  });
});
