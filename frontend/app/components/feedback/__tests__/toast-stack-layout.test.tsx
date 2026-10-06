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
});
