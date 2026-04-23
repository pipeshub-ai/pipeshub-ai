'use client';

import { Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { ChatSectionElement, GeneratingTitleItem } from './chat-section-element';
import { ELEMENT_HEIGHT } from '@/app/components/sidebar';
import type { PendingConversation } from '@/chat/store';
import {
  groupByTime,
  getNonEmptyGroups as getNonEmptyGroupsGeneric,
  TIME_GROUP_KEYS,
  type TimeGroupKey,
} from '@/lib/utils/group-by-time';
import { getConversationLastActivityMs } from '@/lib/utils/conversation-activity';
import type { Conversation } from '@/chat/types';

// Re-export for consumers that still import from here
export { TIME_GROUP_KEYS, type TimeGroupKey };

/** Maps the fixed TimeGroupKey to its i18n translation key */
const TIME_GROUP_I18N: Record<TimeGroupKey, string> = {
  'Today': 'timeGroup.today',
  'Yesterday': 'timeGroup.yesterday',
  'Previous 7 Days': 'timeGroup.previous7Days',
  'Older': 'timeGroup.older',
};

/**
 * Groups conversations into time buckets.
 * Delegates to the shared `groupByTime` utility.
 */
export function groupConversationsByTime(
  conversations: Conversation[]
): Record<TimeGroupKey, Conversation[]> {
  return groupByTime(conversations, (conv) => getConversationLastActivityMs(conv));
}

/**
 * Returns only the non-empty group entries in display order.
 */
export function getNonEmptyGroups(
  groups: Record<TimeGroupKey, Conversation[]>
): Array<[TimeGroupKey, Conversation[]]> {
  return getNonEmptyGroupsGeneric(groups, getConversationLastActivityMs);
}

// ========================================
// Components
// ========================================

interface TimeGroupProps {
  label: TimeGroupKey;
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
  const pendingSortedNewestFirst = [...pendingConversations].sort(
    (a, b) => b.createdAt - a.createdAt
  );
  return (
    <Flex direction="column">
      {/* Sub-heading */}
      <Flex
        align="center"
        style={{
          height: ELEMENT_HEIGHT,
          padding: '0 var(--space-3)',
        }}
      >
        <span
          style={{
            fontSize: 12,
            fontWeight: 400,
            lineHeight: 'var(--line-height-1)',
            letterSpacing: '0.04px',
            color: 'var(--slate-10)',
          }}
        >
          {t(TIME_GROUP_I18N[label])}
        </span>
      </Flex>

      {/* Chat items */}
      <Flex direction="column" gap="1">
        {pendingSortedNewestFirst.map((pending) => (
          <GeneratingTitleItem key={pending.slotId} slotId={pending.slotId} />
        ))}
        {conversations.map((conv) => (
          <ChatSectionElement
            key={conv.id}
            conversation={conv}
            isActive={currentConversationId === conv.id}
            onClick={() => onSelectConversation(conv.id)}
            agentId={agentId}
          />
        ))}
      </Flex>
    </Flex>
  );
}
