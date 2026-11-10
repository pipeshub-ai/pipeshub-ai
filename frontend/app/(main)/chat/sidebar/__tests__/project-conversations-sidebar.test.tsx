import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const h = vi.hoisted(() => ({
  t: (key: string) => key,
  get: vi.fn(),
  listConversations: vi.fn(),
  remove: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/projects',
  useSearchParams: () => new URLSearchParams('projectId=p1'),
}));
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: h.t, i18n: { language: 'en-US' } }),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/chat-star-icon', () => ({ ChatStarIcon: () => null }));
vi.mock('@/app/components/sidebar', () => ({
  SidebarBase: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  ICON_SIZE_DEFAULT: 16,
}));
vi.mock('@/config', () => ({ ChatSidebarFooter: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/lib/navigation', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));
vi.mock('../header', () => ({ ChatSidebarHeader: () => null }));
vi.mock('../sidebar-item', () => ({ SidebarItem: ({ label }: { label: React.ReactNode }) => <span>{label}</span> }));
vi.mock('../chat-section-element', () => ({
  ChatSectionElement: ({ conversation }: { conversation: { title: string } }) => <span>{conversation.title}</span>,
  ChatItemSkeleton: () => null,
  GeneratingTitleItem: () => null,
}));
vi.mock('@/chat/project-api', async (importActual) => ({
  ...(await importActual<typeof import('@/chat/project-api')>()),
  ProjectApi: { get: h.get, listConversations: h.listConversations },
}));

import { clearSessionCache } from '@/lib/api/session-cache';
import { writeSwrKey } from '@/lib/api/swr-cache';
import { projectDetailKey } from '@/chat/project-api';
import { ProjectConversationsSidebar } from '../project-conversations-sidebar';

const renderSidebar = () => render(<Theme><ProjectConversationsSidebar projectId="p1" /></Theme>);

beforeEach(() => {
  vi.clearAllMocks();
  clearSessionCache();
  h.get.mockResolvedValue({ _id: 'p1', name: 'Launch plan', color: '#f00' });
  h.listConversations.mockResolvedValue({ conversations: [], pagination: { totalCount: 0 } });
});

afterEach(() => cleanup());

describe('project sidebar', () => {
  it('shows the project name from the shared project read', async () => {
    renderSidebar();

    expect(await screen.findByText('Launch plan')).toBeTruthy();
    expect(h.get).toHaveBeenCalledTimes(1);
  });

  it('shows "Failed to load" when the project cannot be loaded', async () => {
    h.get.mockRejectedValue(new Error('network'));

    renderSidebar();

    expect(await screen.findByText('chat.failedToLoad')).toBeTruthy();
  });

  it('shows no "Failed to load" for a project just deleted or left in this tab', async () => {
    renderSidebar();
    expect(await screen.findByText('Launch plan')).toBeTruthy();
    await waitFor(() => expect(screen.getByText('chat.projects.noChats')).toBeTruthy());

    // What ProjectApi.remove and ProjectApi.leave write, while the page moves away.
    act(() => writeSwrKey(projectDetailKey('p1'), null));

    await waitFor(() => expect(screen.queryByText('Launch plan')).toBeNull());
    expect(screen.queryByText('chat.failedToLoad')).toBeNull();
  });
});
