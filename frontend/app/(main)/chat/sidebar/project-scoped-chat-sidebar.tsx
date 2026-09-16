'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';
import { SidebarBase, ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useChatStore, selectPendingForSidebar } from '@/chat/store';
import { ProjectApi, type ProjectConversationRow } from '@/chat/project-api';
import type { ProjectDetail } from '@/chat/project-types';
import { buildChatHref } from '@/chat/build-chat-url';
import { ChatSidebarHeader } from './header';
import { ChatSidebarFooter } from './footer';
import { SidebarItem } from './sidebar-item';
import { ChatItemSkeleton, GeneratingTitleItem } from './chat-section-element';
import { groupByTime, getNonEmptyGroups, type TimeGroupKey } from '@/lib/utils/group-by-time';
import { SIDEBAR_PROJECT_CONVERSATIONS_PAGE_SIZE } from '../constants';

const YOUR_CHATS_SKELETON_COUNT = 3;

const TIME_GROUP_I18N: Record<TimeGroupKey, string> = {
  Today: 'timeGroup.today',
  Yesterday: 'timeGroup.yesterday',
  'Previous 7 Days': 'timeGroup.previous7Days',
  Older: 'timeGroup.older',
};

interface ProjectScopedChatSidebarProps {
  projectId: string;
}

/**
 * Chat sidebar when URL includes `projectId` — project name + new chat +
 * recent conversations in this project, backed by
 * `GET /api/v1/projects/:projectId/conversations`.
 *
 * Row actions (rename/archive/delete) are intentionally out of scope here —
 * conversations can still be managed from the main "Your Chats" list or the
 * agent-scoped sidebar; this view is for project-scoped navigation.
 */
export const ProjectScopedChatSidebar = React.memo(function ProjectScopedChatSidebar({
  projectId,
}: ProjectScopedChatSidebarProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const currentConversationId = searchParams?.get('conversationId') ?? null;
  const { t } = useTranslation();

  const closeMobile = useMobileSidebarStore((s) => s.close);
  const isMobileOpen = useMobileSidebarStore((s) => s.isOpen);
  const isMobile = useIsMobile();

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

  /**
   * Starting a new project chat now goes through the project workspace
   * (`/projects?projectId=…`) rather than opening a bare composer on `/chat`
   * — the workspace composer is where new project-scoped chats are started.
   */
  const handleNewProjectChat = () => {
    if (isMobile) closeMobile();
    useChatStore.getState().clearActiveSlot();
    router.push(`/projects/?projectId=${encodeURIComponent(projectId)}`);
  };

  const handleSelectConversation = () => {
    if (isMobile) closeMobile();
  };

  const pendingProjectChats = useMemo(() => {
    const convIds = new Set(conversations.map((c) => c._id));
    return selectPendingForSidebar(pendingConversations, slots, convIds, { projectId });
  }, [pendingConversations, slots, conversations, projectId]);

  const timeGroups = getNonEmptyGroups(
    groupByTime(conversations, (c) => c.lastActivityAt),
  );

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
          label={t('chat.projects.backToProject')}
          href={`/projects/?projectId=${encodeURIComponent(projectId)}`}
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

        <Flex
          direction="column"
          className="no-scrollbar"
          style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}
        >
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
            <Text
              size="1"
              style={{ padding: 'var(--space-2) var(--space-3)', color: 'var(--slate-10)' }}
            >
              {t('chat.projects.noChats')}
            </Text>
          ) : (
            <>
            {pendingProjectChats.map((p) => (
              <GeneratingTitleItem key={p.slotId} slotId={p.slotId} />
            ))}
            {timeGroups.map(([label, rows]) => (
              <Flex direction="column" key={label}>
                <Flex align="center" style={{ height: 28, padding: '0 var(--space-3)' }}>
                  <Text size="1" style={{ color: 'var(--slate-10)' }}>
                    {t(TIME_GROUP_I18N[label])}
                  </Text>
                </Flex>
                <Flex direction="column" gap="1">
                  {rows.map((row) => {
                    const href =
                      row.sessionType === 'agent' && row.agentKey
                        ? buildChatHref({ agentId: row.agentKey, conversationId: row._id })
                        : buildChatHref({ projectId, conversationId: row._id });
                    return (
                      <SidebarItem
                        key={row._id}
                        label={row.title || t('chat.generatingTitle')}
                        isActive={currentConversationId === row._id}
                        href={href}
                        onClick={handleSelectConversation}
                        textColor="var(--slate-12)"
                        fontWeight={500}
                      />
                    );
                  })}
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
