'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';
import { SidebarBase, ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';
import { useUserStore } from '@/lib/store/user-store';
import { useChatStore } from '@/chat/store';
import { usePendingForSidebar } from './use-pending-for-sidebar';
import { ProjectApi, type ProjectConversationRow } from '@/chat/project-api';
import { useProjectDetail } from '@/chat/hooks/use-project-detail';
import type { Conversation } from '@/chat/types';
import { ChatSidebarHeader } from './header';
import { ChatSidebarFooter } from '@/config';
import { Link } from '@/lib/navigation';
import { SidebarItem } from './sidebar-item';
import { ChatSectionElement, ChatItemSkeleton, GeneratingTitleItem } from './chat-section-element';
import { groupByTime, getNonEmptyGroups, type TimeGroupKey } from '@/lib/utils/group-by-time';
import { useLocalDay } from '@/lib/hooks/use-local-day';
import { SIDEBAR_LISTS_STALE_MS, SIDEBAR_PROJECT_CONVERSATIONS_PAGE_SIZE } from '../constants';
import { chatExtension, chatSlots } from '@/config';
import { getCurrentUserId } from '@/chat/utils/current-user-id';
import { startListRead, withHeldTitlesBy } from '@/chat/utils/held-titles';

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
    title: chatExtension.formatConversationTitle(row.title) || '',
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
    isShared: row.isShared,
    sharedWith: [],
    lastActivityAt: row.lastActivityAt,
    status: row.status,
    isOwner: row.userId === currentUserId || row.initiator === currentUserId,
    projectId: row.sessionType === 'agent' ? undefined : projectId,
    projectVisibility: row.projectVisibility,
    ...chatExtension.conversationFields(row, { ownerFromAccess: true }),
  };
}

interface ProjectConversationsSidebarProps {
  projectId: string;
}

interface ProjectRailCache {
  projectId: string;
  userId: string | null;
  conversations: ProjectConversationRow[];
  loadedAt: number;
  /** `conversationsVersion` and `projectsVersion` at the load: a bump since means the rows are behind. */
  versions: string;
}

/** The last project rail shown, so coming back to that project lists its chats at once. One project, one person. */
let projectRailCache: ProjectRailCache | null = null;
/** Loads numbered as they start, and the one whose rows the rail has: a load older than that is dropped when it lands. */
let projectRailLoads = 0;
let projectRailShownLoad = 0;

function cachedProjectRail(projectId: string): ProjectRailCache | null {
  const cache = projectRailCache;
  return cache && cache.projectId === projectId && cache.userId === getCurrentUserId() ? cache : null;
}

/**
 * A project's chats, when the URL carries `projectId` — project name, "new chat"
 * (back to the workspace composer), and recent conversations in this
 * project, backed by `GET /api/v1/projects/:projectId/conversations`.
 *
 * The middle of the chat rail inside a project-scoped conversation (`chat/sidebar/index.tsx`), and of the Projects
 * page's own rail (`ProjectConversationsSidebar`, in `@sidebar/projects`).
 *
 * Rows render through `ChatSectionElement` (not a plain `SidebarItem`) so
 * rename/delete/archive/move-to-project match the main chat sidebar exactly.
 */
export const ProjectConversationsRail = React.memo(function ProjectConversationsRail({
  projectId,
}: ProjectConversationsSidebarProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const currentConversationId = searchParams?.get('conversationId') ?? null;
  const { t } = useTranslation();

  const closeMobile = useMobileSidebarStore((s) => s.close);
  const isMobile = useIsBelowDesktop();
  const today = useLocalDay();
  const currentUserId = useUserStore((s) => s.profile?.userId ?? '');

  const conversationsVersion = useChatStore((s) => s.conversationsVersion);
  const projectsVersion = useChatStore((s) => s.projectsVersion);

  const versions = `${conversationsVersion}:${projectsVersion}`;
  const [shownProjectId, setShownProjectId] = useState(projectId);
  // One shared request per project, with the chat page.
  const { project, unavailable: projectUnavailable, gone: projectGone } = useProjectDetail(projectId);
  const [conversations, setConversations] = useState<ProjectConversationRow[]>(
    () => cachedProjectRail(projectId)?.conversations ?? [],
  );
  const [isLoading, setIsLoading] = useState(() => !cachedProjectRail(projectId));
  const [conversationsFailed, setConversationsFailed] = useState(false);
  // A project deleted or left in this tab is not a load failure: the page is about to move away.
  const hasError = conversationsFailed || (projectUnavailable && !projectGone);
  // Another project in the same rail: its own cached rows, or a skeleton, never the last project's.
  if (shownProjectId !== projectId) {
    const cached = cachedProjectRail(projectId);
    setShownProjectId(projectId);
    setConversations(cached?.conversations ?? []);
    setIsLoading(!cached);
    setConversationsFailed(false);
  }

  // The project the rail shows now: a load for another one, still out when the rail moved on, is dropped.
  const shownProject = useRef(projectId);
  useEffect(() => {
    shownProject.current = projectId;
  }, [projectId]);

  const load = useCallback(async () => {
    // Only a rail with nothing to show for this project waits on a skeleton; a refresh swaps the rows in place.
    const shown = cachedProjectRail(projectId) !== null;
    // Signed out, or someone else signed in, while this was out: the answer is not theirs to see or keep.
    const userId = getCurrentUserId();
    const read = startListRead();
    const loadNo = ++projectRailLoads;
    if (!shown) setIsLoading(true);
    setConversationsFailed(false);
    try {
      // The row shows its own "Failed to load"; the chat page owns the one toast.
      const conv = await ProjectApi.listConversations(
        projectId,
        { page: 1, limit: SIDEBAR_PROJECT_CONVERSATIONS_PAGE_SIZE },
        { suppressErrorToast: true },
      );
      // Two loads out at once can land in either order: the older one landing last would put back older rows.
      if (getCurrentUserId() !== userId || loadNo < projectRailShownLoad) return;
      projectRailShownLoad = loadNo;
      const state = useChatStore.getState();
      const stillShown = shownProject.current === projectId;
      // A rename made here may be newer than what this read found; the rail is not reloaded after it.
      const rows = withHeldTitlesBy(conv.conversations, read, (row) => row._id);
      projectRailCache = {
        projectId,
        userId,
        conversations: rows,
        loadedAt: Date.now(),
        versions: `${state.conversationsVersion}:${state.projectsVersion}`,
      };
      if (!stillShown) return;
      setConversations(rows);
    } catch {
      if (getCurrentUserId() !== userId || shownProject.current !== projectId || loadNo < projectRailShownLoad) return;
      if (!shown) {
        setConversationsFailed(true);
        setConversations([]);
      }
    } finally {
      // A dropped load leaves any skeleton to the newer one.
      if (shownProject.current === projectId && loadNo >= projectRailShownLoad) setIsLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    const cached = cachedProjectRail(projectId);
    // Fresh and not behind any bump: shown as it is.
    if (cached && cached.versions === versions && Date.now() - cached.loadedAt < SIDEBAR_LISTS_STALE_MS) return;
    load();
  }, [projectId, versions, load]);

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

  const convIds = useMemo(() => new Set(conversations.map((c) => c._id)), [conversations]);
  const pendingProjectChats = usePendingForSidebar(convIds, { projectId });

  const rows = chatSlots.useVisibleConversations(
    useMemo(
      () => conversations.map((row) => ({ row, id: row._id, conversation: toConversation(row, currentUserId, projectId) })),
      [conversations, currentUserId, projectId],
    ),
  );
  const timeGroups = getNonEmptyGroups(groupByTime(rows, ({ row }) => row.lastActivityAt, { now: today }));

  return (
    <Flex direction="column" gap="3" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
      <SidebarItem
        icon={<MaterialIcon name="chevron_left" size={ICON_SIZE_DEFAULT} />}
        label={backLabel}
        href={backHref}
        onClick={handleBackToProject}
      />

      <Link
        href={`/projects/?projectId=${encodeURIComponent(projectId)}`}
        onClick={handleBackToProject}
        className="touch-hit"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-2)',
          padding: '0 var(--space-3)',
          textDecoration: 'none',
          minWidth: 0,
        }}
      >
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
      </Link>

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
  );
});

/** A project's chats in a rail of their own: the Projects page's (`@sidebar/projects`). */
export function ProjectConversationsSidebar({ projectId }: ProjectConversationsSidebarProps) {
  const closeMobile = useMobileSidebarStore((s) => s.close);
  const isMobileOpen = useMobileSidebarStore((s) => s.isOpen);
  const isMobile = useIsBelowDesktop();
  return (
    <SidebarBase
      header={<ChatSidebarHeader />}
      footer={<ChatSidebarFooter />}
      isMobile={isMobile}
      mobileOpen={isMobileOpen}
      onMobileClose={closeMobile}
    >
      <ProjectConversationsRail projectId={projectId} />
    </SidebarBase>
  );
}
