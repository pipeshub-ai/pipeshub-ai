/**
 * Bug bash 2026-10-22: an agent picked with `@` in the project workspace's composer did not answer. The workspace hands
 * the question to the chat page, and that handoff kept the text (with its `<@agent:…>` token) but dropped what the
 * send carried besides it, so the server stored the token inert and the assistant answered.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const routerPush = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: routerPush, replace: vi.fn() }),
  usePathname: () => '/projects',
}));

type OnSend = (message: string, attachments?: unknown, nodes?: unknown, extra?: unknown) => unknown;
const composer = vi.hoisted(() => ({ onSend: null as OnSend | null }));
vi.mock('@/chat/components/chat-input', () => ({
  ChatInput: ({ onSend }: { onSend: OnSend }) => {
    composer.onSend = onSend;
    return <div data-testid="composer" />;
  },
}));
vi.mock('@/chat/project-api', () => ({
  ProjectApi: {
    get: vi.fn().mockResolvedValue({ id: 'p1', name: 'Bug bash', role: 'owner', instructions: '', tools: [] }),
    listConversations: vi.fn().mockResolvedValue({ pagination: { totalCount: 0 } }),
  },
}));
vi.mock('@/chat/hooks/use-project-scope-hydration', () => ({ useProjectScopeHydration: () => {} }));
vi.mock('@/chat/utils/fetch-models-for-context', () => ({ fetchModelsForContext: () => Promise.resolve() }));
vi.mock('@/app/components/share', () => ({ ShareSidebar: () => null }));
vi.mock('../settings-panel', () => ({ ProjectSettingsPanel: () => null }));
vi.mock('../chat-defaults-card', () => ({ ChatDefaultsCard: () => null }));
vi.mock('../about-card', () => ({ AboutCard: () => null }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

import { ProjectWorkspaceRedesigned } from '../project-workspace-redesigned';
import { usePendingChatStore } from '@/lib/store/pending-chat-store';

beforeEach(() => {
  usePendingChatStore.setState({ pending: null });
  composer.onSend = null;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('the project workspace composer', () => {
  it('hands the agent it mentions to the chat page with the question', async () => {
    render(
      <Theme>
        <ProjectWorkspaceRedesigned projectId="p1" />
      </Theme>,
    );
    await screen.findByTestId('composer');
    const agent = { type: 'agent', id: '08623d5a-e271-49b8-b4d0-821c8010b07b' };

    composer.onSend!(`<@agent:${agent.id}> what are the new bug bash sessions?`, undefined, undefined, { mentions: [agent] });

    await waitFor(() => expect(routerPush).toHaveBeenCalledWith('/chat/?projectId=p1'));
    expect(usePendingChatStore.getState().pending).toMatchObject({
      message: `<@agent:${agent.id}> what are the new bug bash sessions?`,
      extra: { mentions: [agent] },
    });
  });
});
