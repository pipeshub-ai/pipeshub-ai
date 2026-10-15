import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import type { ProjectConversationRow } from '@/chat/project-api';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/chat/',
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../header', () => ({ ChatSidebarHeader: () => null }));
vi.mock('../footer', () => ({ ChatSidebarFooter: () => null }));

const projectApi = vi.hoisted(() => ({ get: vi.fn(), listConversations: vi.fn() }));
vi.mock('@/chat/project-api', () => ({ ProjectApi: projectApi }));

const rowProps = vi.hoisted(() => [] as Array<Record<string, unknown>>);
vi.mock('../chat-section-element', () => ({
  ChatSectionElement: (props: Record<string, unknown>) => {
    rowProps.push(props);
    return null;
  },
  ChatItemSkeleton: () => null,
  GeneratingTitleItem: () => null,
}));

import { ProjectConversationsSidebar } from '../project-conversations-sidebar';

function row(overrides: Partial<ProjectConversationRow>): ProjectConversationRow {
  return {
    _id: 'conv',
    title: 'Chat',
    userId: 'u1',
    initiator: 'u1',
    isShared: false,
    isArchived: false,
    status: 'Complete',
    lastActivityAt: Date.now(),
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    ...overrides,
  };
}

describe('ProjectConversationsSidebar rows', () => {
  beforeEach(() => {
    rowProps.length = 0;
    projectApi.get.mockResolvedValue({ _id: 'proj-1', name: 'Project' });
    // The endpoint never returns `sessionType` (unselected); agent rows carry `agentKey`.
    projectApi.listConversations.mockResolvedValue({
      conversations: [row({ _id: 'plain-1' }), row({ _id: 'agent-1', agentKey: 'agent-key' })],
      pagination: { page: 1, limit: 20, totalCount: 2, totalPages: 1 },
    });
  });

  it('opens agent chats through the agent route and keeps both rows linked to the project', async () => {
    render(<ProjectConversationsSidebar projectId="proj-1" />);
    await waitFor(() => expect(rowProps.length).toBeGreaterThanOrEqual(2));
    const byId = (id: string) =>
      rowProps.filter((p) => (p.conversation as { id: string }).id === id).at(-1)!;

    expect(byId('agent-1')).toMatchObject({
      agentId: 'agent-key',
      projectId: undefined,
      conversation: { projectId: 'proj-1' },
    });
    expect(byId('plain-1')).toMatchObject({
      agentId: undefined,
      projectId: 'proj-1',
      conversation: { projectId: 'proj-1' },
    });
  });
});
