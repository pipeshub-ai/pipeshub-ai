/**
 * The delegation panel: who a service account may act as, and the record of
 * every time it tried.
 *
 * The screen has to tell an administrator the truth about what the system
 * will do. Two things in particular: an entry whose person can no longer be
 * acted as must still be visible and marked, rather than quietly dropped; and
 * refused attempts must be as readable as successful ones, because a run of
 * refusals is what a stolen credential looks like.
 */
import React from 'react';
import { Theme } from '@radix-ui/themes';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import en from '@/lib/i18n/locales/en-US.json';

// jsdom has no matchMedia; useIsMobile needs it to resolve the touch-target size.
Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }),
});

vi.mock('@/lib/store/toast-store', () => {
  const addToast = vi.fn();
  const state = { addToast };
  return {
    useToastStore: (selector: (s: typeof state) => unknown) => selector(state),
  };
});

vi.mock('react-i18next', () => {
  const t = (key: string, vars?: Record<string, unknown>) => {
    const parts = key.split('.');
    let cur: unknown = en;
    for (const part of parts) {
      if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
      cur = (cur as Record<string, unknown>)[part];
    }
    if (typeof cur !== 'string') return key;
    return vars
      ? cur.replace(/\{\{(\w+)\}\}/g, (_m, name: string) =>
          String(vars[name] ?? '')
        )
      : cur;
  };
  const value = { i18n: { language: 'en-US' }, t };
  return { useTranslation: () => value };
});

const listDelegations = vi.fn();
const listEvents = vi.fn();
const grantDelegation = vi.fn();
const revokeDelegation = vi.fn();

vi.mock('../api', () => ({
  DelegationsApi: {
    list: (...a: unknown[]) => listDelegations(...a),
    events: (...a: unknown[]) => listEvents(...a),
    grant: (...a: unknown[]) => grantDelegation(...a),
    revoke: (...a: unknown[]) => revokeDelegation(...a),
    capability: () => Promise.resolve({ available: true }),
  },
}));

const { ServiceAccountDelegationsPanel } = await import(
  '../components/service-account-delegations-panel'
);

const ACCOUNT = {
  id: 'account-1',
  slug: 'nightly',
  fullName: 'Nightly sync',
  email: 'svc-nightly-org@service.pipeshub.internal',
  isDisabled: false,
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
};

const copy = en.workspace.serviceAccounts.delegations;

function renderPanel() {
  return render(
    <Theme>
      <ServiceAccountDelegationsPanel
        open
        account={ACCOUNT}
        onOpenChange={vi.fn()}
      />
    </Theme>
  );
}

describe('ServiceAccountDelegationsPanel', () => {
  beforeEach(() => {
    listDelegations.mockReset().mockResolvedValue({ delegations: [] });
    listEvents.mockReset().mockResolvedValue({ events: [] });
    grantDelegation.mockReset().mockResolvedValue({});
    revokeDelegation.mockReset().mockResolvedValue(undefined);
  });

  afterEach(cleanup);

  it('says plainly when the account cannot act as anyone', async () => {
    renderPanel();
    await waitFor(() => expect(listDelegations).toHaveBeenCalled());
    expect(screen.getByText(copy.none)).toBeTruthy();
  });

  it('shows an entry whose person can no longer be acted as, and says why', async () => {
    listDelegations.mockResolvedValue({
      delegations: [
        {
          id: 'd1',
          serviceAccountId: 'account-1',
          subjectUserId: 'u1',
          subjectEmail: 'boss@example.com',
          subjectFullName: 'The Boss',
          inactive: true,
          inactiveReason: 'This person is now an administrator',
          grantedBy: 'admin-1',
          createdAt: new Date().toISOString(),
        },
      ],
    });

    renderPanel();

    await waitFor(() => expect(screen.getByText('The Boss')).toBeTruthy());
    // Still listed, not hidden — the exchange refuses it, and the screen must
    // not disagree with the system.
    expect(screen.getByText('boss@example.com')).toBeTruthy();
    expect(screen.getByText(copy.inactive)).toBeTruthy();
    expect(
      screen.getByText('This person is now an administrator')
    ).toBeTruthy();
  });

  it('shows refused attempts alongside successful ones', async () => {
    listEvents.mockResolvedValue({
      events: [
        {
          id: 'e1',
          serviceAccountId: 'account-1',
          requestedSubject: 'boss@example.com',
          outcome: 'refused',
          refusedReason: 'subject_is_admin',
          scopes: [],
          createdAt: new Date().toISOString(),
        },
        {
          id: 'e2',
          serviceAccountId: 'account-1',
          subjectEmail: 'person@example.com',
          requestedSubject: 'person@example.com',
          outcome: 'issued',
          scopes: ['kb:read'],
          createdAt: new Date().toISOString(),
        },
      ],
    });

    renderPanel();

    await waitFor(() => expect(screen.getByText(copy.outcomeRefused)).toBeTruthy());
    expect(screen.getByText(copy.outcomeIssued)).toBeTruthy();
    // The cause of a refusal is on screen, because that is the signal worth
    // noticing.
    expect(screen.getByText('subject_is_admin')).toBeTruthy();
  });

  it('never shows one account’s people under another account’s name', async () => {
    // Two accounts can be in flight at once. If the first account's list is
    // still on screen when the second opens, clicking a row sends the second
    // account's id with the first account's person, and revokes a delegation
    // nobody asked about.
    listDelegations.mockResolvedValueOnce({
      delegations: [
        {
          id: 'd1',
          serviceAccountId: 'account-1',
          subjectUserId: 'u1',
          subjectEmail: 'person@example.com',
          subjectFullName: 'A Person',
          inactive: false,
          grantedBy: 'admin-1',
          createdAt: new Date().toISOString(),
        },
      ],
    });

    const { rerender } = render(
      <Theme>
        <ServiceAccountDelegationsPanel
          open
          account={ACCOUNT}
          onOpenChange={vi.fn()}
        />
      </Theme>
    );
    await waitFor(() => expect(screen.getByText('A Person')).toBeTruthy());

    // The second account's list never resolves, so anything still on screen
    // is left over from the first.
    listDelegations.mockImplementation(() => new Promise(() => {}));
    listEvents.mockImplementation(() => new Promise(() => {}));
    rerender(
      <Theme>
        <ServiceAccountDelegationsPanel
          open
          account={{ ...ACCOUNT, id: 'account-2', fullName: 'Other sync' }}
          onOpenChange={vi.fn()}
        />
      </Theme>
    );

    await waitFor(() => expect(screen.queryByText('A Person')).toBeNull());
  });

  it('still shows the allow-list when the activity log fails to load', async () => {
    // The list is what an administrator revokes from. A failing log should
    // not take it off the screen.
    listDelegations.mockResolvedValue({
      delegations: [
        {
          id: 'd1',
          serviceAccountId: 'account-1',
          subjectUserId: 'u1',
          subjectEmail: 'person@example.com',
          subjectFullName: 'A Person',
          inactive: false,
          grantedBy: 'admin-1',
          createdAt: new Date().toISOString(),
        },
      ],
    });
    listEvents.mockRejectedValue(new Error('log unavailable'));

    renderPanel();

    await waitFor(() => expect(screen.getByText('A Person')).toBeTruthy());
  });

  it('ignores a slower load that would undo a grant just made', async () => {
    // Granting refreshes while the first load may still be pending. If the
    // older one lands last it replaces the list with a snapshot taken before
    // the grant, and the new entry vanishes.
    let resolveFirst: ((v: unknown) => void) | undefined;
    listDelegations.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        })
    );
    listDelegations.mockResolvedValue({
      delegations: [
        {
          id: 'd1',
          serviceAccountId: 'account-1',
          subjectUserId: 'u1',
          subjectEmail: 'person@example.com',
          subjectFullName: 'Newly Granted',
          inactive: false,
          grantedBy: 'admin-1',
          createdAt: new Date().toISOString(),
        },
      ],
    });

    renderPanel();
    await waitFor(() => expect(listDelegations).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByPlaceholderText(copy.subjectPlaceholder), {
      target: { value: 'person@example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: copy.addConfirm }));
    await waitFor(() => expect(screen.getByText('Newly Granted')).toBeTruthy());

    // Now let the stale first load finish, with nothing in it.
    resolveFirst?.({ delegations: [] });

    await waitFor(() => expect(screen.getByText('Newly Granted')).toBeTruthy());
    expect(screen.queryByText(copy.none)).toBeNull();
  });

  it('will not grant until a person has been named', async () => {
    renderPanel();
    await waitFor(() => expect(listDelegations).toHaveBeenCalled());

    const button = screen.getByRole('button', { name: copy.addConfirm });
    expect((button as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByPlaceholderText(copy.subjectPlaceholder), {
      target: { value: 'person@example.com' },
    });
    await waitFor(() =>
      expect((button as HTMLButtonElement).disabled).toBe(false)
    );
  });

  it('sends the person and the reason, and reloads afterwards', async () => {
    renderPanel();
    await waitFor(() => expect(listDelegations).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByPlaceholderText(copy.subjectPlaceholder), {
      target: { value: '  person@example.com  ' },
    });
    fireEvent.change(screen.getByPlaceholderText(copy.reasonPlaceholder), {
      target: { value: 'nightly digest' },
    });
    fireEvent.click(screen.getByRole('button', { name: copy.addConfirm }));

    await waitFor(() => expect(grantDelegation).toHaveBeenCalled());
    expect(grantDelegation.mock.calls[0][0]).toEqual({
      serviceAccountId: 'account-1',
      subject: 'person@example.com',
      reason: 'nightly digest',
    });
    await waitFor(() => expect(listDelegations).toHaveBeenCalledTimes(2));
  });

  it('asks before removing a permission, and names who it is for', async () => {
    listDelegations.mockResolvedValue({
      delegations: [
        {
          id: 'd1',
          serviceAccountId: 'account-1',
          subjectUserId: 'u1',
          subjectEmail: 'person@example.com',
          subjectFullName: 'A Person',
          inactive: false,
          grantedBy: 'admin-1',
          createdAt: new Date().toISOString(),
        },
      ],
    });

    renderPanel();
    await waitFor(() => expect(screen.getByText('A Person')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: copy.revoke }));

    await waitFor(() => expect(screen.getByText(copy.revokeTitle)).toBeTruthy());
    expect(revokeDelegation).not.toHaveBeenCalled();
    expect(
      screen.getByText(
        copy.revokeDescription.replace('{{subject}}', 'person@example.com')
      )
    ).toBeTruthy();
  });
  it('explains instead of loading where the capability is not in the build', async () => {
    render(
      <Theme>
        <ServiceAccountDelegationsPanel
          open
          available={false}
          account={ACCOUNT}
          onOpenChange={vi.fn()}
        />
      </Theme>
    );

    expect(screen.getByText(copy.unavailableTitle)).toBeTruthy();
    expect(screen.getByText(copy.unavailableBody)).toBeTruthy();

    // The point of the explanatory state is that nothing is asked for. A
    // request here would come back 501 and surface as an error toast, which
    // is the "looks broken" outcome this replaces.
    expect(listDelegations).not.toHaveBeenCalled();
    expect(listEvents).not.toHaveBeenCalled();

    // The account is still named, so it is clear which one this is about.
    expect(screen.getByText(ACCOUNT.email)).toBeTruthy();
  });
});
