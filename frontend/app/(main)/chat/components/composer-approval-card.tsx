'use client';

import React, { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { toast } from '@/lib/store/toast-store';
import { answerElsewhere } from '../answer-elsewhere';
import { buildChatHref } from '../build-chat-url';
import { useOtherChatApprovals, usePendingApprovals, usePendingApprovalsSync } from '../pending-approvals-store';
import { approvalToolLabel } from '../tool-approval';
import type { PendingApprovalItem, ToolApprovalAnswer } from '../types';
import { ToolApprovalCard, type AnswerResult } from './message-area/tool-approval-card';

// Above the composer, so a toast never sits on the composer or the next card's actions.
const TOAST_PLACEMENT = { placement: 'top' } as const;

/** Re-read every half minute, for "Asked 3 min ago". */
function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

/** Calls waiting in the person's other chats. A one-line bar above the composer by default
 * (it never holds Allow or Deny, so it can't be mistaken for this chat's own card); Review opens
 * the full card. The newest shows first; once shown it stays put until it leaves the list, so a
 * new arrival only raises "+N more". */
export function ComposerApprovalCard() {
  usePendingApprovalsSync();
  const { t } = useTranslation();
  const router = useRouter();
  const now = useNow();
  const items = useOtherChatApprovals();
  const inlineOpen = usePendingApprovals((s) => s.inlineOpen > 0);
  const [shownId, setShownId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  // A refused edit comes back with the server's errors: a new card, opened on them.
  const [refusals, setRefusals] = useState<Record<string, number>>({});
  const rootRef = useRef<HTMLDivElement>(null);
  const item = items.find((i) => i.card.approvalId === shownId) ?? items[0];
  const itemId = item?.card.approvalId ?? null;
  useEffect(() => {
    if (itemId !== shownId) setShownId(itemId);
  }, [itemId, shownId]);
  // This chat's own card is the one to answer: fold away so the two are never side by side.
  useEffect(() => {
    if (inlineOpen) setExpanded(false);
  }, [inlineOpen]);
  if (!item) return null;

  const titleOf = (target: PendingApprovalItem) => target.conversationTitle ?? t('chat.newChat');
  const askedOf = (target: PendingApprovalItem) => {
    if (!target.createdAt) return null;
    const minutes = Math.floor((now - target.createdAt) / 60_000);
    return minutes < 1 ? t('chat.toolApproval.askedJustNow') : t('chat.toolApproval.askedAgo', { count: minutes });
  };
  const focusLater = (selector: string) =>
    setTimeout(() => rootRef.current?.querySelector<HTMLElement>(selector)?.focus(), 0);
  const expand = () => {
    setExpanded(true);
    focusLater('[data-approval-hide]');
  };
  const collapse = () => {
    setExpanded(false);
    focusLater('[data-approval-review]');
  };
  const showNext = () => setShownId(items[(items.indexOf(item) + 1) % items.length].card.approvalId);

  const openChat = (target: PendingApprovalItem) =>
    router.push(buildChatHref({ agentId: target.agentId, conversationId: target.conversationId }));

  const answerFor = (target: PendingApprovalItem) => async (answer: ToolApprovalAnswer): Promise<AnswerResult> => {
    const { approvalId } = target.card;
    const result = await answerElsewhere(target, answer);
    if (result === 'refused') {
      setRefusals((r) => ({ ...r, [approvalId]: (r[approvalId] ?? 0) + 1 }));
      return 'sent';
    }
    if (result === 'sent' || result === 'stale') usePendingApprovals.getState().drop(approvalId);
    const title = titleOf(target);
    if (result === 'sent') {
      const message = answer.decision === 'allow'
        ? t('chat.toolApproval.runningIn', { tool: approvalToolLabel(target.card), title })
        : answer.feedback
          ? t('chat.toolApproval.suggestionSentIn', { title })
          : t('chat.toolApproval.declinedIn', { title });
      toast.success(message, { ...TOAST_PLACEMENT, action: { label: t('chat.toolApproval.openChat'), onClick: () => openChat(target) } });
    } else if (result === 'stale') {
      toast.info(t('chat.toolApproval.alreadyAnswered'), TOAST_PLACEMENT);
    }
    return result;
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    // The card handles Esc first while it edits or holds a note, and a popover closing marks it handled.
    if (e.key !== 'Escape' || e.defaultPrevented || !expanded) return;
    e.stopPropagation();
    collapse();
  };

  return (
    <Box ref={rootRef} mb="2" className="composer-approval" onKeyDown={onKeyDown}>
      {!expanded && (
        <Flex
          className="tool-approval-card"
          data-testid="composer-approval-bar"
          role="group"
          aria-label={t('chat.toolApproval.needsApproval')}
          align="center"
          gap="2"
          px="3"
          py="2"
          style={{
            border: '1px solid var(--gray-5)',
            borderRadius: 'var(--radius-3)',
            backgroundColor: 'var(--gray-2)',
            boxShadow: 'var(--shadow-4)',
          }}
        >
          <Flex
            align="center"
            justify="center"
            style={{ width: 'var(--space-5)', height: 'var(--space-5)', borderRadius: 'var(--radius-2)', backgroundColor: 'var(--gray-4)', flexShrink: 0 }}
          >
            <ConnectorIcon type={item.card.serverName} size={14} />
          </Flex>
          <Text size="2" truncate style={{ flex: 1, minWidth: 0, color: 'var(--gray-12)' }}>
            {t('chat.toolApproval.waitingIn', { tool: approvalToolLabel(item.card), title: titleOf(item) })}
            {askedOf(item) && <Text style={{ color: 'var(--gray-11)' }}>{` · ${askedOf(item)}`}</Text>}
          </Text>
          {items.length > 1 && (
            <Button
              size="1"
              variant="ghost"
              color="gray"
              style={{ flexShrink: 0 }}
              aria-label={t('chat.toolApproval.moreWaitingLabel', { count: items.length - 1 })}
              onClick={showNext}
            >
              {t('chat.toolApproval.moreWaiting', { count: items.length - 1 })}
            </Button>
          )}
          <Button size="1" variant="ghost" color="gray" style={{ flexShrink: 0 }} onClick={() => openChat(item)}>
            {t('chat.toolApproval.openChat')}
          </Button>
          <Button data-approval-review="" size="1" variant="ghost" style={{ flexShrink: 0 }} onClick={expand}>
            {t('chat.toolApproval.review')}
          </Button>
        </Flex>
      )}
      {/* Every listed call keeps its card mounted, so an edit survives "+N more" and folding. */}
      {items.map((target) => {
        const id = target.card.approvalId;
        return (
          <Box key={`${id}:${refusals[id] ?? 0}`} hidden={!expanded || id !== item.card.approvalId}>
            <ToolApprovalCard
              approval={target.card}
              isLatest
              placement="composer"
              conversationTitle={titleOf(target)}
              asked={askedOf(target)}
              onAnswer={answerFor(target)}
              onOpenChat={() => openChat(target)}
              moreCount={items.length - 1}
              onMore={showNext}
              onHide={collapse}
            />
          </Box>
        );
      })}
    </Box>
  );
}
