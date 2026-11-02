'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Box } from '@radix-ui/themes';
import { toast } from '@/lib/store/toast-store';
import { answerElsewhere } from '../answer-elsewhere';
import { buildChatHref } from '../build-chat-url';
import { useOtherChatApprovals, usePendingApprovals, usePendingApprovalsSync } from '../pending-approvals-store';
import { approvalToolLabel } from '../tool-approval';
import type { PendingApprovalItem, ToolApprovalAnswer } from '../types';
import { ToolApprovalCard, type AnswerResult } from './message-area/tool-approval-card';

/** A call waiting in another of the person's chats, answered from above the composer. The newest
 * shows first; once shown it stays put until it leaves the list, so a new arrival only raises "+N more". */
export function ComposerApprovalCard() {
  usePendingApprovalsSync();
  const { t } = useTranslation();
  const router = useRouter();
  const items = useOtherChatApprovals();
  const [shownId, setShownId] = useState<string | null>(null);
  // A refused edit comes back with the server's errors: a new card, opened on them.
  const [refusals, setRefusals] = useState(0);
  const item = items.find((i) => i.card.approvalId === shownId) ?? items[0];
  const itemId = item?.card.approvalId ?? null;
  useEffect(() => {
    if (itemId !== shownId) setShownId(itemId);
  }, [itemId, shownId]);
  if (!item) return null;
  const title = item.conversationTitle ?? t('chat.newChat');

  const openChat = (target: PendingApprovalItem) =>
    router.push(buildChatHref({ agentId: target.agentId, conversationId: target.conversationId }));

  const onAnswer = async (answer: ToolApprovalAnswer): Promise<AnswerResult> => {
    const result = await answerElsewhere(item, answer);
    if (result === 'refused') {
      setRefusals((n) => n + 1);
      return 'sent';
    }
    if (result === 'sent' || result === 'stale') usePendingApprovals.getState().drop(item.card.approvalId);
    if (result === 'sent') {
      const message = answer.decision === 'allow'
        ? t('chat.toolApproval.runningIn', { tool: approvalToolLabel(item.card), title })
        : answer.feedback
          ? t('chat.toolApproval.suggestionSentIn', { title })
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
        // Only a refusal remounts it: a poll that brings the same call back keeps the edits in progress.
        key={`${item.card.approvalId}:${refusals}`}
        approval={item.card}
        isLatest
        placement="composer"
        conversationTitle={title}
        onAnswer={onAnswer}
        onOpenChat={() => openChat(item)}
        moreCount={items.length - 1}
        onMore={() => setShownId(items[(items.indexOf(item) + 1) % items.length].card.approvalId)}
      />
    </Box>
  );
}
