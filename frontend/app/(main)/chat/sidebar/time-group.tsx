'use client';

import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { GeneratingTitleItem } from './chat-section-element';
import { ChatListRow, useRowSelect } from './chat-list-row';
import { ELEMENT_HEIGHT } from '@/app/components/sidebar';
import type { PendingConversation } from '@/chat/store';
import {
  groupByTime,
  getNonEmptyGroups as getNonEmptyGroupsGeneric,
  TIME_GROUP_KEYS,
  type TimeGroupKey,
} from '@/lib/utils/group-by-time';
import { getConversationLastActivityMs } from '@/lib/utils/conversation-activity';
import { splitPinned } from '@/chat/utils/pinned-conversations';
import type { Conversation } from '@/chat/types';

// Re-export for consumers that still import from here
export { TIME_GROUP_KEYS, type TimeGroupKey };

/** A time bucket, or the owner's pinned chats shown above all of them. */
export type SidebarGroupKey = TimeGroupKey | 'Pinned';

/** Maps the fixed group keys to their i18n translation key */
const TIME_GROUP_I18N: Record<SidebarGroupKey, string> = {
  'Pinned': 'chat.pinned',
  'Today': 'timeGroup.today',
  'Yesterday': 'timeGroup.yesterday',
  'Previous 7 Days': 'timeGroup.previous7Days',
  'Older': 'timeGroup.older',
};

/**
 * Groups conversations into the viewer's local-day buckets, relative to `now` (default: the current time).
 * Delegates to the shared `groupByTime` utility.
 */
export function groupConversationsByTime(
  conversations: Conversation[],
  now?: number,
): Record<TimeGroupKey, Conversation[]> {
  return groupByTime(conversations, (conv) => getConversationLastActivityMs(conv), { now });
}

/**
 * Returns only the non-empty group entries in display order.
 */
export function getNonEmptyGroups(
  groups: Record<TimeGroupKey, Conversation[]>
): Array<[TimeGroupKey, Conversation[]]> {
  return getNonEmptyGroupsGeneric(groups, getConversationLastActivityMs);
}

/**
 * The owner's sidebar groups: a "Pinned" group first (when anything is
 * pinned), then the rest by time. A pinned chat appears only under "Pinned",
 * not again under the day it was last active.
 */
export function groupConversationsWithPinned(
  conversations: Conversation[],
  now?: number,
): Array<[SidebarGroupKey, Conversation[]]> {
  const { pinned, rest } = splitPinned(conversations);
  const timed = getNonEmptyGroups(groupConversationsByTime(rest, now));
  return pinned.length > 0 ? [['Pinned', pinned], ...timed] : timed;
}

// ========================================
// Components
// ========================================

interface TimeGroupProps {
  label: SidebarGroupKey;
  conversations: Conversation[];
  currentConversationId: string | null;
  onSelectConversation: (id: string) => void;
  /** Pending conversations to show as clickable "Generating Title…" shimmers */
  pendingConversations?: PendingConversation[];
  /** Agent sidebar: use agent delete API and hide rename/archive on rows */
  agentId?: string;
}

/**
 * TimeGroup — renders a time-period sub-heading ("Today", "Yesterday", …)
 * followed by the list of chat items in that period.
 */
export function TimeGroup({
  label,
  conversations,
  currentConversationId,
  onSelectConversation,
  pendingConversations = [],
  agentId,
}: TimeGroupProps) {
  const { t } = useTranslation();
  const select = useRowSelect(onSelectConversation);
  const pendingSortedNewestFirst = [...pendingConversations].sort(
    (a, b) => b.createdAt - a.createdAt
  );
  return (
    <Flex direction="column">
      {/* Sub-heading: a quiet, non-interactive 32px label row over the group's rows */}
      <Flex
        align="center"
        data-sidebar-sub-label=""
        style={{ height: ELEMENT_HEIGHT, padding: '0 var(--space-3)', flexShrink: 0 }}
      >
        <Text size="1" weight="regular" style={{ color: 'var(--text-secondary)' }}>
          {t(TIME_GROUP_I18N[label])}
        </Text>
      </Flex>

      {/* Chat items: rows sit flush, one 36px row after another */}
      <Flex direction="column">
        {pendingSortedNewestFirst.map((pending) => (
          <GeneratingTitleItem key={pending.slotId} slotId={pending.slotId} />
        ))}
        {conversations.map((conv) => (
          <ChatListRow
            key={conv.id}
            conversation={conv}
            isActive={currentConversationId === conv.id}
            agentId={agentId}
            select={select}
          />
        ))}
      </Flex>
    </Flex>
  );
}
