'use client';

import React, { useMemo, useState } from 'react';
import { Flex, Box, Text, DropdownMenu } from '@radix-ui/themes';
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
import { useToastStore } from '@/lib/store/toast-store';
import { Spinner } from '@/app/components/ui/spinner';
import type { Conversation, AgentArchivedGroup } from '../types';
import { ArchivedChatsApi } from '../api';
import { DeleteConfirmDialog } from './delete-confirm-dialog';

interface ArchivedChatsSidebarProps {
  conversations: Conversation[];
  agentGroups: AgentArchivedGroup[];
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
  // Post-action callbacks (called after successful API response)
  onRestored: (conversationId: string, agentKey: string | null) => void;
  onDeleted: (conversationId: string, agentKey: string | null) => void;
}

// ── Inline item menu ─────────────────────────────────────────────────────────

interface ArchivedChatItemMenuProps {
  isParentHovered: boolean;
  isRestoring: boolean;
  onOpenChange?: (open: boolean) => void;
  onRestore: () => void;
  onDelete: () => void;
}

function ArchivedChatItemMenu({
  isParentHovered,
  isRestoring,
  onOpenChange,
  onRestore,
  onDelete,
}: ArchivedChatItemMenuProps) {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const [isMeatballHovered, setIsMeatballHovered] = useState(false);
  const visible = isParentHovered || isOpen;

  const handleOpenChange = (open: boolean) => {
    setIsOpen(open);
    onOpenChange?.(open);
  };

  if (!visible) return null;

  return (
    <DropdownMenu.Root open={isOpen} onOpenChange={handleOpenChange} modal={false}>
      <DropdownMenu.Trigger>
        <button
          type="button"
          onMouseEnter={() => setIsMeatballHovered(true)}
          onMouseLeave={() => setIsMeatballHovered(false)}
          onClick={(e) => {
            e.stopPropagation();
          }}
          style={{
            appearance: 'none',
            border: 'none',
            background: isMeatballHovered || isOpen ? 'var(--olive-5)' : 'transparent',
            borderRadius: 'var(--radius-1)',
            padding: 2,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            flexShrink: 0,
          }}
        >
          <MaterialIcon name="more_horiz" size={18} color="var(--slate-11)" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Content
        side="bottom"
        align="end"
        sideOffset={4}
        style={{ minWidth: 160 }}
      >
        <DropdownMenu.Item
          disabled={isRestoring}
          onSelect={(event) => {
            event.preventDefault();
            onRestore();
          }}
        >
          <Flex align="center" gap="2">
            {isRestoring ? (
              <Spinner size={16} color="var(--slate-11)" />
            ) : (
              <MaterialIcon name="restore" size={16} color="var(--slate-11)" />
            )}
            <Text size="2" style={{ color: 'var(--slate-11)' }}>
              {isRestoring ? t('action.loading') : t('workspace.archivedChats.restore')}
            </Text>
          </Flex>
        </DropdownMenu.Item>
        <DropdownMenu.Item
          color="red"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
        >
          <Flex align="center" gap="2">
            <MaterialIcon name="delete" size={16} color="var(--red-11)" />
            <Text size="2" style={{ color: 'var(--red-11)' }}>
              {t('workspace.archivedChats.permanentlyDelete')}
            </Text>
          </Flex>
        </DropdownMenu.Item>
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

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
  hoveredId: string | null;
  openMenuId: string | null;
  restoringId: string | null;
  onHoverChange: (id: string | null) => void;
  onMenuOpenChange: (id: string | null) => void;
  onRestore: (convId: string, agentKey: string) => void;
  onDeleteRequest: (convId: string, agentKey: string) => void;
}

function AgentGroupSection({
  group,
  selectedConversationId,
  selectedAgentKey,
  onSelect,
  onLoadMore,
  hoveredId,
  openMenuId,
  restoringId,
  onHoverChange,
  onMenuOpenChange,
  onRestore,
  onDeleteRequest,
}: AgentGroupSectionProps) {
  const { t } = useTranslation();
  const [isExpanded, setIsExpanded] = useState(true);

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
          {group.pagination.totalCount}
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
                forceHighlight={openMenuId === conv.id}
                onHoverChange={(isHovered) => onHoverChange(isHovered ? conv.id : null)}
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
                rightSlot={
                  <ArchivedChatItemMenu
                    isParentHovered={hoveredId === conv.id || openMenuId === conv.id}
                    isRestoring={restoringId === conv.id}
                    onOpenChange={(open) => onMenuOpenChange(open ? conv.id : null)}
                    onRestore={() => onRestore(conv.id, group.agentKey)}
                    onDelete={() => onDeleteRequest(conv.id, group.agentKey)}
                  />
                }
              />
            );
          })}

          {group.pagination.hasNextPage && (
            <LoadMoreButton
              isLoading={group.isLoadingMore}
              label={t('workspace.archivedChats.showMore')}
              onClick={() => {
                onLoadMore(group.agentKey, group.pagination.page + 1);
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
  onRestored,
  onDeleted,
}: ArchivedChatsSidebarProps) {
  const { t } = useTranslation();
  const modKey = useMemo(() => getModifierSymbol(), []);
  const addToast = useToastStore((s) => s.addToast);

  const hasNormal = conversations.length > 0;
  const hasAgents = agentGroups.length > 0;

  // Per-item hover / menu-open tracking
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);

  // Restore / delete state
  const [restoringId, setRestoringId] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<{
    conversationId: string;
    agentKey: string | null;
  } | null>(null);
  const [isDeletingId, setIsDeletingId] = useState<string | null>(null);

  const handleRestore = async (convId: string, convAgentKey: string | null) => {
    if (restoringId) return;
    setRestoringId(convId);
    try {
      if (convAgentKey) {
        await ArchivedChatsApi.restoreAgentConversation(convAgentKey, convId);
      } else {
        await ArchivedChatsApi.restoreConversation(convId);
      }
      addToast({
        variant: 'success',
        title: t('workspace.archivedChats.restoreSuccess'),
        icon: 'restore',
      });
      onRestored(convId, convAgentKey);
    } catch {
      addToast({ variant: 'error', title: t('workspace.archivedChats.restoreError') });
    } finally {
      setRestoringId(null);
    }
  };

  const handleDeleteConfirm = async () => {
    if (!pendingDelete || isDeletingId) return;
    const { conversationId, agentKey } = pendingDelete;
    setIsDeletingId(conversationId);
    try {
      if (agentKey) {
        await ArchivedChatsApi.deleteAgentConversation(agentKey, conversationId);
      } else {
        await ArchivedChatsApi.deleteConversation(conversationId);
      }
      setPendingDelete(null);
      addToast({
        variant: 'success',
        title: t('workspace.archivedChats.deleteSuccess'),
        description: t('workspace.archivedChats.deleteSuccessDescription'),
        icon: 'check',
      });
      onDeleted(conversationId, agentKey);
    } catch {
      addToast({ variant: 'error', title: t('workspace.archivedChats.deleteError') });
    } finally {
      setIsDeletingId(null);
    }
  };

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
                {conversations.map((conversation) => {
                  const isActive =
                    selectedConversationId === conversation.id && !selectedAgentKey;
                  return (
                    <SidebarItem
                      key={conversation.id}
                      isActive={isActive}
                      onClick={() => onSelect(conversation, null)}
                      forceHighlight={openMenuId === conversation.id}
                      onHoverChange={(isHovered) =>
                        setHoveredId(isHovered ? conversation.id : null)
                      }
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
                          {conversation.title}
                        </Text>
                      }
                      rightSlot={
                        <ArchivedChatItemMenu
                          isParentHovered={
                            hoveredId === conversation.id ||
                            openMenuId === conversation.id
                          }
                          isRestoring={restoringId === conversation.id}
                          onOpenChange={(open) =>
                            setOpenMenuId(open ? conversation.id : null)
                          }
                          onRestore={() => void handleRestore(conversation.id, null)}
                          onDelete={() =>
                            setPendingDelete({
                              conversationId: conversation.id,
                              agentKey: null,
                            })
                          }
                        />
                      }
                    />
                  );
                })}
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
                    key={group.agentKey}
                    group={group}
                    selectedConversationId={selectedConversationId}
                    selectedAgentKey={selectedAgentKey}
                    onSelect={onSelect}
                    onLoadMore={onLoadMoreForAgent}
                    hoveredId={hoveredId}
                    openMenuId={openMenuId}
                    restoringId={restoringId}
                    onHoverChange={setHoveredId}
                    onMenuOpenChange={(id) => setOpenMenuId(id)}
                    onRestore={(convId, agentKey) => void handleRestore(convId, agentKey)}
                    onDeleteRequest={(convId, agentKey) =>
                      setPendingDelete({ conversationId: convId, agentKey })
                    }
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

      {/* Permanent delete confirmation */}
      <DeleteConfirmDialog
        open={pendingDelete !== null}
        isLoading={isDeletingId !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
        onConfirm={() => void handleDeleteConfirm()}
      />
    </Flex>
  );
}
