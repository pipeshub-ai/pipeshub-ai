import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

/**
 * Disabling a service account stops every token it already holds, and
 * revoking a token cannot be undone. Both are one click away from a live
 * integration breaking, so neither may fire straight off the icon button.
 */

vi.mock('next/navigation', () => ({
  usePathname: () => '/workspace/service-accounts',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/store/user-store', () => {
  const state = { profile: { isAdmin: true } };
  return {
    useUserStore: (selector: (s: typeof state) => unknown) => selector(state),
    selectIsProfileInitialized: () => true,
    selectIsAdmin: () => true,
  };
});

// Both of these have to be stable across renders. The page puts `t` and
// `addToast` in the dependencies of the callback its effect runs, so a fresh
// identity on every render would re-fire the fetch forever.
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
      ? cur.replace(/\{\{(\w+)\}\}/g, (_m, name: string) => String(vars[name] ?? ''))
      : cur;
  };
  const value = { i18n: { language: 'en-US' }, t };
  return { useTranslation: () => value };
});

const listAccounts = vi.fn();
const updateAccount = vi.fn();
const removeAccount = vi.fn();
const listTokens = vi.fn();
const getScopes = vi.fn();
const revokeToken = vi.fn();

vi.mock('../api', () => ({
  ServiceAccountsApi: {
    list: (...a: unknown[]) => listAccounts(...a),
    update: (...a: unknown[]) => updateAccount(...a),
    remove: (...a: unknown[]) => removeAccount(...a),
    create: vi.fn(),
  },
  ServiceTokensApi: {
    list: (...a: unknown[]) => listTokens(...a),
    getScopes: (...a: unknown[]) => getScopes(...a),
    revoke: (...a: unknown[]) => revokeToken(...a),
    create: vi.fn(),
  },
}));

import ServiceAccountsPage from '../page';
import { ServiceAccountTokensPanel } from '../components/service-account-tokens-panel';

const ACCOUNT = {
  id: '507f1f77bcf86cd799439012',
  slug: 'nightly-sync',
  fullName: 'Nightly sync',
  email: 'svc-nightly-sync-org@service.pipeshub.internal',
  isDisabled: false,
};

beforeEach(() => {
  listAccounts.mockResolvedValue({ serviceAccounts: [ACCOUNT] });
  updateAccount.mockResolvedValue(ACCOUNT);
  removeAccount.mockResolvedValue(undefined);
  listTokens.mockResolvedValue({
    tokens: [
      {
        id: 'token-1',
        name: 'Release digest job',
        serviceAccountId: ACCOUNT.id,
        scopes: ['kb:read'],
        createdAt: new Date().toISOString(),
        expiresAt: new Date(Date.now() + 90 * 86400000).toISOString(),
      },
    ],
  });
  getScopes.mockResolvedValue({ scopes: ['kb:read'] });
  revokeToken.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('disabling a service account', () => {
  it('asks before it stops every token the account holds', async () => {
    render(
      <Theme>
        <ServiceAccountsPage />
      </Theme>
    );

    const disableButton = await screen.findByLabelText(
      en.workspace.serviceAccounts.disable
    );
    fireEvent.click(disableButton);

    // The dialog is up and nothing has been sent.
    expect(
      await screen.findByText(en.workspace.serviceAccounts.disableTitle)
    ).toBeTruthy();
    expect(updateAccount).not.toHaveBeenCalled();
  });

  it('sends the change only once it is confirmed', async () => {
    render(
      <Theme>
        <ServiceAccountsPage />
      </Theme>
    );

    fireEvent.click(await screen.findByLabelText(en.workspace.serviceAccounts.disable));
    fireEvent.click(
      await screen.findByRole('button', {
        name: en.workspace.serviceAccounts.disableConfirm,
      })
    );

    await waitFor(() => expect(updateAccount).toHaveBeenCalledTimes(1));
    expect(updateAccount.mock.calls[0][1]).toEqual({ isDisabled: true });
  });
});

describe('revoking a service token', () => {
  it('asks before a live credential is destroyed', async () => {
    render(
      <Theme>
        <ServiceAccountTokensPanel open onOpenChange={() => {}} account={ACCOUNT} />
      </Theme>
    );

    fireEvent.click(await screen.findByLabelText(en.workspace.serviceAccounts.tokens.revoke));

    expect(
      await screen.findByText(en.workspace.serviceAccounts.tokens.revokeTitle)
    ).toBeTruthy();
    expect(revokeToken).not.toHaveBeenCalled();
  });

  it('revokes only once it is confirmed', async () => {
    render(
      <Theme>
        <ServiceAccountTokensPanel open onOpenChange={() => {}} account={ACCOUNT} />
      </Theme>
    );

    fireEvent.click(await screen.findByLabelText(en.workspace.serviceAccounts.tokens.revoke));
    fireEvent.click(
      await screen.findByRole('button', {
        name: en.workspace.serviceAccounts.tokens.revoke,
      })
    );

    await waitFor(() => expect(revokeToken).toHaveBeenCalledTimes(1));
    expect(revokeToken).toHaveBeenCalledWith('token-1', ACCOUNT.id);
  });
});
