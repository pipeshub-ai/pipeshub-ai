import React from 'react';
import { Theme } from '@radix-ui/themes';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { OAuthApplicationCard } from '../oauth-application-card';
import type { OAuthClient } from '../../types';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const app: OAuthClient = {
  id: 'a1',
  clientId: 'cid',
  name: 'Payroll',
  allowedScopes: ['kb:read'],
  createdBy: '507f1f77bcf86cd799439013',
};

function renderCard(client: OAuthClient) {
  return render(
    <Theme>
      <OAuthApplicationCard client={client} onManage={() => {}} />
    </Theme>
  );
}

describe('OAuthApplicationCard', () => {
  afterEach(() => {
    cleanup();
  });

  it('names who created the app when the list says so (an admin sees every app)', () => {
    renderCard({ ...app, createdByName: 'Mallory', createdByEmail: 'mallory@example.com' });
    expect(screen.getByText('Created by Mallory')).toBeTruthy();
  });

  it('falls back to the creator email when there is no name', () => {
    renderCard({ ...app, createdByEmail: 'mallory@example.com' });
    expect(screen.getByText('Created by mallory@example.com')).toBeTruthy();
  });

  it('shows no creator on a member list, which carries none', () => {
    renderCard(app);
    expect(screen.queryByText(/^Created by/)).toBeNull();
  });
});
