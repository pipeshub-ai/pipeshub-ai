import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

const replace = vi.fn();
let token: string | null = 'tok_123';
let authState = { isHydrated: true, isAuthenticated: true };

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  useSearchParams: () => ({
    get: (key: string) => (key === 'token' ? token : null),
    toString: () => (token ? `token=${token}` : ''),
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

import { SlackLinkView } from '../slack-link-view';

const renderView = () => render(React.createElement(Theme, null, React.createElement(SlackLinkView, null)));

const context = {
  slack: { teamId: 'T0HOME', userId: 'U0ALICE', name: 'Alice' },
  botName: 'PipesHub',
  expiresAt: '2026-11-13T00:10:00Z',
  account: { email: 'alice@example.com', fullName: 'Alice A' },
  alreadyLinkedToYou: false,
  linkedToAnotherAccount: false,
};

beforeEach(() => {
  replace.mockClear();
  post.mockReset();
  token = 'tok_123';
  authState = { isHydrated: true, isAuthenticated: true };
});
afterEach(() => cleanup());

describe('SlackLinkView (R38-IDENT SEC-C-03)', () => {
  it('sends a signed-out person to sign in and back here', () => {
    authState = { isHydrated: true, isAuthenticated: false };
    renderView();
    expect(replace).toHaveBeenCalledWith(`/login?returnTo=${encodeURIComponent('/slack/link?token=tok_123')}`);
    expect(post).not.toHaveBeenCalled();
  });

  it('names both sides of the link before anything is bound', async () => {
    post.mockResolvedValueOnce({ data: context });
    renderView();
    const summary = await screen.findByTestId('slack-link-summary');
    expect(summary.textContent).toContain('Alice (U0ALICE)');
    expect(summary.textContent).toContain('T0HOME');
    expect(summary.textContent).toContain('Alice A (alice@example.com)');
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][1]).toEqual({ token: 'tok_123' });
  });

  it('links only when the person approves', async () => {
    post.mockResolvedValueOnce({ data: context }).mockResolvedValueOnce({ data: { linked: true } });
    renderView();
    fireEvent.click(await screen.findByText(en.slackLink.approve));
    await waitFor(() => expect(screen.getByText(en.slackLink.doneTitle)).toBeTruthy());
    expect(post.mock.calls[1][1]).toEqual({ token: 'tok_123', action: 'approve' });
  });

  it('cancels without linking', async () => {
    post.mockResolvedValueOnce({ data: context }).mockResolvedValueOnce({ data: { linked: false } });
    renderView();
    fireEvent.click(await screen.findByText(en.slackLink.deny));
    await waitFor(() => expect(screen.getByText(en.slackLink.deniedTitle)).toBeTruthy());
    expect(post.mock.calls[1][1]).toEqual({ token: 'tok_123', action: 'deny' });
  });

  it('warns when the Slack account is linked to someone else now', async () => {
    post.mockResolvedValueOnce({ data: { ...context, linkedToAnotherAccount: true } });
    renderView();
    expect(await screen.findByText(en.slackLink.replacesOther)).toBeTruthy();
  });

  it('says, in the person\'s language, that a link sent by another organization\'s bot is not theirs', async () => {
    post.mockRejectedValueOnce(Object.assign(new Error('x'), {
      isAxiosError: true,
      response: { status: 403, data: { error: { code: 'HTTP_SLACK_LINK_OTHER_ORG', message: 'server words' } } },
    }));
    renderView();
    expect(await screen.findByText(en.slackLink.otherOrg)).toBeTruthy();
  });

  it('says what to do when the page has no link', () => {
    token = null;
    renderView();
    expect(screen.getByTestId('slack-link-error').textContent).toBe(en.slackLink.missingLink);
    expect(post).not.toHaveBeenCalled();
  });
});
