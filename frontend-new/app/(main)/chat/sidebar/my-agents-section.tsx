'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { useRouter, useSearchParams, usePathname } from 'next/navigation';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { useChatStore } from '@/chat/store';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { AgentsApi } from '@/app/(main)/agents/api';
import type { AgentListRecord } from '@/app/(main)/agents/types';
import { buildChatHref } from '@/chat/build-chat-url';
import { ChatSectionHeader } from './chat-section-header';
import { SidebarItem } from './sidebar-item';
import { AgentSidebarListRow } from './agent-sidebar-list-row';
import { ChatItemSkeleton } from './chat-section-element';
import {
  MAX_VISIBLE_AGENTS_IN_SIDEBAR,
  SIDEBAR_AGENTS_PREVIEW_FETCH_LIMIT,
} from '../constants';

const AGENTS_SKELETON_COUNT = 3;

function agentRowKey(agent: AgentListRecord): string {
  return agent.id || agent._key || String(agent.name);
}

/**
 * Main chat sidebar — short list of agents + "More agents" (opens full Agents panel).
 */
export const MyAgentsSection = React.memo(function MyAgentsSection() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const currentAgentId = searchParams.get('agentId');
  const onChatRoute = pathname === '/chat' || pathname === '/chat/';
  const { t } = useTranslation();
  const toggleAgentsSidebar = useChatStore((s) => s.toggleAgentsSidebar);
  const openMobileSidebar = useMobileSidebarStore((s) => s.open);
  const isMobile = useIsMobile();

  const [agents, setAgents] = useState<AgentListRecord[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  const load = useCallback(async () => {
    setIsLoading(true);
    setLoadError(false);
    try {
      const { agents: rows, pagination } = await AgentsApi.getAgents({
        page: 1,
        limit: SIDEBAR_AGENTS_PREVIEW_FETCH_LIMIT,
      });
      setAgents(rows);
      setHasMore(
        rows.length > MAX_VISIBLE_AGENTS_IN_SIDEBAR || Boolean(pagination?.hasNext)
      );
    } catch {
      setLoadError(true);
      setAgents([]);
      setHasMore(false);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const visible = agents.slice(0, MAX_VISIBLE_AGENTS_IN_SIDEBAR);

  const openAgent = (id: string) => {
    if (isMobile) useMobileSidebarStore.getState().close();
    router.push(buildChatHref({ agentId: id }));
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
        onAdd={goCreateAgent}
        addAriaLabel={t('chat.newAgent')}
      />

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
                  onSelect={() => openAgent(id)}
                  onBeforeNavigate={() => {
                    if (isMobile) useMobileSidebarStore.getState().close();
                  }}
                  onDeleted={(deletedId) => {
                    void load();
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
