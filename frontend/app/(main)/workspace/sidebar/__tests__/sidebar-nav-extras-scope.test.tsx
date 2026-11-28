/**
 * Entries an edition adds (`workspaceNavExtras` in config.ts) follow the personal/workspace split:
 * a personal one opens Personal settings, and a "workspace" one joins the admin list of its anchor.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

let currentPath = '/workspace/general';
vi.mock('next/navigation', () => ({
  usePathname: () => currentPath,
}));

vi.mock('@/lib/hooks/use-is-mobile', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/hooks/use-is-mobile')>()),
  useIsMobile: () => false,
}));

vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: { profile: { isAdmin: boolean } }) => unknown) => selector({ profile: { isAdmin: true } }),
  selectIsAdmin: (s: { profile: { isAdmin: boolean } }) => s.profile.isAdmin,
  selectRefreshedIsAdmin: (s: { profile: { isAdmin: boolean } }) => s.profile.isAdmin,
}));

vi.mock('@/config', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/config')>()),
  workspaceNavExtras: [
    { section: 'personal', icon: 'star', labelKey: 'test.mine', route: '/workspace/mine' },
    { section: 'developerSettings', icon: 'key', labelKey: 'test.myTokens', route: '/workspace/my-tokens', personal: true },
    { section: 'workspace', icon: 'star', labelKey: 'test.orgThing', route: '/workspace/org-thing', adminOnly: true, after: '/workspace/mail' },
  ],
}));

import WorkspaceSidebar from '../index';

afterEach(() => {
  cleanup();
  currentPath = '/workspace/general';
});

const renderAt = (path: string) => {
  currentPath = path;
  return render(<Theme><WorkspaceSidebar /></Theme>);
};

describe('WorkspaceSidebar — an edition\'s entries', () => {
  it('treats a personal entry\'s page as Personal settings and lists it only there', () => {
    renderAt('/workspace/mine');
    expect(screen.getByText('test.mine')).toBeTruthy();
    expect(screen.getByText(en.workspaceMenu.workspaceSettings)).toBeTruthy();
    expect(screen.queryByText('test.orgThing')).toBeNull();
  });

  it('treats a personal developer-settings entry\'s page as Personal settings and lists it there', () => {
    renderAt('/workspace/my-tokens');
    expect(screen.getByText('test.myTokens')).toBeTruthy();
    expect(screen.getByText(en.workspaceMenu.workspaceSettings)).toBeTruthy();
    expect(screen.queryByText('test.orgThing')).toBeNull();
  });

  it('lists a "workspace" entry right after its anchor in System, for an admin in Workspace settings', () => {
    renderAt('/workspace/general');
    expect(screen.queryByText('test.mine')).toBeNull();
    const links = screen.getAllByRole('link').map((a) => a.getAttribute('href')?.replace(/\/$/, ''));
    const mail = links.indexOf('/workspace/mail');
    expect(mail).toBeGreaterThan(-1);
    expect(links[mail + 1]).toBe('/workspace/org-thing');
  });
});
