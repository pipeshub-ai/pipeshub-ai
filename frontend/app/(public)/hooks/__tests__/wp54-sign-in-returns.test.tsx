/**
 * WP-54: every sign-in method comes back to the page that sent the person to sign in. The page remembers itself in
 * the tab's session storage (path only); password, OTP, Google and Microsoft sign-ins go there (SAML and generic
 * OAuth sign-in were removed in 1.0, main #4217). An address `returnTo` still wins; a remembered destination is used
 * only when it is a path inside the app, and stays until the page forgets it. A session that expired on the page
 * (sign-in at `/login` with no `returnTo`) comes back too.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { forgetReturnTo, peekReturnTo, rememberReturnTo, takeReturnTo } from '@/lib/auth/post-sign-in-return';

const api = vi.hoisted(() => ({
  signInWithPassword: vi.fn(),
  signInWithOtp: vi.fn(),
  signInWithGoogle: vi.fn(),
  signInWithMicrosoft: vi.fn(),
}));
const { push } = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push, replace: vi.fn() }) }));
vi.mock('@/config', () => ({
  useAuthStore: (fn: (s: Record<string, unknown>) => unknown) => fn({ setTokens: vi.fn(), setUser: vi.fn(), logout: vi.fn() }),
  takeSignInRefusal: () => false,
}));
vi.mock('@/lib/store/toast-store', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }));
vi.mock('@/lib/auth/hydrate-user', () => ({ fetchAndSetCurrentUser: vi.fn() }));
vi.mock('@/lib/electron', () => ({ isElectron: () => false, desktopOAuthErrorMessage: vi.fn(), runDesktopOAuth: vi.fn() }));
vi.mock('../../api', () => ({ AuthApi: api }));

import { useAuthActions } from '../use-auth-actions';

const SESSION = { accessToken: 'at', refreshToken: 'rt' };
const PAGE = '/workspace/schedules';

const methods: Array<[string, (a: ReturnType<typeof useAuthActions>) => Promise<unknown>]> = [
  ['password', (a) => a.signInWithPassword('pw')],
  ['OTP', (a) => a.signInWithOtp('123456')],
  ['Google', (a) => a.signInWithGoogle('cred')],
  ['Microsoft', (a) => a.signInWithMicrosoft({ accessToken: 'a', idToken: 'i' })],
];

describe('WP-54: every sign-in returns to the page the tab remembered', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    for (const fn of Object.values(api)) fn.mockResolvedValue(SESSION);
  });

  it.each(methods)('WP-54: %s sign-in goes back to the page the tab remembered', async (_name, signIn) => {
    rememberReturnTo(PAGE);
    const { result } = renderHook(() => useAuthActions({ email: 'asha@acme.example' }));
    await act(async () => {
      await signIn(result.current);
    });
    expect(push).toHaveBeenCalledWith(PAGE);
    // The sign-in page's guard reads the same destination as the store flips to signed in.
    expect(peekReturnTo()).toBe(PAGE);
  });

  it('WP-54: an address returnTo wins over a remembered one; nothing remembered goes to the chat', async () => {
    rememberReturnTo(PAGE);
    const routed = renderHook(() => useAuthActions({ email: 'a@b.co', redirectTo: '/oauth/authorize?x=1' }));
    await act(() => routed.result.current.signInWithPassword('pw'));
    expect(push).toHaveBeenLastCalledWith('/oauth/authorize?x=1');
    forgetReturnTo();
    const plain = renderHook(() => useAuthActions({ email: 'a@b.co' }));
    await act(() => plain.result.current.signInWithPassword('pw'));
    expect(push).toHaveBeenLastCalledWith('/chat');
  });

  it('WP-54: only a path inside the app is remembered or used', () => {
    rememberReturnTo('https://evil.example/x');
    expect(takeReturnTo()).toBeNull();
    window.sessionStorage.setItem('pipeshub.postSignInReturnTo', '//evil.example');
    expect(takeReturnTo()).toBeNull();
    rememberReturnTo(PAGE);
    forgetReturnTo();
    expect(takeReturnTo()).toBeNull();
  });
});
