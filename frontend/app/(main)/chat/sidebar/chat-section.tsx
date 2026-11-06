'use client';

import { useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { ChatSectionHeader } from './chat-section-header';
import { ListSkeleton } from '@/app/components/ui/list-skeleton';
import { useDeferredLoading } from '@/app/components/ui/use-deferred-loading';
import { StartChatButton, GeneratingTitleItem } from './chat-section-element';
import { ChatListRow, useRowSelect } from './chat-list-row';
import { SidebarItem } from './sidebar-item';
import { TimeGroup } from './time-group';
import type { Conversation } from '@/chat/types';
import type { PendingConversation } from '@/chat/store';
import type { SidebarGroupKey } from './time-group';

interface ChatSectionBaseProps {
  /** Section label shown in the header. Omit to suppress the header entirely. */
  title?: string;
  isLoading: boolean;
  hasError: boolean;
  currentConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onAdd?: () => void;
  /** The +'s accessible name; defaults to "New chat". */
  addLabel?: string;
  onNewChat: () => void;
  skeletonCount: number;
  /** When true, the section grows to fill available space and scrolls */
  isScrollable?: boolean;
  /** Show "⋯ More" overflow button at the bottom */
  hasMore?: boolean;
  /** Called when "More" button is clicked */
  onMore?: () => void;
  /** Pending conversations to show as "Generating Title…" shimmers first (in the Today group when time-grouped) */
  pendingConversations?: PendingConversation[];
  /** Empty-state text; defaults to "No chats yet". `null`: no line at all. */
  emptyStateText?: string | null;
  /** Agent sidebar: agent-scoped row actions (delete only) */
  agentId?: string;
  /** When true, a chevron toggle is shown and the section body can be collapsed */
  isCollapsible?: boolean;
  /** Initial collapsed state (defaults to false) */
  defaultCollapsed?: boolean;
  /**
   * `rail`: a group of the main chat rail. A 40px heading whose title is plain text, an 8px gap
   * before the first row, and a one-line empty state in medium weight.
   */
  variant?: 'default' | 'rail';
}

/**
 * Props when time groups are provided (Your Chats mode).
 */
interface TimeGroupedSectionProps extends ChatSectionBaseProps {
  timeGroups: Array<[SidebarGroupKey, Conversation[]]>;
  conversations?: never;
}

/**
 * Props when flat conversation list is provided (Shared Chats mode).
 */
interface FlatSectionProps extends ChatSectionBaseProps {
  conversations: Conversation[];
  timeGroups?: never;
}

type ChatSectionProps = TimeGroupedSectionProps | FlatSectionProps;

/**
 * A single chat section — renders a header plus either a flat list
 * or time-grouped conversations, with loading/error/empty states
 * and an optional "More" overflow button.
 */
export function ChatSection({
  title,
  conversations,
  timeGroups,
  isLoading,
  hasError,
  currentConversationId,
  onSelectConversation,
  onAdd,
  addLabel,
  onNewChat,
  skeletonCount,
  isScrollable = false,
  hasMore = false,
  onMore,
  pendingConversations = [],
  emptyStateText,
  agentId,
  isCollapsible = false,
  defaultCollapsed = false,
  variant = 'default',
}: ChatSectionProps) {
  const { t } = useTranslation();
  const select = useRowSelect(onSelectConversation);
  const [isCollapsed, setIsCollapsed] = useState(defaultCollapsed);
  const skeleton = useDeferredLoading(isLoading);
  const showSkeleton = isLoading || skeleton.visible;

  const isTimeGrouped = !!timeGroups;
  const showGenerating = pendingConversations.length > 0;
  const isEmpty = isTimeGrouped
    ? timeGroups.length === 0 && !showGenerating
    : !conversations || conversations.length === 0;
  const isRail = variant === 'rail';
  // A chat still being written is today's, so it gets a Today group even when no listed chat is from today.
  const groups =
    timeGroups && showGenerating && !timeGroups.some(([label]) => label === 'Today')
      ? withTodayGroup(timeGroups)
      : timeGroups;

  return (
    <Flex
      direction="column"
      gap={isRail ? '2' : undefined}
      style={isScrollable && !isCollapsed ? { flex: 1, minHeight: 0 } : undefined}
    >
      {(title || onAdd || isCollapsible) && (
        <ChatSectionHeader
          title={title ?? ''}
          size={isRail ? 'rail' : 'default'}
          onAdd={onAdd}
          addAriaLabel={onAdd ? addLabel ?? t('chat.newChat') : undefined}
          onTitleClick={!isRail && !isCollapsible && hasMore ? onMore : undefined}
          isCollapsed={isCollapsible ? isCollapsed : undefined}
          onToggleCollapse={isCollapsible ? () => setIsCollapsed((c) => !c) : undefined}
        />
      )}

      {!isCollapsed && hasError ? (
        <Flex direction="column" gap="2" style={{ padding: 'var(--space-2) var(--space-3)' }}>
          <Text size="1" style={{ color: 'var(--red-11)' }}>
            {t('chat.failedToLoad')}
          </Text>
          <StartChatButton onClick={onNewChat} />
        </Flex>
      ) : !isCollapsed ? (
        <Flex
          direction="column"
          className={isScrollable ? 'no-scrollbar' : undefined}
          style={{
            ...(isScrollable ? { overflowY: 'auto', flex: 1 } : {}),
          }}
        >
          {showSkeleton ? (
            /* Skeleton loading state: the shared list skeleton, at the chat row's height */
            <ListSkeleton
              variant="sidebar-row"
              count={skeletonCount}
              statusLabel={t('nav.loadingChats')}
              exiting={skeleton.exiting}
            />
          ) : isEmpty && !showGenerating && !hasMore ? (
            /* Empty state: plain text, since the sidebar's New chat already starts one */
            emptyStateText !== null && (
              <Text
                size="1"
                weight={isRail ? 'medium' : undefined}
                style={{
                  // In the rail, in line with the section's label above it.
                  padding: isRail ? '0 var(--space-3)' : 'var(--space-2) var(--space-3)',
                  color: 'var(--text-secondary)',
                }}
              >
                {emptyStateText ?? t('chat.noChatsYet')}
              </Text>
            )
          ) : groups ? (
            /* Time-grouped list (Your Chats) */
            <Flex direction="column">
              {groups.map(([label, convs]) => (
                <TimeGroup
                  key={label}
                  label={label}
                  conversations={convs}
                  currentConversationId={currentConversationId}
                  onSelectConversation={onSelectConversation}
                  pendingConversations={label === 'Today' ? pendingConversations : undefined}
                  agentId={agentId}
                />
              ))}
            </Flex>
          ) : (
            /* Flat list: rows sit flush, one 36px row after another */
            <Flex direction="column">
              {[...pendingConversations]
                .sort((a, b) => b.createdAt - a.createdAt)
                .map((pending) => (
                  <GeneratingTitleItem key={pending.slotId} slotId={pending.slotId} />
                ))}
              {conversations!.map((conv) => (
                <ChatListRow
                  key={conv.id}
                  conversation={conv}
                  isActive={currentConversationId === conv.id}
                  agentId={agentId}
                  select={select}
                />
              ))}
            </Flex>
          )}

          {/* "⋯ More" overflow button, with the rows: under a skeleton it would show, then move down when they come */}
          {hasMore && !showSkeleton && (
            <MoreButton onClick={onMore} />
          )}
        </Flex>
      ) : null}
    </Flex>
  );
}

/** `groups` with an empty Today group where it belongs: after Pinned, before every earlier day. */
function withTodayGroup(groups: Array<[SidebarGroupKey, Conversation[]]>): Array<[SidebarGroupKey, Conversation[]]> {
  const at = groups.findIndex(([label]) => label !== 'Pinned');
  const i = at < 0 ? groups.length : at;
  return [...groups.slice(0, i), ['Today', []], ...groups.slice(i)];
}

/**
 * "⋯ More" button shown at the bottom of a section when there are
 * more chats than MAX_VISIBLE_CHATS.
 */
function MoreButton({ onClick }: { onClick?: () => void }) {
  const { t } = useTranslation();
  return (
    <SidebarItem
      icon={<MaterialIcon name="more_horiz" size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />}
      label={t('common.more')}
      onClick={onClick}
    />
  );
}
