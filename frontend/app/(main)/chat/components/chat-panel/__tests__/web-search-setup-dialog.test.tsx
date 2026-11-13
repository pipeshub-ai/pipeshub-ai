import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const router = vi.hoisted(() => ({ push: vi.fn() }));
const user = vi.hoisted(() => ({ isAdmin: null as boolean | null }));
vi.mock('next/navigation', () => ({ useRouter: () => router }));
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (select: (s: unknown) => unknown) => select({ profile: { isAdmin: user.isAdmin } }),
  selectIsAdmin: (s: { profile?: { isAdmin: boolean | null } }) => s.profile?.isAdmin ?? null,
}));

import { WebSearchSetupDialog } from '../web-search-setup-dialog';

function open(onOpenChange = vi.fn()) {
  render(
    <Theme>
      <WebSearchSetupDialog open onOpenChange={onOpenChange} />
    </Theme>,
  );
  return onOpenChange;
}

beforeEach(() => router.push.mockReset());
afterEach(cleanup);

describe('WebSearchSetupDialog', () => {
  it('tells a member to ask their admin, with nowhere to go', () => {
    user.isAdmin = false;
    open();

    expect(screen.getByText(/hasn.t set up a web search provider/)).toBeTruthy();
    expect(screen.getByText(/Ask your admin to configure it under Workspace/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Set up web search' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Got it' })).toBeTruthy();
  });

  it('is treated as a member until the profile says admin', () => {
    user.isAdmin = null;
    open();

    expect(screen.queryByRole('button', { name: 'Set up web search' })).toBeNull();
  });

  it('takes an admin to the web search settings and closes', () => {
    user.isAdmin = true;
    const onOpenChange = open();

    fireEvent.click(screen.getByRole('button', { name: 'Set up web search' }));

    expect(router.push).toHaveBeenCalledWith('/workspace/web-search');
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('lets an admin put it off', () => {
    user.isAdmin = true;
    const onOpenChange = open();

    fireEvent.click(screen.getByRole('button', { name: 'Not now' }));

    expect(router.push).not.toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
