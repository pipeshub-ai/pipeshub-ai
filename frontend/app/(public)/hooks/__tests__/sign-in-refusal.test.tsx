import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const { signInWithPassword, toastError, setTokens, takeSignInRefusal } = vi.hoisted(() => ({
  signInWithPassword: vi.fn(),
  toastError: vi.fn(),
  setTokens: vi.fn(),
  takeSignInRefusal: vi.fn(),
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));
vi.mock('@/config', () => ({
  useAuthStore: (fn: (s: Record<string, unknown>) => unknown) => fn({ setTokens, setUser: vi.fn(), logout: vi.fn() }),
  takeSignInRefusal: (error: unknown) => takeSignInRefusal(error),
}));
vi.mock('@/lib/store/toast-store', () => ({ toast: { error: (...a: unknown[]) => toastError(...a), success: vi.fn(), info: vi.fn() } }));
vi.mock('@/lib/auth/hydrate-user', () => ({ fetchAndSetCurrentUser: vi.fn() }));
vi.mock('@/lib/electron', () => ({ isElectron: () => false, desktopOAuthErrorMessage: vi.fn(), runDesktopOAuth: vi.fn() }));
vi.mock('../../api', () => ({ AuthApi: { signInWithPassword: (...a: unknown[]) => signInWithPassword(...a) } }));

import { useAuthActions } from '../use-auth-actions';
import { takeSignInRefusal as builtIn } from '@/lib/auth/sign-in-refusal';

const refused = { response: { status: 403, data: { error: { message: 'Forbidden' } } } };

describe('a refused sign-in', () => {
  beforeEach(() => vi.clearAllMocks());

  it('this build takes no refusal itself', () => {
    expect(builtIn(refused)).toBe(false);
  });

  it('is an ordinary sign-in error when the configured handler does not take it', async () => {
    takeSignInRefusal.mockReturnValue(false);
    signInWithPassword.mockRejectedValue(refused);
    const { result } = renderHook(() => useAuthActions({ email: 'mo@acme.example' }));
    await act(() => result.current.signInWithPassword('Right-Password-1!'));
    expect(takeSignInRefusal).toHaveBeenCalledWith(refused);
    expect(toastError).toHaveBeenCalled();
    expect(setTokens).not.toHaveBeenCalled();
  });

  it('is left to the configured handler when it takes it: no error, no session', async () => {
    takeSignInRefusal.mockReturnValue(true);
    signInWithPassword.mockRejectedValue(refused);
    const { result } = renderHook(() => useAuthActions({ email: 'mo@acme.example' }));
    await act(() => result.current.signInWithPassword('Right-Password-1!'));
    expect(toastError).not.toHaveBeenCalled();
    expect(setTokens).not.toHaveBeenCalled();
  });
});
