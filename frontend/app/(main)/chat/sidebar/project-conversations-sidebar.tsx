'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';
import { SidebarBase, ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useUserStore } from '@/lib/store/user-store';
import { useChatStore, selectPendingForSidebar } from '@/chat/store';
import { ProjectApi, type ProjectConversationRow } from '@/chat/project-api';
import type { ProjectDetail } from '@/chat/project-types';
import type { Conversation } from '@/chat/types';
import { ChatSidebarHeader } from './header';
import { ChatSidebarFooter } from './footer';
import { SidebarItem } from './sidebar-item';
import { ChatSectionElement, ChatItemSkeleton, GeneratingTitleItem } from './chat-section-element';
import { groupByTime, getNonEmptyGroups, type TimeGroupKey } from '@/lib/utils/group-by-time';
import { SIDEBAR_PROJECT_CONVERSATIONS_PAGE_SIZE } from '../constants';

const YOUR_CHATS_SKELETON_COUNT = 3;

const TIME_GROUP_I18N: Record<TimeGroupKey, string> = {
  Today: 'timeGroup.today',
  Yesterday: 'timeGroup.yesterday',
  'Previous 7 Days': 'timeGroup.previous7Days',
  Older: 'timeGroup.older',
};

/**
 * `GET /projects/:id/conversations` returns raw Mongo rows with no computed
 * `isOwner` — derive it from the caller's id, same signal `userId`/`initiator`
 * carry elsewhere. `projectId` is stamped back onto the row (the API omits it
 * since every row here is already scoped to this project) so
 * `ChatSectionElement`'s menu offers "Remove from project".
 */
function toConversation(
  row: ProjectConversationRow,
  currentUserId: string,
  projectId: string,
): Conversation {
  return {
    id: row._id,
    title: row.title || '',
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
    isShared: row.isShared,
    sharedWith: [],
    lastActivityAt: row.lastActivityAt,
    status: row.status,
    isOwner: row.userId === currentUserId || row.initiator === currentUserId,
    projectId: row.sessionType === 'agent' ? undefined : projectId,
    projectVisibility: row.projectVisibility,
  };
}

interface ProjectConversationsSidebarProps {
  projectId: string;
}

/**
 * Chat sidebar when the URL carries `projectId` — project name, "new chat"
 * (back to the workspace composer), and recent conversations in this
 * project, backed by `GET /api/v1/projects/:projectId/conversations`.
 *
 * Shared between `@sidebar/projects` (workspace, no `conversationId` yet)
 * and `@sidebar/chat` (once inside an actual project-scoped conversation) —
 * see `chat/sidebar/index.tsx` and `@sidebar/projects/page.tsx`.
 *
 * Rows render through `ChatSectionElement` (not a plain `SidebarItem`) so
 * rename/delete/archive/move-to-project match the main chat sidebar exactly.
 */
export const ProjectConversationsSidebar = React.memo(function ProjectConversationsSidebar({
  projectId,
}: ProjectConversationsSidebarProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const currentConversationId = searchParams?.get('conversationId') ?? null;
  const { t } = useTranslation();

  const closeMobile = useMobileSidebarStore((s) => s.close);
  const isMobileOpen = useMobileSidebarStore((s) => s.isOpen);
  const isMobile = useIsMobile();
  const currentUserId = useUserStore((s) => s.profile?.userId ?? '');

  const conversationsVersion = useChatStore((s) => s.conversationsVersion);
  const projectsVersion = useChatStore((s) => s.projectsVersion);
  const pendingConversations = useChatStore((s) => s.pendingConversations);
  const slots = useChatStore((s) => s.slots);

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [conversations, setConversations] = useState<ProjectConversationRow[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);

  const load = useCallback(async () => {
    setIsLoading(true);
    setHasError(false);
    try {
      const [detail, conv] = await Promise.all([
        ProjectApi.get(projectId),
        ProjectApi.listConversations(projectId, { page: 1, limit: SIDEBAR_PROJECT_CONVERSATIONS_PAGE_SIZE }),
      ]);
      setProject(detail);
      setConversations(conv.conversations);
    } catch {
      setHasError(true);
      setProject(null);
      setConversations([]);
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    load();
  }, [projectId, conversationsVersion, projectsVersion, load]);

  const handleBackToProject = () => {
    if (isMobile) closeMobile();
  };

  // On the workspace itself "Back to project" would link to the current page
  // (a no-op click); step out to the all-projects list instead.
  const isOnWorkspace = pathname === '/projects' || pathname === '/projects/';
  const backHref = isOnWorkspace
    ? '/projects/'
    : `/projects/?projectId=${encodeURIComponent(projectId)}`;
  const backLabel = isOnWorkspace
    ? t('chat.projects.backToProjects')
    : t('chat.projects.backToProject');

  /**
   * Starting a new project chat stays on `/chat/?projectId=…` — same page,
   * no navigation flash. Clearing the active slot resets the composer to
   * the new-chat state. If already on the matching URL, `replaceState`
   * avoids a no-op push that would add a duplicate history entry.
   */
  const handleNewProjectChat = () => {
    if (isMobile) closeMobile();
    useChatStore.getState().clearActiveSlot();
    const href = `/chat/?projectId=${encodeURIComponent(projectId)}`;
    if (pathname === '/chat' || pathname === '/chat/') {
      window.history.replaceState(null, '', href);
    } else {
      router.push(href);
    }
  };

  const handleSelectConversation = () => {
    if (isMobile) closeMobile();
  };

  const pendingProjectChats = useMemo(() => {
    const convIds = new Set(conversations.map((c) => c._id));
    return selectPendingForSidebar(pendingConversations, slots, convIds, { projectId });
  }, [pendingConversations, slots, conversations, projectId]);

  const rows = useMemo(
    () => conversations.map((row) => ({ row, conversation: toConversation(row, currentUserId, projectId) })),
    [conversations, currentUserId, projectId],
  );
  const timeGroups = getNonEmptyGroups(groupByTime(rows, ({ row }) => row.lastActivityAt));

  return (
    <SidebarBase
      header={<ChatSidebarHeader />}
      footer={<ChatSidebarFooter />}
      isMobile={isMobile}
      mobileOpen={isMobileOpen}
      onMobileClose={closeMobile}
    >
      <Flex direction="column" gap="3" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <SidebarItem
          icon={<MaterialIcon name="chevron_left" size={ICON_SIZE_DEFAULT} />}
          label={backLabel}
          href={backHref}
          onClick={handleBackToProject}
        />

        <Flex align="center" gap="2" style={{ padding: '0 var(--space-3)' }}>
          <MaterialIcon
            name="folder"
            size={ICON_SIZE_DEFAULT}
            color={project?.color || 'var(--slate-11)'}
          />
          <Text
            size="2"
            weight="bold"
            style={{
              color: 'var(--slate-12)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {project?.name ?? '…'}
          </Text>
        </Flex>

        <SidebarItem
          icon={<ChatStarIcon size={ICON_SIZE_DEFAULT} color="var(--accent-8)" />}
          label={t('chat.projects.newChatInProject')}
          onClick={handleNewProjectChat}
          textColor="var(--accent-8)"
          fontWeight={500}
        />

        <Flex direction="column" className="no-scrollbar" style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
          {hasError ? (
            <Text size="1" style={{ padding: 'var(--space-2) var(--space-3)', color: '#ef4444' }}>
              {t('chat.failedToLoad')}
            </Text>
          ) : isLoading ? (
            <Flex direction="column" gap="1">
              {Array.from({ length: YOUR_CHATS_SKELETON_COUNT }, (_, i) => (
                <ChatItemSkeleton key={i} />
              ))}
            </Flex>
          ) : timeGroups.length === 0 && pendingProjectChats.length === 0 ? (
            <Text size="1" style={{ padding: 'var(--space-2) var(--space-3)', color: 'var(--slate-10)' }}>
              {t('chat.projects.noChats')}
            </Text>
          ) : (
            <>
              {pendingProjectChats.map((p) => (
                <GeneratingTitleItem key={p.slotId} slotId={p.slotId} />
              ))}
              {timeGroups.map(([label, groupRows]) => (
                <Flex direction="column" key={label}>
                  <Flex align="center" style={{ height: 28, padding: '0 var(--space-3)' }}>
                    <Text size="1" style={{ color: 'var(--slate-10)' }}>
                      {t(TIME_GROUP_I18N[label])}
                    </Text>
                  </Flex>
                  <Flex direction="column" gap="1">
                    {groupRows.map(({ row, conversation }) => (
                      <ChatSectionElement
                        key={row._id}
                        conversation={conversation}
                        isActive={currentConversationId === row._id}
                        onClick={handleSelectConversation}
                        agentId={row.sessionType === 'agent' ? row.agentKey : undefined}
                        projectId={row.sessionType === 'agent' ? undefined : projectId}
                      />
                    ))}
                  </Flex>
                </Flex>
              ))}
            </>
          )}
        </Flex>
      </Flex>
    </SidebarBase>
  );
});
