import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';

const isMobile = vi.hoisted(() => ({ value: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => isMobile.value }));
vi.mock('@/app/components/theme-provider', () => ({ useThemeAppearance: () => ({ appearance: 'light' }) }));
vi.mock('../toast', () => ({ Toast: ({ toast }: { toast: { title: string } }) => <span>{toast.title}</span> }));

import { ToastContainer } from '../toast-container';
import { useToastStore } from '@/lib/store/toast-store';

function regionOf(title: string): string | null | undefined {
  return Array.from(document.querySelectorAll('[data-ph-toast-region]'))
    .find((el) => el.textContent?.includes(title))
    ?.getAttribute('data-ph-toast-placement');
}

afterEach(() => {
  cleanup();
  useToastStore.setState({ toasts: [] });
});

describe('toast placement', () => {
  it('keeps a bottom toast at the bottom on a wide screen', () => {
    isMobile.value = false;
    useToastStore.getState().addToast({ variant: 'info', title: 'Saved' });
    render(<ToastContainer />);
    expect(regionOf('Saved')).toBe('bottom');
  });

  it('moves a bottom toast to the top on a phone, clear of footer actions', () => {
    isMobile.value = true;
    useToastStore.getState().addToast({ variant: 'info', title: 'Saved' });
    render(<ToastContainer />);
    expect(regionOf('Saved')).toBe('top');
  });
});
