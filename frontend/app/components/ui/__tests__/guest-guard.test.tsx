import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';

const replace = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace, push: vi.fn() }) }));

let auth = { isHydrated: true, isAuthenticated: true };
vi.mock('@/config', () => ({
  useAuthStore: (fn: (s: typeof auth) => unknown) => fn(auth),
}));
vi.mock('../auth-guard', () => ({ LoadingScreen: () => null }));

import { GuestGuard } from '../guest-guard';

function renderAt(search: string) {
  window.history.replaceState(null, '', `/login/${search}`);
  render(<GuestGuard><div>login form</div></GuestGuard>);
}

describe('GuestGuard', () => {
  beforeEach(() => {
    replace.mockReset();
    auth = { isHydrated: true, isAuthenticated: true };
  });
  afterEach(() => cleanup());

  // Signing in flips isAuthenticated on the login page; the guard must not send an OAuth login to /chat.
  it('sends a signed-in user back to an in-app returnTo', () => {
    const back = '/oauth/authorize?client_id=https%3A%2F%2Fclaude.ai%2Fm&state=s';
    renderAt(`?returnTo=${encodeURIComponent(back)}`);
    expect(replace).toHaveBeenCalledWith(back);
  });

  it('falls back to /chat without a returnTo or with one that leaves the app', () => {
    renderAt('');
    renderAt(`?returnTo=${encodeURIComponent('https://evil.example/x')}`);
    renderAt(`?returnTo=${encodeURIComponent('//evil.example/x')}`);
    expect(replace.mock.calls).toEqual([['/chat'], ['/chat'], ['/chat']]);
  });

  it('stays on the page while signed out', () => {
    auth = { isHydrated: true, isAuthenticated: false };
    renderAt('?returnTo=%2Fchat');
    expect(replace).not.toHaveBeenCalled();
  });
});
