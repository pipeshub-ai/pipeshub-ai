import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

/**
 * A token's lifetime can be any whole number of days the server accepts, not
 * only the presets, so it can match how long the integration really needs it.
 */

vi.mock('@/lib/store/toast-store', () => {
  const addToast = vi.fn();
  const state = { addToast };
  return {
    useToastStore: (selector: (s: typeof state) => unknown) => selector(state),
  };
});

vi.mock('react-i18next', () => {
  const t = (key: string, vars?: Record<string, unknown>) => {
    let cur: unknown = en;
    for (const part of key.split('.')) {
      if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
      cur = (cur as Record<string, unknown>)[part];
    }
    if (typeof cur !== 'string') return key;
    return vars
      ? cur.replace(/\{\{(\w+)\}\}/g, (_m, name: string) => String(vars[name] ?? ''))
      : cur;
  };
  const value = { i18n: { language: 'en-US' }, t };
  return {
    initReactI18next: { type: '3rdParty', init: () => undefined },
    useTranslation: () => value,
  };
});

const createToken = vi.fn();
const listTokens = vi.fn();

vi.mock('../api', () => ({
  ServiceTokensApi: {
    list: (...a: unknown[]) => listTokens(...a),
    getScopes: () =>
      Promise.resolve({
        scopes: [
          {
            name: 'kb:read',
            description: 'Read knowledge bases and records',
            category: 'Knowledge Base',
            requiresUserConsent: true,
          },
        ],
      }),
    revoke: vi.fn(),
    create: (...a: unknown[]) => createToken(...a),
  },
}));

import { ServiceAccountTokensPanel } from '../components/service-account-tokens-panel';

const tokens = en.workspace.serviceAccounts.tokens;

const ACCOUNT = {
  id: '507f1f77bcf86cd799439012',
  slug: 'nightly-sync',
  fullName: 'Nightly sync',
  email: 'svc-nightly-sync-org@service.pipeshub.internal',
  isDisabled: false,
};

async function openMintForm() {
  render(
    <Theme>
      <ServiceAccountTokensPanel open onOpenChange={() => {}} account={ACCOUNT} />
    </Theme>
  );
  fireEvent.click(await screen.findByRole('button', { name: new RegExp(tokens.newToken) }));
  fireEvent.change(screen.getByPlaceholderText(tokens.namePlaceholder), {
    target: { value: 'Release digest job' },
  });
  fireEvent.click(await screen.findByRole('checkbox'));
}

function chooseCustom(days: string) {
  fireEvent.click(screen.getByRole('button', { name: tokens.expiryCustom }));
  fireEvent.change(screen.getByLabelText(tokens.expiryCustomLabel), {
    target: { value: days },
  });
}

const mintButton = () => screen.getByRole('button', { name: tokens.mint });

beforeEach(() => {
  listTokens.mockResolvedValue({ tokens: [] });
  createToken.mockResolvedValue({ token: { accessToken: 'phsvc_test' } });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('custom token expiry', () => {
  it('sends the number of days that was typed', async () => {
    await openMintForm();
    chooseCustom('45');

    fireEvent.click(mintButton());

    await waitFor(() => expect(createToken).toHaveBeenCalledTimes(1));
    expect(createToken.mock.calls[0][0]).toMatchObject({ expiryDays: 45 });
  });

  it('shows the date the token will expire on', async () => {
    await openMintForm();
    chooseCustom('10');

    const expected = new Date(Date.now() + 10 * 24 * 60 * 60 * 1000).toLocaleDateString(
      'en-US',
      { year: 'numeric', month: 'short', day: 'numeric' }
    );
    expect(screen.getByText(`Expires on ${expected}`)).toBeTruthy();
  });

  it.each(['0', '366', '2.5', '-3'])(
    'refuses %s days, which the server would reject',
    async (days) => {
      await openMintForm();
      chooseCustom(days);

      expect(screen.getByRole('alert').textContent).toBe(
        tokens.expiryCustomInvalid.replace('{{max}}', '365')
      );
      expect((mintButton() as HTMLButtonElement).disabled).toBe(true);
    }
  );

  it('points the field at its error, so a screen reader can find it again', async () => {
    await openMintForm();
    chooseCustom('400');

    const input = screen.getByLabelText(tokens.expiryCustomLabel);
    const alert = screen.getByRole('alert');
    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect(alert.id).not.toBe('');
    expect(input.getAttribute('aria-describedby')).toBe(alert.id);
  });

  it('waits for a number before a custom token can be created', async () => {
    await openMintForm();
    fireEvent.click(screen.getByRole('button', { name: tokens.expiryCustom }));

    expect((mintButton() as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('accepts the longest lifetime the server allows', async () => {
    await openMintForm();
    chooseCustom('365');

    fireEvent.click(mintButton());

    await waitFor(() => expect(createToken).toHaveBeenCalledTimes(1));
    expect(createToken.mock.calls[0][0]).toMatchObject({ expiryDays: 365 });
  });

  it('still sends a preset when one is picked after typing a custom value', async () => {
    await openMintForm();
    chooseCustom('45');
    fireEvent.click(
      screen.getByRole('button', { name: tokens.expiryDays.replace('{{count}}', '30') })
    );

    fireEvent.click(mintButton());

    await waitFor(() => expect(createToken).toHaveBeenCalledTimes(1));
    expect(createToken.mock.calls[0][0]).toMatchObject({ expiryDays: 30 });
  });

  it('keeps 90 days as the default', async () => {
    await openMintForm();

    fireEvent.click(mintButton());

    await waitFor(() => expect(createToken).toHaveBeenCalledTimes(1));
    expect(createToken.mock.calls[0][0]).toMatchObject({ expiryDays: 90 });
  });
});

describe('days left on a token', () => {
  const HOUR = 60 * 60 * 1000;
  const token = (name: string, msLeft: number) => ({
    id: name,
    name,
    serviceAccountId: ACCOUNT.id,
    scopes: ['kb:read'],
    createdAt: new Date().toISOString(),
    expiresAt: new Date(Date.now() + msLeft).toISOString(),
  });

  it.each([
    // A token minted for 7 days a minute ago still has 7 days, not 6.
    ['minted for a week', 7 * 24 * HOUR - 60_000, 'Days left: 7'],
    // Hours left is not none left.
    ['with hours left', 3 * HOUR, 'Days left: 1'],
    ['past its expiry', -HOUR, tokens.expired],
  ])('reads right for a token %s', async (_label, msLeft, badge) => {
    listTokens.mockResolvedValue({ tokens: [token('Nightly', msLeft)] });
    render(
      <Theme>
        <ServiceAccountTokensPanel open onOpenChange={() => {}} account={ACCOUNT} />
      </Theme>
    );

    expect(await screen.findByText(badge)).toBeTruthy();
  });
});
