import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { KbPaginationFooter } from '../kb-pagination';

afterEach(() => cleanup());

const base = { limit: 50, totalItems: 120, hasNext: true, hasPrev: true, nextCursor: 'n', prevCursor: 'p' };

function setup(p: Parameters<typeof KbPaginationFooter>[0]['pagination']) {
  const onCursorChange = vi.fn();
  render(
    <Theme>
      <KbPaginationFooter pagination={p} onCursorChange={onCursorChange} />
    </Theme>,
  );
  return onCursorChange;
}

describe('KbPaginationFooter', () => {
  it('Collections: Prev stays enabled when startIndex is not page-aligned and the range comes from start/end', () => {
    const onCursor = setup({ ...base, startIndex: 31, endIndex: 80 });
    expect(screen.getByText('Showing 31-80 of 120')).toBeTruthy();
    const prev = screen.getByRole('button', { name: /Previous/ });
    expect((prev as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(prev);
    expect(onCursor).toHaveBeenCalledWith('p', 'prev');
  });

  it('All Records: Prev follows hasPrev even with the fallback startIndex of 1; Next is disabled at the end', () => {
    setup({ ...base, startIndex: 1, endIndex: 7, hasNext: false, totalItems: 7 });
    expect((screen.getByRole('button', { name: /Previous/ }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole('button', { name: /Next/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('Showing 1-7 of 7')).toBeTruthy();
  });
});
