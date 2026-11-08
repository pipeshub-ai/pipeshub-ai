import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { EntityPagination } from '../entity-pagination';

afterEach(() => cleanup());

function setup(totalCount: number) {
  render(
    <Theme>
      <EntityPagination page={1} limit={25} totalCount={totalCount} onPageChange={() => {}} />
    </Theme>,
  );
}

describe('EntityPagination', () => {
  it('counts many items in the plural', () => {
    setup(26);
    expect(screen.getByText('Showing 1-25 of 26 items')).toBeTruthy();
  });

  it('counts one item in the singular', () => {
    setup(1);
    expect(screen.getByText('Showing 1-1 of 1 item')).toBeTruthy();
  });

  it('shows no counter when there are no rows', () => {
    setup(0);
    expect(screen.queryByText(/Showing/)).toBeNull();
  });
});
