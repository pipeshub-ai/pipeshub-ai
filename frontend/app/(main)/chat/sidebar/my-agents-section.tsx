'use client';

import React, { useEffect, useCallback } from 'react';
import useSWR from 'swr';
import { useRouter, useSearchParams } from 'next/navigation';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { useChatStore } from '@/chat/store';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { AgentsApi } from '@/app/(main)/agents/api';
import { revalidateSwrNamespace, SWR_NS, SWR_POLICY } from '@/lib/api/swr-cache';
import type { AgentListRecord } from '@/app/(main)/agents/types';
import { buildChatHref } from '@/chat/build-chat-url';
import { ChatSectionHeader } from './chat-section-header';
import { SidebarItem } from './sidebar-item';
import { AgentSidebarListRow, useUserPermission, usePermissionDeniedDialog } from '@/config';
import { ChatItemSkeleton } from './chat-section-element';
import {
  MAX_VISIBLE_AGENTS_IN_SIDEBAR,
  SIDEBAR_AGENTS_PREVIEW_FETCH_LIMIT,
} from '../constants';
import { useIsMainChatRoute } from '@/chat/hooks/use-is-main-chat-route';

const AGENTS_SKELETON_COUNT = 3;

function agentRowKey(agent: AgentListRecord): string {
  return agent.id || agent._key || String(agent.name);
}

/**
 * Main chat sidebar — short list of agents + "More agents" (opens full Agents panel).
 */
export const MyAgentsSection = React.memo(function MyAgentsSection() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const currentAgentId = searchParams.get('agentId');
  const onChatRoute = useIsMainChatRoute();
  const { t } = useTranslation();
  const canCreateAgent = useUserPermission('createAgent');
  const permissionDenied = usePermissionDeniedDialog();
  const toggleAgentsSidebar = useChatStore((s) => s.toggleAgentsSidebar);
  const openMobileSidebar = useMobileSidebarStore((s) => s.open);
  const isMobile = useIsMobile();

  const { data, error, isLoading } = useSWR(
    [SWR_NS.agentLists, 'sidebar-preview', { limit: SIDEBAR_AGENTS_PREVIEW_FETCH_LIMIT }] as const,
    // The section shows its own error text; a background refresh must not toast.
    ([, , { limit }]) => AgentsApi.getAgents({ page: 1, limit }, { suppressErrorToast: true }),
    SWR_POLICY.list60s,
  );
  const agents: AgentListRecord[] = data?.agents ?? [];
  const hasMore = agents.length > MAX_VISIBLE_AGENTS_IN_SIDEBAR || Boolean(data?.pagination?.hasNext);
  // A failed background refresh keeps the rows already shown.
  const loadError = Boolean(error) && !data;

  useEffect(() => {
    if (error) console.error('Failed to fetch agents:', error);
  }, [error]);

  const visible = agents.slice(0, MAX_VISIBLE_AGENTS_IN_SIDEBAR);

  const openAgent = () => {
    if (isMobile) useMobileSidebarStore.getState().close();
  };

  /** Keep the mobile drawer open: closing it unmounts SidebarBase before the agents panel can show. */
  const openMore = () => {
    toggleAgentsSidebar();
    if (isMobile) openMobileSidebar();
  };

  const goCreateAgent = useCallback(() => {
    if (isMobile) useMobileSidebarStore.getState().close();
    router.push('/agents/new');
  }, [isMobile, router]);

  return (
    <Flex direction="column" style={{ flexShrink: 0 }}>
      <ChatSectionHeader
        title={t('chat.myAgents')}
        onAdd={permissionDenied.guard(canCreateAgent, goCreateAgent)}
        addAriaLabel={t('chat.newAgent')}
      />
      {permissionDenied.dialog}

      {loadError ? (
        <Text
          size="1"
          style={{ padding: 'var(--space-2) var(--space-3)', color: '#ef4444' }}
        >
          {t('chat.failedToLoadAgents')}
        </Text>
      ) : (
        <Flex direction="column" gap="1">
          {isLoading ? (
            <Flex direction="column" gap="1">
              {Array.from({ length: AGENTS_SKELETON_COUNT }, (_, i) => (
                <ChatItemSkeleton key={i} />
              ))}
            </Flex>
          ) : visible.length === 0 ? (
            <Text
              size="1"
              style={{ padding: 'var(--space-2) var(--space-3)', color: 'var(--slate-10)' }}
            >
              {t('chat.noAgentsFound')}
            </Text>
          ) : (
            visible.map((agent) => {
              const id = agent.id || agent._key;
              if (!id) return null;
              const label = agent.name?.trim() || id;
              const isActive = onChatRoute && currentAgentId === id;
              return (
                <AgentSidebarListRow
                  key={agentRowKey(agent)}
                  agent={agent}
                  label={label}
                  isActive={isActive}
                  href={buildChatHref({ agentId: id })}
                  onSelect={openAgent}
                  onBeforeNavigate={() => {
                    if (isMobile) useMobileSidebarStore.getState().close();
                  }}
                  onDeleted={(deletedId) => {
                    // An edition's row may delete without AgentsApi.deleteAgent, which invalidates too.
                    revalidateSwrNamespace(SWR_NS.agentLists);
                    if (onChatRoute && currentAgentId === deletedId) {
                      router.replace('/chat/');
                    }
                  }}
                />
              );
            })
          )}

          {hasMore && !isLoading && !loadError && (
            <SidebarItem
              icon={<MaterialIcon name="apps" size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />}
              label={t('chat.moreAgents')}
              onClick={openMore}
            />
          )}
        </Flex>
      )}
    </Flex>
  );
});
