import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

const replace = vi.fn();
let attemptToken: string | null = 'att_123';
let authState = { isHydrated: true, isAuthenticated: true };

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  useSearchParams: () => ({
    get: (key: string) => (key === 'claim_attempt_token' ? attemptToken : null),
    toString: () => (attemptToken ? `claim_attempt_token=${attemptToken}` : ''),
  }),
}));

vi.mock('@/config', () => ({
  useAuthStore: (fn: (s: typeof authState) => unknown) => fn(authState),
}));

const post = vi.fn();
vi.mock('@/lib/api', () => ({
  apiClient: { post: (...args: unknown[]) => post(...args) },
}));

vi.mock('@/lib/api/api-error', () => ({
  extractApiErrorMessage: () => '',
  processError: () => ({ message: 'error' }),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, string>) => {
      let cur: unknown = en;
      for (const part of key.split('.')) {
        if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
        cur = (cur as Record<string, unknown>)[part];
      }
      if (typeof cur !== 'string') return key;
      return cur.replace(/\{\{(\w+)\}\}/g, (_, k) => vars?.[k] ?? '');
    },
  }),
}));

vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));

vi.mock('@/app/components/ui/loading-button', () => ({
  LoadingButton: ({
    children,
    onClick,
    loading,
    disabled,
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    loading?: boolean;
    disabled?: boolean;
  }) => React.createElement('button', { type: 'button', onClick, disabled: loading || disabled }, children),
}));

import { AgentClaimView } from '../agent-claim-view';

const renderView = () => render(React.createElement(Theme, null, React.createElement(AgentClaimView, null)));

const context = {
  scopes: [{ name: 'org:read', description: 'Read the organization' }],
  user: { email: 'ada@example.com' },
};

beforeEach(() => {
  replace.mockClear();
  post.mockReset();
  attemptToken = 'att_123';
  authState = { isHydrated: true, isAuthenticated: true };
});
afterEach(() => cleanup());

describe('AgentClaimView', () => {
  it('sends a signed-out visitor to login and back here with the same link', async () => {
    authState = { isHydrated: true, isAuthenticated: false };
    renderView();
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith(
        '/login?returnTo=' + encodeURIComponent('/agent/claim?claim_attempt_token=att_123'),
      ),
    );
    expect(post).not.toHaveBeenCalled();
  });

  it('shows who is signed in and what the agent will be able to do', async () => {
    post.mockResolvedValue({ data: context });
    renderView();
    await screen.findByText(en.agentClaim.scopesHeading);
    expect(post).toHaveBeenCalledWith(
      '/api/v1/agent/identity/claim/verify',
      { claim_attempt_token: 'att_123' },
      { suppressErrorToast: true },
    );
    expect(screen.getByText('Signed in as ada@example.com')).toBeTruthy();
    expect(screen.getByText(/org:read/)).toBeTruthy();
    expect(screen.getByText(en.agentClaim.intro)).toBeTruthy();
  });

  it('keeps Approve off until six digits are typed, then posts the code', async () => {
    post.mockResolvedValueOnce({ data: context }).mockResolvedValueOnce({ data: { ok: true, status: 'claimed' } });
    renderView();
    const input = await screen.findByLabelText(en.agentClaim.codeLabel);
    const approve = screen.getByRole('button', { name: en.agentClaim.approve }) as HTMLButtonElement;
    expect(approve.disabled).toBe(true);
    fireEvent.change(input, { target: { value: '12a345' } }); // letters are dropped
    expect(approve.disabled).toBe(true);
    fireEvent.change(input, { target: { value: '123456' } });
    expect(approve.disabled).toBe(false);
    fireEvent.click(approve);
    await waitFor(() =>
      expect(post).toHaveBeenLastCalledWith(
        '/api/v1/agent/identity/claim/complete',
        { claim_attempt_token: 'att_123', user_code: '123456', action: 'approve' },
        { suppressErrorToast: true },
      ),
    );
    expect(await screen.findByText(en.agentClaim.doneTitle)).toBeTruthy();
  });

  it('denies without needing a code', async () => {
    post.mockResolvedValueOnce({ data: context }).mockResolvedValueOnce({ data: { ok: true, status: 'revoked' } });
    renderView();
    fireEvent.click(await screen.findByRole('button', { name: en.oauthConsent.deny }));
    await waitFor(() =>
      expect(post).toHaveBeenLastCalledWith(
        '/api/v1/agent/identity/claim/complete',
        { claim_attempt_token: 'att_123', user_code: '', action: 'deny' },
        { suppressErrorToast: true },
      ),
    );
    expect(await screen.findByText(en.agentClaim.deniedTitle)).toBeTruthy();
  });

  it('shows the server\'s reason when the link cannot be used', async () => {
    post.mockRejectedValue(new Error('This claim link is not valid'));
    renderView();
    expect(await screen.findByText('This claim link is not valid')).toBeTruthy();
    expect(screen.queryByRole('button', { name: en.agentClaim.approve })).toBeNull();
  });

  it('says so when the link carries no token, and calls nothing', () => {
    attemptToken = null;
    renderView();
    expect(screen.getByText(en.agentClaim.missingLink)).toBeTruthy();
    expect(post).not.toHaveBeenCalled();
  });
});
