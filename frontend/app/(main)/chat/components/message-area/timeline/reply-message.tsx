'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { MessageAuthor } from '../../../collaboration-types';
import type { RespondingAgent } from '../../../types';
import { useChatStore } from '../../../store';
import { resolveResponder } from '../../../utils/collab-timeline';
import { useAuthorName } from './human-message';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { MessageRow } from './message-row';

interface ReplyMessageProps {
  respondingAgent?: RespondingAgent;
  time?: string;
  /** Set when messages were posted between the question and this reply: who asked. */
  replyingTo?: MessageAuthor | null;
  meUserId: string | null;
  /** The access note, or the live "answering" line while it streams. */
  headerExtra?: React.ReactNode;
  children: React.ReactNode;
}

/** The AI's reply as a row of its own: the responder's avatar, name and time, with the answer body inside. */
export function ReplyMessage({ respondingAgent, time, replyingTo, meUserId, headerExtra, children }: ReplyMessageProps) {
  const { t } = useTranslation();
  const threadAgentId = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.threadAgentId ?? null : null));
  const agentName = useChatStore((s) => s.agentContextDisplayName);
  const responder = resolveResponder(respondingAgent, threadAgentId, agentName);
  const name =
    responder.name ??
    (responder.kind === 'assistant' ? t('chat.collab.timeline.assistantName') : t('chat.collab.attribution.respondingAgent'));
  const askerName = useAuthorName(replyingTo, meUserId);
  const handle = respondingAgent?.handle?.trim();

  return (
    <MessageRow
      nameTooltip={handle ? `@${handle}` : undefined}
      testId="reply-message"
      name={name}
      tone="ai"
      time={time}
      headerExtra={headerExtra}
      lead={
        replyingTo !== undefined ? (
          <Flex align="center" gap="1" data-testid="replying-to" style={{ color: 'var(--slate-11)', marginBottom: 'var(--space-2)' }}>
            <MaterialIcon name="reply" size={14} color="var(--slate-11)" />
            <Text size="1">{t('chat.collab.timeline.replyingTo', { name: askerName })}</Text>
          </Flex>
        ) : null
      }
    >
      {children}
    </MessageRow>
  );
}
