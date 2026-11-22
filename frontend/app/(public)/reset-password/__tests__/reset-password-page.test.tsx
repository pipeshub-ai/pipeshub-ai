import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

const replace = vi.fn();
const push = vi.fn();
const logout = vi.fn();
const authState = { isHydrated: true, isAuthenticated: false, logout };

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push }),
}));

vi.mock('@/config', () => ({
  useAuthStore: (selector?: (s: typeof authState) => unknown) =>
    selector ? selector(authState) : authState,
}));

vi.mock('@/lib/hooks/use-breakpoint', () => ({ useAuthWideLayout: () => false }));
vi.mock('../../components/auth-hero', () => ({ default: () => null }));
vi.mock('../../components/form-panel', () => ({
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock('../../components/password-reset-success', () => ({
  default: () => <div>reset-success</div>,
}));
vi.mock('../../components/password-reset-failure', () => ({
  default: ({ variant }: { variant: string }) => <div>reset-failure:{variant}</div>,
}));
vi.mock('../../forms/change-password', () => ({
  default: ({
    token,
    onSuccess,
    onInvalidToken,
  }: {
    token: string;
    onSuccess: () => void;
    onInvalidToken?: () => void;
  }) => (
    <div>
      <span>reset-form:{token}</span>
      <button type="button" onClick={onSuccess}>
        submit-ok
      </button>
      <button type="button" onClick={onInvalidToken}>
        submit-invalid
      </button>
    </div>
  ),
}));

import ResetPasswordPage from '../page';

function makeToken(claims: Record<string, unknown>): string {
  const encode = (value: unknown) =>
    btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${encode({ alg: 'HS256' })}.${encode(claims)}.signature`;
}

const nowSeconds = () => Math.floor(Date.now() / 1000);

// A password-reset token lives 20 minutes; an invite lives 48 hours.
const validResetToken = () =>
  makeToken({ userEmail: 'a@example.com', iat: nowSeconds(), exp: nowSeconds() + 20 * 60 });
const expiredResetToken = () =>
  makeToken({
    userEmail: 'a@example.com',
    iat: nowSeconds() - 3600,
    exp: nowSeconds() - 60,
  });
const expiredInviteToken = () =>
  makeToken({
    userEmail: 'a@example.com',
    iat: nowSeconds() - 3 * 24 * 3600,
    exp: nowSeconds() - 3600,
  });

function openLink(token: string | null) {
  window.location.hash = token ? `#token=${encodeURIComponent(token)}` : '';
}

describe('ResetPasswordPage', () => {
  beforeEach(() => {
    replace.mockReset();
    push.mockReset();
    logout.mockReset();
    authState.isHydrated = true;
    authState.isAuthenticated = false;
  });

  afterEach(() => {
    cleanup();
    window.location.hash = '';
  });

  describe('signed out', () => {
    it('shows the form for a valid link', () => {
      const token = validResetToken();
      openLink(token);
      render(<ResetPasswordPage />);

      expect(screen.getByText(`reset-form:${token}`)).toBeTruthy();
      expect(replace).not.toHaveBeenCalled();
    });

    it('does not sign anyone out when the password changes', () => {
      openLink(validResetToken());
      render(<ResetPasswordPage />);

      fireEvent.click(screen.getByText('submit-ok'));

      expect(screen.getByText('reset-success')).toBeTruthy();
      expect(logout).not.toHaveBeenCalled();
    });
  });

  describe('signed in to a workspace', () => {
    beforeEach(() => {
      authState.isAuthenticated = true;
    });

    it('still shows the form instead of redirecting into the app', () => {
      const token = validResetToken();
      openLink(token);
      render(<ResetPasswordPage />);

      expect(screen.getByText(`reset-form:${token}`)).toBeTruthy();
      expect(replace).not.toHaveBeenCalled();
      expect(push).not.toHaveBeenCalled();
      expect(logout).not.toHaveBeenCalled();
    });

    it('drops the stored session once the password changes, so Sign In reaches the login page', () => {
      openLink(validResetToken());
      render(<ResetPasswordPage />);

      fireEvent.click(screen.getByText('submit-ok'));

      expect(logout).toHaveBeenCalledTimes(1);
      expect(screen.getByText('reset-success')).toBeTruthy();
      expect(screen.queryByText(/^reset-form:/)).toBeNull();
    });

    it('keeps the session when the submit is rejected', () => {
      openLink(validResetToken());
      render(<ResetPasswordPage />);

      fireEvent.click(screen.getByText('submit-invalid'));

      expect(logout).not.toHaveBeenCalled();
      expect(screen.getByText('reset-failure:reset-expired')).toBeTruthy();
    });

    it('explains a missing token instead of redirecting', () => {
      openLink(null);
      render(<ResetPasswordPage />);

      expect(screen.getByText('reset-failure:missing-token')).toBeTruthy();
      expect(replace).not.toHaveBeenCalled();
    });

    it('explains an expired link instead of redirecting', () => {
      openLink(expiredResetToken());
      render(<ResetPasswordPage />);

      expect(screen.getByText('reset-failure:reset-expired')).toBeTruthy();
      expect(replace).not.toHaveBeenCalled();
    });
  });

  describe('failure screens', () => {
    it('shows the invite variant for an expired invite link', () => {
      openLink(expiredInviteToken());
      render(<ResetPasswordPage />);

      expect(screen.getByText('reset-failure:invite-expired')).toBeTruthy();
    });

    it('shows the missing-token screen when there is no token in the hash', () => {
      openLink(null);
      render(<ResetPasswordPage />);

      expect(screen.getByText('reset-failure:missing-token')).toBeTruthy();
    });

    it('swaps to the expired screen when the server rejects the token on submit', () => {
      openLink(validResetToken());
      render(<ResetPasswordPage />);

      fireEvent.click(screen.getByText('submit-invalid'));

      expect(screen.getByText('reset-failure:reset-expired')).toBeTruthy();
    });
  });

  it('renders nothing until the auth store has hydrated', () => {
    authState.isHydrated = false;
    openLink(validResetToken());
    const { container } = render(<ResetPasswordPage />);

    expect(container.textContent).toBe('');
  });
});
