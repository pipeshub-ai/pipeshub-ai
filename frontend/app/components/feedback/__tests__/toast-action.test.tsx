import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import { Toast } from '../toast';
import type { Toast as ToastModel } from '@/lib/store/toast-store';

afterEach(cleanup);

function renderToast(action: NonNullable<ToastModel['action']>) {
  const toast: ToastModel = {
    id: 't1',
    variant: 'info',
    title: 'Saved',
    action,
    createdAt: 0,
  };
  return render(
    <Theme>
      <Toast toast={toast} onDismiss={vi.fn()} />
    </Theme>,
  );
}

describe('Toast action button', () => {
  it('draws its border with the theme token so it stays visible in dark mode', () => {
    renderToast({ label: 'Undo', onClick: vi.fn() });
    const button = screen.getByRole('button', { name: /undo/i });
    expect(button.style.border).toBe('1px solid var(--slate-a7)');
    expect(button.getAttribute('style')).not.toMatch(/rgba\(0,\s*6,\s*46/);
  });
});
