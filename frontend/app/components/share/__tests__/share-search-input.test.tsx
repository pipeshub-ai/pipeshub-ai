import React from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { ShareSearchInput } from '../share-search-input';
import type { ShareRoleOption, ShareSelection } from '../types';

const chips: ShareSelection[] = [
  { type: 'user', id: 'u1', name: 'a', email: 'first.person@example.com' },
  { type: 'user', id: 'u2', name: 'b', email: 'second.person@example.com' },
];
const roleOptions: ShareRoleOption[] = [{ role: 'READER', label: 'Can view', description: '' }];

function renderInput(options?: ShareRoleOption[], selections: ShareSelection[] = chips) {
  render(
    <Theme>
      <ShareSearchInput
        selections={selections}
        searchQuery=""
        selectedRole="READER"
        supportsRoles={false}
        onSearchChange={() => {}}
        onRemoveSelection={() => {}}
        onRoleChange={() => {}}
        roleOptions={options}
      />
    </Theme>,
  );
  return screen.getByTestId('share-chips');
}

beforeEach(() => vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} }));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('ShareSearchInput chips', () => {
  it('wraps chips when the adapter passes roleOptions (chat drawer)', () => {
    const row = renderInput(roleOptions);
    expect(row.style.flexWrap).toBe('wrap');
    expect(row.style.overflowX).toBe('visible');
    expect(row.parentElement!.style.flexWrap).toBe('wrap');
  });

  it('keeps the single scrolling row for drawers without roleOptions (KB, teams)', () => {
    const row = renderInput(undefined);
    expect(row.style.flexWrap).toBe('nowrap');
    expect(row.style.overflowX).toBe('auto');
  });

  it('shows all 51 chips of an over-the-cap selection when wrapped, each removable, so the user can trim to 50', () => {
    const many: ShareSelection[] = Array.from({ length: 51 }, (_, i) => ({ type: 'user', id: `u${i}`, name: `n${i}`, email: `p${i}@example.com` }));
    const row = renderInput(roleOptions, many);
    expect(row.querySelectorAll('button[aria-label]').length).toBe(51);
    expect(row.style.flexWrap).toBe('wrap');
  });
});
