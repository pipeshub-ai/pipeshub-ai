/**
 * SEC-AZ-08 (DEACT-UI-1..4): an admin deactivates or reactivates a member from the Users table. The dialog says
 * in plain words what each does, the calls go to the member's own routes, and a deactivated account shows as
 * Deactivated.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { apiClient } from '@/lib/api';
import { DeactivateUserDialog, userStatus } from '../components/deactivate-user-dialog';
import { UsersApi } from '../api';
import type { User } from '../types';

const MAX = { userId: 'u-max', name: 'Max Muster', email: 'max@example.com' } as User;

const renderDialog = (action: 'deactivate' | 'reactivate', onConfirm = vi.fn()) =>
  render(
    <Theme>
      <DeactivateUserDialog target={MAX} action={action} isLoading={false} onClose={() => {}} onConfirm={onConfirm} />
    </Theme>,
  );

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('Deactivate and reactivate a member', () => {
  it('DEACT-UI-1: deactivating says their work stops, schedules pause and tokens are revoked', () => {
    const onConfirm = vi.fn();
    renderDialog('deactivate', onConfirm);
    expect(screen.getByText('Deactivate Max Muster?')).toBeTruthy();
    expect(screen.getByText(/any work an agent is doing for them stops now/)).toBeTruthy();
    expect(screen.getByText('Their schedules and triggers are paused.')).toBeTruthy();
    expect(screen.getByText(/access tokens are revoked/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Deactivate' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('DEACT-UI-2: reactivating says nothing restarts and schedules stay paused', () => {
    renderDialog('reactivate');
    expect(screen.getByText('Reactivate Max Muster?')).toBeTruthy();
    expect(screen.getByText(/Nothing that was stopped restarts: their schedules stay paused/)).toBeTruthy();
    expect(screen.getByText('They need to create new access tokens.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Reactivate' })).toBeTruthy();
  });

  it('DEACT-UI-3: calls the member routes by id; the org comes from the session', async () => {
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue({ data: { cancelledRuns: 1, pausedSchedules: 2 } } as never);
    expect(await UsersApi.deactivateUser('u-max')).toEqual({ cancelledRuns: 1, pausedSchedules: 2 });
    await UsersApi.reactivateUser('u-max');
    expect(put.mock.calls.map((call) => call[0])).toEqual([
      '/api/v1/users/u-max/deactivate',
      '/api/v1/users/u-max/reactivate',
    ]);
    expect(put.mock.calls.every((call) => call.length === 1)).toBe(true);
  });

  it('DEACT-UI-4: a deactivated account shows as Deactivated, before blocked, active or pending', () => {
    expect(userStatus({ isDisabled: true, isBlocked: true, hasLoggedIn: true })).toBe('Deactivated');
    expect(userStatus({ isBlocked: true, hasLoggedIn: true })).toBe('Blocked');
    expect(userStatus({ hasLoggedIn: true })).toBe('Active');
    expect(userStatus({ hasLoggedIn: false })).toBe('Pending');
  });
});
