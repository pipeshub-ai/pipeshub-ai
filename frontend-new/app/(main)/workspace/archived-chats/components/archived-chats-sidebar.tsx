'use client';

import React, { useMemo, useState } from 'react';
import { Flex, Box, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SidebarItem } from '@/chat/sidebar/sidebar-item';
import {
  CHAT_ITEM_HEIGHT,
  SIDEBAR_WIDTH,
  HEADER_HEIGHT,
  CONTENT_PADDING,
  ICON_SIZE_DEFAULT,
  KBD_BADGE_PADDING,
} from '@/app/components/sidebar';
import { getModifierSymbol } from '@/lib/utils/platform';
import type { Conversation, AgentArchivedGroup } from '../types';

interface ArchivedChatsSidebarProps {
  conversations: Conversation[];
  agentGroups: AgentArchivedGroup[];
  /** Bumped when agent groups are re-fetched — resets per-agent "See more" page state. */
  agentGroupsEpoch: number;
  isLoading: boolean;
  isLoadingAgentGroups: boolean;
  selectedConversationId: string | null;
  selectedAgentKey: string | null;
  onSelect: (conversation: Conversation, agentKey?: string | null) => void;
  onSearchClick: () => void;
  onLoadMoreForAgent: (agentKey: string, page: number) => void;
  // Normal chats pagination
  assistantChatsTotalCount: number;
  assistantChatsHasMore: boolean;
  isLoadingMoreAssistantChats: boolean;
  onLoadMoreAssistantChats: () => void;
  // Agent groups pagination
  agentGroupsTotalCount: number;
  agentGroupsHasMore: boolean;
  isLoadingMoreAgentGroups: boolean;
  onLoadMoreAgentGroups: () => void;
}

const KbdBadge = ({ children }: { children: React.ReactNode }) => (
  <span
    style={{
      background: 'var(--olive-1)',
      border: '1px solid var(--olive-3)',
      padding: KBD_BADGE_PADDING,
      borderRadius: 'var(--radius-2)',
      fontSize: 12,
      lineHeight: '16px',
      letterSpacing: '0.04px',
      color: 'var(--slate-12)',
      fontWeight: 400,
    }}
  >
    {children}
  </span>
);

const SectionHeader = ({ label, count }: { label: string; count?: number }) => (
  <Box style={{ padding: '4px 8px 2px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
    <Text
      size="1"
      weight="medium"
      style={{
        color: 'var(--slate-9)',
        textTransform: 'uppercase',
        letterSpacing: '0.06em',
      }}
    >
      {label}
    </Text>
    {count != null && count > 0 && (
      <Text size="1" style={{ color: 'var(--slate-9)' }}>
        {count}
      </Text>
    )}
  </Box>
);

function LoadMoreButton({
  onClick,
  isLoading,
  label,
}: {
  onClick: () => void;
  isLoading: boolean;
  label: string;
}) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      disabled={isLoading}
      onClick={onClick}
      style={{
        appearance: 'none',
        border: 'none',
        background: 'transparent',
        cursor: isLoading ? 'default' : 'pointer',
        padding: '4px 8px',
        display: 'flex',
        alignItems: 'center',
        gap: 4,
        borderRadius: 'var(--radius-1)',
        opacity: isLoading ? 0.5 : 1,
        width: '100%',
      }}
    >
      <MaterialIcon name="expand_more" size={14} color="var(--accent-9)" />
      <Text size="1" style={{ color: 'var(--accent-9)' }}>
        {isLoading ? t('action.loading') : label}
      </Text>
    </button>
  );
}

interface AgentGroupSectionProps {
  group: AgentArchivedGroup;
  selectedConversationId: string | null;
  selectedAgentKey: string | null;
  onSelect: (conversation: Conversation, agentKey: string) => void;
  onLoadMore: (agentKey: string, page: number) => void;
}

function AgentGroupSection({
  group,
  selectedConversationId,
  selectedAgentKey,
  onSelect,
  onLoadMore,
}: AgentGroupSectionProps) {
  const { t } = useTranslation();
  const [isExpanded, setIsExpanded] = useState(true);
  /** Next API `page` to request (1-based). Starts at 2 because page 1 is already loaded from the grouped endpoint. */
  const [nextApiPage, setNextApiPage] = useState(2);

  const displayName = group.agentName ?? group.agentKey;

  return (
    <Box>
      {/* Agent name header — click to collapse/expand */}
      <button
        type="button"
        onClick={() => setIsExpanded((p) => !p)}
        style={{
          appearance: 'none',
          border: 'none',
          background: 'transparent',
          cursor: 'pointer',
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          gap: 4,
          padding: '4px 8px',
          borderRadius: 'var(--radius-1)',
        }}
      >
        <MaterialIcon
          name={isExpanded ? 'expand_more' : 'chevron_right'}
          size={16}
          color="var(--slate-10)"
        />
        <Text
          size="1"
          weight="medium"
          style={{
            color: 'var(--slate-10)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            flex: 1,
            textAlign: 'left',
          }}
        >
          {displayName}
        </Text>
        <Text size="1" style={{ color: 'var(--slate-9)', flexShrink: 0 }}>
          {group.totalCount}
        </Text>
      </button>

      {isExpanded && (
        <Flex direction="column" gap="0" style={{ paddingLeft: 8 }}>
          {group.conversations.map((conv) => {
            const isActive =
              selectedConversationId === conv.id &&
              selectedAgentKey === group.agentKey;
            return (
              <SidebarItem
                key={conv.id}
                isActive={isActive}
                onClick={() => onSelect(conv, group.agentKey)}
                label={
                  <Text
                    size="2"
                    style={{
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      display: 'block',
                      lineHeight: `${CHAT_ITEM_HEIGHT}px`,
                      color: isActive ? 'var(--slate-12)' : 'var(--slate-11)',
                      fontWeight: isActive ? 500 : 400,
                    }}
                  >
                    {conv.title}
                  </Text>
                }
              />
            );
          })}

          {group.hasMore && (
            <LoadMoreButton
              isLoading={group.isLoadingMore}
              label={t('workspace.archivedChats.moreChats')}
              onClick={() => {
                const pageToFetch = nextApiPage;
                setNextApiPage((p) => p + 1);
                onLoadMore(group.agentKey, pageToFetch);
              }}
            />
          )}
        </Flex>
      )}
    </Box>
  );
}

export function ArchivedChatsSidebar({
  conversations,
  agentGroups,
  agentGroupsEpoch,
  isLoading,
  isLoadingAgentGroups,
  selectedConversationId,
  selectedAgentKey,
  onSelect,
  onSearchClick,
  onLoadMoreForAgent,
  assistantChatsTotalCount,
  assistantChatsHasMore,
  isLoadingMoreAssistantChats,
  onLoadMoreAssistantChats,
  agentGroupsTotalCount,
  agentGroupsHasMore,
  isLoadingMoreAgentGroups,
  onLoadMoreAgentGroups,
}: ArchivedChatsSidebarProps) {
  const { t } = useTranslation();
  const modKey = useMemo(() => getModifierSymbol(), []);

  const hasNormal = conversations.length > 0;
  const hasAgents = agentGroups.length > 0;

  return (
    <Flex
      direction="column"
      style={{
        width: `${SIDEBAR_WIDTH}px`,
        flexShrink: 0,
        height: '100%',
        borderRight: '1px solid var(--olive-3)',
        backgroundColor: 'var(--olive-1)',
        overflow: 'hidden',
        fontFamily: 'Manrope, sans-serif',
      }}
    >
      {/* Header */}
      <Box
        style={{
          height: `${HEADER_HEIGHT}px`,
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          padding: '0 var(--space-4)',
          backgroundColor: 'var(--olive-1)',
        }}
      >
        <Text size="2" weight="medium" style={{ color: 'var(--slate-11)' }}>
          {t('workspace.archivedChats.title')}
        </Text>
      </Box>

      {/* Search Chats button */}
      <Box style={{ padding: '0 var(--space-2) var(--space-2)', flexShrink: 0 }}>
        <SidebarItem
          icon={<MaterialIcon name="search" size={ICON_SIZE_DEFAULT} />}
          label={t('nav.searchChats')}
          onClick={onSearchClick}
          rightSlot={<KbdBadge>{modKey}+K</KbdBadge>}
        />
      </Box>

      {/* List */}
      <Box
        className="no-scrollbar"
        style={{ flex: 1, overflowY: 'auto', padding: CONTENT_PADDING }}
      >
        {/* ── Normal chats ───────────────────────────────────────── */}
        {(isLoading || hasNormal) && (
          <Box style={{ marginBottom: 12 }}>
            <SectionHeader
              label={t('workspace.archivedChats.assistantChat')}
              count={assistantChatsTotalCount}
            />
            {isLoading ? (
              <Flex align="center" justify="center" style={{ paddingTop: 'var(--space-8)' }}>
                <Text size="1" style={{ color: 'var(--slate-9)' }}>
                  {t('action.loading')}
                </Text>
              </Flex>
            ) : (
              <>
                {conversations.map((conversation) => (
                  <SidebarItem
                    key={conversation.id}
                    isActive={
                      selectedConversationId === conversation.id &&
                      !selectedAgentKey
                    }
                    onClick={() => onSelect(conversation, null)}
                    label={
                      <Text
                        size="2"
                        style={{
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          display: 'block',
                          lineHeight: `${CHAT_ITEM_HEIGHT}px`,
                          color:
                            selectedConversationId === conversation.id &&
                            !selectedAgentKey
                              ? 'var(--slate-12)'
                              : 'var(--slate-11)',
                          fontWeight:
                            selectedConversationId === conversation.id &&
                            !selectedAgentKey
                              ? 500
                              : 400,
                        }}
                      >
                        {conversation.title}
                      </Text>
                    }
                  />
                ))}
                {assistantChatsHasMore && (
                  <LoadMoreButton
                    isLoading={isLoadingMoreAssistantChats}
                    label={t('workspace.archivedChats.showMore')}
                    onClick={onLoadMoreAssistantChats}
                  />
                )}
              </>
            )}
          </Box>
        )}

        {/* ── Agent chats ────────────────────────────────────────── */}
        {(isLoadingAgentGroups || hasAgents) && (
          <Box>
            <SectionHeader
              label={t('workspace.archivedChats.agents')}
              count={agentGroupsTotalCount}
            />
            {isLoadingAgentGroups ? (
              <Flex align="center" justify="center" style={{ paddingTop: 'var(--space-8)' }}>
                <Text size="1" style={{ color: 'var(--slate-9)' }}>
                  {t('action.loading')}
                </Text>
              </Flex>
            ) : (
              <Flex direction="column" gap="1" style={{ marginTop: 'var(--space-4)' }}>
                {agentGroups.map((group) => (
                  <AgentGroupSection
                    key={`${group.agentKey}-${agentGroupsEpoch}`}
                    group={group}
                    selectedConversationId={selectedConversationId}
                    selectedAgentKey={selectedAgentKey}
                    onSelect={onSelect}
                    onLoadMore={onLoadMoreForAgent}
                  />
                ))}
                {agentGroupsHasMore && (
                  <LoadMoreButton
                    isLoading={isLoadingMoreAgentGroups}
                    label={t('workspace.archivedChats.moreAgents')}
                    onClick={onLoadMoreAgentGroups}
                  />
                )}
              </Flex>
            )}
          </Box>
        )}

        {/* ── Empty state ────────────────────────────────────────── */}
        {!isLoading && !isLoadingAgentGroups && !hasNormal && !hasAgents && (
          <Flex align="center" justify="center" style={{ paddingTop: 'var(--space-8)' }}>
            <Text size="1" style={{ color: 'var(--slate-9)' }}>
              {t('workspace.archivedChats.emptyList')}
            </Text>
          </Flex>
        )}
      </Box>
    </Flex>
  );
}
