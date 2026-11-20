import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { ShareSearchInput } from '../share-search-input';
import type { ShareSelection } from '../types';

afterEach(cleanup);

const PICKED: ShareSelection[] = [{ id: 'u1', type: 'user', name: 'Walter Writer', email: 'w@x.com' }];

function renderInput(selections: ShareSelection[], supportsTeams: boolean) {
  render(
    <Theme>
      <ShareSearchInput
        selections={selections}
        searchQuery=""
        selectedRole="WRITER"
        supportsRoles={false}
        supportsTeams={supportsTeams}
        onSearchChange={() => {}}
        onRemoveSelection={() => {}}
        onRoleChange={() => {}}
      />
    </Theme>,
  );
}

/** The search field's name is its placeholder's words; the placeholder goes once someone is picked, the name stays. */
describe('share search field name', () => {
  it.each([
    [true, 'Emails, teams or names (separated by commas)'],
    [false, 'Emails or names (separated by commas)'],
  ])('teams %s: named before and after a pick', (supportsTeams, name) => {
    renderInput([], supportsTeams);
    expect(screen.getByRole('textbox', { name })).toBeTruthy();
    cleanup();
    renderInput(PICKED, supportsTeams);
    expect(screen.getByRole('textbox', { name })).toBeTruthy();
  });
});
