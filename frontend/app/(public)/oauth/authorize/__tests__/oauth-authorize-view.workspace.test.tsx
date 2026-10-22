import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { AxiosError, AxiosHeaders } from 'axios';
import { ErrorType } from '@/lib/api/api-error';

const { get, switchToSignedInWorkspace, t, searchParams } = vi.hoisted(() => ({
  get: vi.fn(),
  switchToSignedInWorkspace: vi.fn(() => false),
  // Stable across renders, as in the app: the page's effect depends on both.
  t: (key: string) => key,
  searchParams: new URLSearchParams(
    'client_id=cid&redirect_uri=https%3A%2F%2Fapp.example.com%2Fcb&scope=org%3Aread&state=s1',
  ),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t }),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => searchParams,
}));
vi.mock('@/lib/api', () => ({ apiClient: { get, post: vi.fn() } }));
vi.mock('@/config', () => ({
  useAuthStore: (select: (s: { isHydrated: boolean; isAuthenticated: boolean }) => unknown) =>
    select({ isHydrated: true, isAuthenticated: true }),
  switchToSignedInWorkspace,
}));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));

import { OAuthAuthorizeView } from '../oauth-authorize-view';

function rejectedByServer(status: number, body: Record<string, unknown>) {
  const axiosError = new AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, undefined, {
    status,
    statusText: '',
    headers: {},
    config: { headers: new AxiosHeaders() },
    data: body,
  });
  // apiClient rejects with a processed error that keeps the axios error underneath.
  return { message: 'Request failed', type: ErrorType.VALIDATION_ERROR, statusCode: status, originalError: axiosError };
}

describe('OAuthAuthorizeView', () => {
  beforeEach(() => {
    get.mockReset();
    switchToSignedInWorkspace.mockReset();
    switchToSignedInWorkspace.mockReturnValue(false);
  });
  afterEach(() => cleanup());

  it("shows the server's error_description when apiClient rejects", async () => {
    get.mockRejectedValue(rejectedByServer(400, { error: 'invalid_request', error_description: 'redirect_uri mismatch' }));

    render(<Theme><OAuthAuthorizeView /></Theme>);

    expect(await screen.findByText('redirect_uri mismatch')).toBeTruthy();
  });

  it('retries once in the workspace that owns the app when the edition can switch to it', async () => {
    switchToSignedInWorkspace.mockReturnValueOnce(true);
    get.mockRejectedValue(rejectedByServer(403, {
      error: 'access_denied', error_description: 'This application belongs to a different workspace.', org_id: 'org-2',
    }));

    render(<Theme><OAuthAuthorizeView /></Theme>);

    await screen.findByText('This application belongs to a different workspace.');
    expect(switchToSignedInWorkspace).toHaveBeenCalledWith('org-2');
    expect(get).toHaveBeenCalledTimes(2);
  });
});
