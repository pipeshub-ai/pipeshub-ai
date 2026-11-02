'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Box } from '@radix-ui/themes';
import { toast } from '@/lib/store/toast-store';
import { answerElsewhere } from '../answer-elsewhere';
import { buildChatHref } from '../build-chat-url';
import { useOtherChatApprovals, usePendingApprovals, usePendingApprovalsSync } from '../pending-approvals-store';
import { approvalToolLabel } from '../tool-approval';
import type { PendingApprovalItem, ToolApprovalAnswer } from '../types';
import { ToolApprovalCard } from './message-area/tool-approval-card';

/** A call waiting in another of the person's chats, answered from above the composer; newest first. */
export function ComposerApprovalCard() {
  usePendingApprovalsSync();
  const { t } = useTranslation();
  const router = useRouter();
  const items = useOtherChatApprovals();
  const [index, setIndex] = useState(0);
  if (items.length === 0) return null;
  const item = items[index % items.length];
  const title = item.conversationTitle ?? t('chat.newChat');

  const openChat = (target: PendingApprovalItem) =>
    router.push(buildChatHref({ agentId: target.agentId, conversationId: target.conversationId }));

  const onAnswer = async (answer: ToolApprovalAnswer) => {
    const result = await answerElsewhere(item, answer);
    if (result === 'sent' || result === 'stale') usePendingApprovals.getState().drop(item.card.approvalId);
    if (result === 'sent') {
      const message = answer.decision === 'allow'
        ? t('chat.toolApproval.runningIn', { tool: approvalToolLabel(item.card), title })
        : t('chat.toolApproval.declinedIn', { title });
      toast.success(message, { action: { label: t('chat.toolApproval.openChat'), onClick: () => openChat(item) } });
    } else if (result === 'stale') {
      toast.info(t('chat.toolApproval.alreadyAnswered'));
    }
    return result;
  };

  return (
    <Box mb="2">
      <ToolApprovalCard
        key={item.card.approvalId}
        approval={item.card}
        isLatest
        placement="composer"
        conversationTitle={title}
        onAnswer={onAnswer}
        onOpenChat={() => openChat(item)}
        moreCount={items.length - 1}
        onMore={() => setIndex((i) => (i + 1) % items.length)}
      />
    </Box>
  );
}
