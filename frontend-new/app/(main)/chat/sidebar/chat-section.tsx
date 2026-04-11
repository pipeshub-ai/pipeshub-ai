'use client';

import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { ChatSectionHeader } from './chat-section-header';
import { ChatSectionElement, StartChatButton, ChatItemSkeleton } from './chat-section-element';
import { SidebarItem } from './sidebar-item';
import { TimeGroup } from './time-group';
import type { Conversation } from '@/chat/types';
import type { PendingConversation } from '@/chat/store';
import type { TimeGroupKey } from './time-group';

interface ChatSectionBaseProps {
  title: string;
  isLoading: boolean;
  hasError: boolean;
  currentConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onAdd?: () => void;
  onNewChat: () => void;
  skeletonCount: number;
  /** When true, the section grows to fill available space and scrolls */
  isScrollable?: boolean;
  /** Show "⋯ More" overflow button at the bottom */
  hasMore?: boolean;
  /** Called when "More" button is clicked */
  onMore?: () => void;
  /** Pending conversations to show as "Generating Title…" shimmers in Today group */
  pendingConversations?: PendingConversation[];
  /** When provided, show this text as the empty state instead of StartChatButton */
  emptyStateText?: string;
  /** Agent sidebar: agent-scoped row actions (delete only) */
  agentId?: string;
}

/**
 * Props when time groups are provided (Your Chats mode).
 */
interface TimeGroupedSectionProps extends ChatSectionBaseProps {
  timeGroups: Array<[TimeGroupKey, Conversation[]]>;
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
  onNewChat,
  skeletonCount,
  isScrollable = false,
  hasMore = false,
  onMore,
  pendingConversations = [],
  emptyStateText,
  agentId,
}: ChatSectionProps) {
  const { t } = useTranslation();
  const isTimeGrouped = !!timeGroups;
  const showGenerating = pendingConversations.length > 0;
  const isEmpty = isTimeGrouped
    ? timeGroups.length === 0 && !showGenerating
    : !conversations || conversations.length === 0;

  return (
    <Flex
      direction="column"
      style={isScrollable ? { flex: 1, minHeight: 0 } : undefined}
    >
      <ChatSectionHeader
        title={title}
        onAdd={onAdd}
        addAriaLabel={onAdd ? t('chat.newChat') : undefined}
        onTitleClick={hasMore ? onMore : undefined}
      />

      {hasError ? (
        <Flex direction="column" gap="2" style={{ padding: 'var(--space-2) var(--space-3)' }}>
          <Text size="1" style={{ color: '#ef4444' }}>
            {t('chat.failedToLoad')}
          </Text>
          <StartChatButton onClick={onNewChat} />
        </Flex>
      ) : (
        <Flex
          direction="column"
          className={isScrollable ? 'no-scrollbar' : undefined}
          style={{
            ...(isScrollable ? { overflowY: 'auto', flex: 1 } : {}),
          }}
        >
          {isLoading ? (
            /* Skeleton loading state */
            <Flex direction="column" gap="1">
              {Array.from({ length: skeletonCount }, (_, i) => (
                <ChatItemSkeleton key={i} />
              ))}
            </Flex>
          ) : isEmpty && !showGenerating ? (
            /* Empty state */
            emptyStateText ? (
              <Text
                size="1"
                style={{ padding: 'var(--space-2) var(--space-3)', color: 'var(--slate-10)' }}
              >
                {emptyStateText}
              </Text>
            ) : (
              <StartChatButton onClick={onNewChat} />
            )
          ) : isTimeGrouped ? (
            /* Time-grouped list (Your Chats) */
            <Flex direction="column">
              {timeGroups.map(([label, convs]) => (
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
              {/* If generating but no groups yet, show a standalone generating group */}
              {showGenerating && timeGroups.length === 0 && (
                <TimeGroup
                  label="Today"
                  conversations={[]}
                  currentConversationId={currentConversationId}
                  onSelectConversation={onSelectConversation}
                  pendingConversations={pendingConversations}
                  agentId={agentId}
                />
              )}
            </Flex>
          ) : (
            /* Flat list (Shared Chats) */
            <Flex direction="column" gap="1">
              {conversations!.map((conv) => (
                <ChatSectionElement
                  key={conv.id}
                  conversation={conv}
                  isActive={currentConversationId === conv.id}
                  onClick={() => onSelectConversation(conv.id)}
                  agentId={agentId}
                />
              ))}
            </Flex>
          )}

          {/* "⋯ More" overflow button */}
          {hasMore && (
            <MoreButton onClick={onMore} />
          )}
        </Flex>
      )}
    </Flex>
  );
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
