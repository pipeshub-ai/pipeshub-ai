'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Flex, Box, Text, TextField, IconButton } from '@radix-ui/themes';
import { usePathname } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { SecondaryPanel } from '@/app/components/sidebar';
import { ICON_SIZE_DEFAULT, SECTION_HEADER_PADDING } from '@/app/components/sidebar';
import { buildChatHref } from '@/chat/build-chat-url';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useDebouncedSearch } from '@/knowledge-base/hooks/use-debounced-search';
import { AgentsApi } from '@/app/(main)/agents/api';
import type { AgentListRecord } from '@/app/(main)/agents/types';
import { AGENTS_SIDEBAR_PAGE_SIZE } from '@/chat/constants';
import { AgentSidebarListRow } from './agent-sidebar-list-row';

interface AgentsSidebarProps {
  onBack: () => void;
}

function agentRowKey(agent: AgentListRecord): string {
  return agent.id || agent._key || String(agent.name);
}

export const AgentsSidebar = React.memo(function AgentsSidebar({ onBack }: AgentsSidebarProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const chatAgentId = searchParams.get('agentId');
  const onChatRoute = pathname === '/chat' || pathname === '/chat/';
  const { t } = useTranslation();
  const closeMobileSidebar = useMobileSidebarStore((s) => s.close);
  const isMobile = useIsMobile();

  const [searchInput, setSearchInput] = useState('');
  const debouncedSearch = useDebouncedSearch(searchInput, 300);

  const [agents, setAgents] = useState<AgentListRecord[]>([]);
  const [pagination, setPagination] = useState<{
    currentPage: number;
    hasNext: boolean;
  } | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const sentinelRef = useRef<HTMLDivElement>(null);
  const paginationRef = useRef(pagination);
  const debouncedSearchRef = useRef(debouncedSearch);
  useEffect(() => {
    paginationRef.current = pagination;
  }, [pagination]);
  useEffect(() => {
    debouncedSearchRef.current = debouncedSearch;
  }, [debouncedSearch]);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setLoadError(null);

    (async () => {
      try {
        const { agents: rows, pagination: pag } = await AgentsApi.getAgents({
          page: 1,
          limit: AGENTS_SIDEBAR_PAGE_SIZE,
          search: debouncedSearch.trim() || undefined,
        });
        if (cancelled) return;
        setAgents(rows);
        setPagination({
          currentPage: pag.currentPage,
          hasNext: pag.hasNext,
        });
      } catch {
        if (!cancelled) {
          setLoadError(t('chat.failedToLoadAgents'));
          setAgents([]);
          setPagination(null);
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [debouncedSearch, t]);

  const loadMore = useCallback(async () => {
    const pag = paginationRef.current;
    const search = debouncedSearchRef.current.trim() || undefined;
    if (!pag?.hasNext || isLoadingMore || isLoading) return;

    const nextPage = pag.currentPage + 1;
    setIsLoadingMore(true);
    try {
      const { agents: rows, pagination: nextPag } = await AgentsApi.getAgents({
        page: nextPage,
        limit: AGENTS_SIDEBAR_PAGE_SIZE,
        search,
      });
      setAgents((prev) => {
        const seen = new Set(prev.map(agentRowKey));
        const merged = [...prev];
        for (const row of rows) {
          const k = agentRowKey(row);
          if (!seen.has(k)) {
            seen.add(k);
            merged.push(row);
          }
        }
        return merged;
      });
      setPagination({
        currentPage: nextPag.currentPage,
        hasNext: nextPag.hasNext,
      });
    } catch {
      // keep list; user can scroll to retry
    } finally {
      setIsLoadingMore(false);
    }
  }, [isLoadingMore, isLoading]);

  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          loadMore();
        }
      },
      { threshold: 0.1 }
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [loadMore]);

  const handleSelect = (agent: AgentListRecord) => {
    const id = agent.id || agent._key;
    if (!id) return;
    if (isMobile) closeMobileSidebar();
    onBack();
    router.push(buildChatHref({ agentId: id }));
  };

  const handleCreateAgent = () => {
    if (isMobile) closeMobileSidebar();
    onBack();
    router.push('/agents/new');
  };

  const hasNextPage = pagination?.hasNext ?? false;

  return (
    <SecondaryPanel
      header={
        <Flex
          align="center"
          justify="between"
          style={{
            height: '100%',
            padding: SECTION_HEADER_PADDING,
            backgroundColor: 'var(--olive-1)',
          }}
        >
          <IconButton variant="ghost" color="gray" size="2" onClick={onBack} aria-label={t('common.back')}>
            <MaterialIcon name="chevron_left" size={24} color="var(--slate-11)" />
          </IconButton>
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('nav.agents')}
          </Text>
          <IconButton
            variant="ghost"
            color="gray"
            size="2"
            onClick={handleCreateAgent}
            aria-label={t('chat.newAgent')}
          >
            <MaterialIcon name="add" size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />
          </IconButton>
        </Flex>
      }
    >
      <Flex direction="column" gap="3">
        <TextField.Root
          size="2"
          placeholder={t('chat.searchAgents')}
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          style={{ width: '100%' }}
        >
          <TextField.Slot side="left">
            <MaterialIcon name="search" size={18} color="var(--slate-11)" />
          </TextField.Slot>
        </TextField.Root>

        <Flex
          direction="column"
          gap="1"
          className="no-scrollbar"
          style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}
        >
          {isLoading ? (
            <Flex align="center" justify="center" style={{ padding: 'var(--space-5) var(--space-3)' }}>
              <LottieLoader variant="loader" size={28} showLabel />
            </Flex>
          ) : loadError ? (
            <Flex align="center" justify="center" style={{ padding: 'var(--space-4) var(--space-3)' }}>
              <Text size="2" style={{ color: 'var(--red-11)', textAlign: 'center' }}>
                {loadError}
              </Text>
            </Flex>
          ) : agents.length > 0 ? (
            <>
              {agents.map((agent) => {
                const id = agent.id || agent._key;
                const label = agent.name || id || '—';
                return (
                  <AgentSidebarListRow
                    key={agentRowKey(agent)}
                    agent={agent}
                    label={label}
                    icon={<MaterialIcon name="smart_toy" size={ICON_SIZE_DEFAULT} />}
                    isActive={!!id && onChatRoute && chatAgentId === id}
                    href={id ? buildChatHref({ agentId: id }) : undefined}
                    onSelect={() => handleSelect(agent)}
                    onBeforeNavigate={() => {
                      if (isMobile) closeMobileSidebar();
                      onBack();
                    }}
                    onDeleted={(deletedId) => {
                      setAgents((prev) =>
                        prev.filter((a) => (a.id || a._key) !== deletedId)
                      );
                      if (onChatRoute && chatAgentId === deletedId) {
                        router.replace('/chat/');
                      }
                    }}
                  />
                );
              })}
              {(hasNextPage || isLoadingMore) && (
                <Box ref={sentinelRef} style={{ padding: 'var(--space-2) 0', textAlign: 'center' }}>
                  {isLoadingMore && (
                    <Flex align="center" justify="center" gap="2" style={{ padding: 'var(--space-1) 0' }}>
                      <LottieLoader variant="loader" size={20} showLabel />
                    </Flex>
                  )}
                </Box>
              )}
            </>
          ) : (
            <Flex align="center" justify="center" style={{ padding: 'var(--space-4) var(--space-3)' }}>
              <Text size="2" style={{ color: 'var(--slate-11)' }}>
                {t('chat.noAgentsFound')}
              </Text>
            </Flex>
          )}
        </Flex>
      </Flex>
    </SecondaryPanel>
  );
});
