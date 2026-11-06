/**
 * The rail's footer mounted again (the next page's rail) shows the org from its first frame: no blank name.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const h = vi.hoisted(() => ({ getOrg: vi.fn(), getLogoUrl: vi.fn() }));

vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({
    t: (key: string, vars?: { name?: string }) => (vars?.name ? `${key}:${vars.name}` : key),
    i18n: { language: 'en-US' },
  }),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/user-avatar', () => ({ UserAvatar: () => null }));
vi.mock('@/config', () => ({ WorkspaceMenu: ({ trigger }: { trigger: React.ReactNode }) => <>{trigger}</> }));
vi.mock('@/app/(main)/workspace/general/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/app/(main)/workspace/general/api')>()),
  OrgApi: { getOrg: h.getOrg, getLogoUrl: h.getLogoUrl },
}));

const { ChatSidebarFooter } = await import('../footer');

const renderFooter = () =>
  render(
    <Theme>
      <ChatSidebarFooter />
    </Theme>,
  );
const label = () => screen.getByRole('button').getAttribute('aria-label');

afterEach(() => cleanup());

describe('the rail footer keeps the org', () => {
  it('mounted again, it names the org from its first frame', async () => {
    let answer: (value: unknown) => void = () => {};
    h.getOrg.mockResolvedValueOnce({ _id: 'o1', registeredName: 'Acme Inc', shortName: 'Acme', domain: 'x', accountType: 'business' });
    h.getLogoUrl.mockResolvedValue(null);
    const first = renderFooter();
    await waitFor(() => expect(label()).toBe('workspaceMenu.triggerLabel:Acme'));
    first.unmount();
    h.getOrg.mockReturnValueOnce(new Promise((resolve) => (answer = resolve)));

    renderFooter();

    expect(label()).toBe('workspaceMenu.triggerLabel:Acme');
    answer(null);
  });
});
