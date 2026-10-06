'use client';

import React from 'react';
import { Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { MessageAuthor } from '../../../collaboration-types';
import type { RespondingAgent } from '../../../types';
import { useChatStore } from '../../../store';
import { resolveResponder } from '../../../utils/collab-timeline';
import { useAuthorName } from './human-message';
import { MessageRow } from './message-row';

interface ReplyMessageProps {
  respondingAgent?: RespondingAgent;
  time?: string;
  /** Set when messages were posted between the question and this reply: who asked. */
  replyingTo?: MessageAuthor | null;
  meUserId: string | null;
  children: React.ReactNode;
}

/** The AI's reply as a row of its own: the responder's avatar, name and time, with the answer body inside. */
export function ReplyMessage({ respondingAgent, time, replyingTo, meUserId, children }: ReplyMessageProps) {
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
      lead={
        replyingTo !== undefined ? (
          <Text as="div" size="1" data-testid="replying-to" style={{ color: 'var(--slate-11)' }}>
            {t('chat.collab.timeline.replyingTo', { name: askerName })}
          </Text>
        ) : null
      }
    >
      {children}
    </MessageRow>
  );
}
