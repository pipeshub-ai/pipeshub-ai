/**
 * The shared option source behind the group and team member pickers.
 *
 * A service account is given something to read by being put in a group or a
 * team — the create panel tells an administrator to grant access that way — so
 * these pickers have to be able to offer one. Everywhere else the list of users
 * is the list of people, because a service account can never sign in and would
 * otherwise sit in the pending-invite set for good, so including them is
 * something a caller asks for rather than the default.
 */
import React from 'react';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const fetchMergedUsers = vi.fn();

vi.mock('../../users/api', () => ({
  UsersApi: {
    fetchMergedUsers: (...a: unknown[]) => fetchMergedUsers(...a),
  },
}));

const { usePaginatedUserOptions } = await import('../use-paginated-user-options');

const PEOPLE_AND_A_MACHINE = {
  users: [
    {
      id: 'u1',
      userId: 'u1',
      name: 'A Colleague',
      email: 'a.colleague@example.com',
      hasLoggedIn: true,
      isActive: true,
    },
    {
      id: 'u2',
      userId: 'u2',
      name: 'Nightly sync',
      email: 'svc-nightly-org@service.pipeshub.internal',
      hasLoggedIn: false,
      isActive: true,
      kind: 'service' as const,
    },
  ],
  totalCount: 2,
};

/**
 * Renders the hook and puts each option in the DOM, so the assertions read what
 * a picker would show rather than reaching into the hook's return value.
 */
function renderOptions(config: Record<string, unknown>) {
  function Probe() {
    const { options } = usePaginatedUserOptions(config as never);
    return (
      <ul>
        {options.map((option) => (
          <li key={option.id} data-testid={`option-${option.id}`}>
            <span data-testid={`label-${option.id}`}>{option.label}</span>
            <span data-testid={`badge-${option.id}`}>{option.badge ?? ''}</span>
          </li>
        ))}
      </ul>
    );
  }
  render(<Probe />);
}

afterEach(() => {
  // Unmounted by hand, as the other suites here do: without it each case adds
  // its own copy of the list and the queries below find several.
  cleanup();
  vi.clearAllMocks();
});

describe('offering service accounts in the member pickers', () => {
  it('does not ask for them unless told to', async () => {
    fetchMergedUsers.mockResolvedValue({ users: [], totalCount: 0 });

    renderOptions({ enabled: true });

    await waitFor(() => expect(fetchMergedUsers).toHaveBeenCalled());
    const params = fetchMergedUsers.mock.calls[0][0] as Record<string, unknown>;
    expect(params.includeServiceAccounts).toBe(undefined);
  });

  it('asks for them when the picker opts in', async () => {
    fetchMergedUsers.mockResolvedValue({ users: [], totalCount: 0 });

    renderOptions({
      enabled: true,
      includeServiceAccounts: true,
      serviceAccountBadge: 'Service account',
    });

    await waitFor(() => expect(fetchMergedUsers).toHaveBeenCalled());
    const params = fetchMergedUsers.mock.calls[0][0] as Record<string, unknown>;
    expect(params.includeServiceAccounts).toBe('true');
  });

  it('marks the service account and leaves the colleague unmarked', async () => {
    // The point of the badge: in a list that is mostly colleagues, a machine
    // identity should not pass for one.
    fetchMergedUsers.mockResolvedValue(PEOPLE_AND_A_MACHINE);

    renderOptions({
      enabled: true,
      includeServiceAccounts: true,
      serviceAccountBadge: 'Service account',
    });

    await waitFor(() => expect(screen.getByTestId('option-u2')).toBeTruthy());
    expect(screen.getByTestId('badge-u1').textContent).toBe('');
    expect(screen.getByTestId('label-u2').textContent).toBe('Nightly sync');
    expect(screen.getByTestId('badge-u2').textContent).toBe('Service account');
  });

  it('leaves the badge off when no word was given for it', async () => {
    fetchMergedUsers.mockResolvedValue(PEOPLE_AND_A_MACHINE);

    renderOptions({ enabled: true, includeServiceAccounts: true });

    await waitFor(() => expect(screen.getByTestId('option-u2')).toBeTruthy());
    expect(screen.getByTestId('badge-u2').textContent).toBe('');
  });
});
