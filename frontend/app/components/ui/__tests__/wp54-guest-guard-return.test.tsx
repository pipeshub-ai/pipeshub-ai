/**
 * WP-54: the sign-in page's guard sends a signed-in person where the sign-in was going, not always to the chat. It
 * runs as the sign-in completes (the store flips to signed in), so it decided the destination last: every sign-in on
 * `/login?returnTo=…` ended on `/chat`, and the page that asked was never reached.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/react';
import { rememberReturnTo } from '@/lib/auth/post-sign-in-return';

const replace = vi.fn();
const auth = { isHydrated: true, isAuthenticated: true };
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace, push: vi.fn() }) }));
vi.mock('@/config', () => ({ useAuthStore: (pick: (s: typeof auth) => unknown) => pick(auth) }));
vi.mock('../auth-guard', () => ({ LoadingScreen: () => <div data-testid="loading" /> }));

import { GuestGuard } from '../guest-guard';

const at = (path: string) => window.history.replaceState(null, '', path);

describe('WP-54 GuestGuard destination', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    auth.isAuthenticated = true;
  });
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('WP-54: signed in on /login?returnTo=…, the guard goes to the returnTo', async () => {
    at('/login?returnTo=%2Fworkspace%2Fschedules');
    render(<GuestGuard>form</GuestGuard>);
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/workspace/schedules'));
  });

  it('WP-54: without one, to the destination the tab remembered (an expired session on that page)', async () => {
    at('/login');
    rememberReturnTo('/workspace/schedules');
    render(<GuestGuard>form</GuestGuard>);
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/workspace/schedules'));
  });

  it('WP-54: an unsafe returnTo and nothing remembered go to the chat, as before', async () => {
    at('/login?returnTo=https%3A%2F%2Fevil.example');
    render(<GuestGuard>form</GuestGuard>);
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/chat'));
  });
});
