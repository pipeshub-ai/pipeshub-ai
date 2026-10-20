'use client';

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Checkbox, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { streamMessageForSlot } from '../../streaming';
import { buildStreamChatRequestForSlot } from '../../runtime';
import { useChatStore } from '../../store';
import { approvalArgumentLines, approvalToolLabel, toolApprovalResumeQuery, type SettledToolApproval } from '../../tool-approval';
import type { ToolApprovalDecision, ToolApprovalDetails } from '../../types';

/** Whole minutes left (rounded up), re-read every half minute; 0 once the request has expired. */
function useMinutesLeft(expiresAt: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const left = expiresAt - now;
    if (left <= 0) return;
    const timer = setTimeout(() => setNow(Date.now()), Math.min(left, 30_000));
    return () => clearTimeout(timer);
  }, [now, expiresAt]);
  return Math.max(0, Math.ceil((expiresAt - now) / 60_000));
}

interface ToolApprovalCardProps {
  approval: ToolApprovalDetails;
  /** The latest reply: an older card was already answered or passed over. */
  isLatest: boolean;
  /** The reply's row: the answer carries it on. */
  rowId?: string;
}

/** Asks the person whether a tool call may run. The call itself is saved on the server; the
 * answer carries on this same reply, sending only the approval id and the choice. Where it can't
 * be answered (expired, an older reply, a conversation shared with the viewer) it is one line. */
export function ToolApprovalCard({ approval, isLatest, rowId }: ToolApprovalCardProps) {
  const { t } = useTranslation();
  const minutesLeft = useMinutesLeft(approval.expiresAt);
  const [remember, setRemember] = useState(false);
  const [showAll, setShowAll] = useState(false);
  // Someone viewing a conversation shared with them can't send in it.
  const canSend = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.isOwner ?? null : null)) !== false;

  const tool = approvalToolLabel(approval);
  const server = approval.serverName;
  const expired = minutesLeft === 0;

  if (expired || !isLatest || !canSend) {
    const notice = expired ? 'expired' : canSend ? 'passed' : 'waiting';
    return (
      <Flex data-testid="tool-approval-card" align="start" gap="2" style={{ color: 'var(--slate-11)' }}>
        <MaterialIcon name={expired ? 'timer_off' : 'pending_actions'} size={16} color="var(--slate-9)" style={{ marginTop: 3 }} />
        <Text size="2">{t(`chat.toolApproval.notice.${notice}`, { tool, server })}</Text>
      </Flex>
    );
  }

  const lines = approval.arguments ? approvalArgumentLines(approval.arguments, { full: showAll }) : [];
  const anyCut = !showAll && lines.some((line) => line.cut);

  const answer = (decision: ToolApprovalDecision) => {
    const slotId = useChatStore.getState().activeSlotId;
    if (!slotId || useChatStore.getState().slots[slotId]?.isStreaming) return;
    const query = toolApprovalResumeQuery(decision);
    const request = buildStreamChatRequestForSlot(slotId, query, undefined, {
      approvalId: approval.approvalId,
      decision,
      ...(remember && approval.canRemember !== false ? { remember: true } : {}),
    });
    if (!request) return;
    void streamMessageForSlot(slotId, query, request, { reuseRowId: rowId });
  };

  return (
    <Box
      data-testid="tool-approval-card"
      role="group"
      aria-label={t('chat.toolApproval.needsApproval')}
      style={{
        border: '1px solid var(--amber-a6)',
        borderRadius: 'var(--radius-4)',
        backgroundColor: 'var(--amber-a2)',
        padding: 'var(--space-3) var(--space-4) var(--space-4)',
      }}
    >
      <Flex gap="3" align="start">
        <Flex
          align="center"
          justify="center"
          style={{ width: 32, height: 32, borderRadius: 'var(--radius-3)', backgroundColor: 'var(--amber-a4)', flexShrink: 0 }}
        >
          <MaterialIcon name="pending_actions" size={18} color="var(--amber-11)" />
        </Flex>
        <Box style={{ flex: 1, minWidth: 0 }}>
          <Flex align="center" gap="2" wrap="wrap">
            <Text size="3" weight="medium" title={approval.toolName} style={{ color: 'var(--slate-12)', overflowWrap: 'anywhere' }}>
              {tool}
            </Text>
            <Badge size="1" variant="soft" color="gray">{server}</Badge>
            {approval.kind === 'destructive' && (
              <Badge size="1" variant="soft" color="red">
                {t('chat.toolApproval.cantBeUndone')}
              </Badge>
            )}
          </Flex>
          <Text as="p" size="1" style={{ color: 'var(--slate-11)' }}>
            {t('chat.toolApproval.needsApproval')}
            {' · '}
            {minutesLeft <= 1 ? t('chat.toolApproval.expiresSoon') : t('chat.toolApproval.expiresIn', { count: minutesLeft })}
          </Text>
        </Box>
      </Flex>

      <Box
        mt="3"
        style={{
          border: '1px solid var(--slate-a5)',
          borderRadius: 'var(--radius-3)',
          backgroundColor: 'var(--color-panel-solid)',
          padding: 'var(--space-2) var(--space-3)',
          maxHeight: showAll ? 360 : 220,
          overflowY: 'auto',
        }}
      >
        {approval.argumentsPreview ? (
          <>
            <Text as="p" size="1" style={{ fontFamily: 'var(--code-font-family)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
              {approval.argumentsPreview}
            </Text>
            <Text as="p" size="1" mt="1" style={{ color: 'var(--slate-10)' }}>
              {t('chat.toolApproval.partial')}
            </Text>
          </>
        ) : lines.length === 0 ? (
          <Text size="1" style={{ color: 'var(--slate-10)' }}>
            {t('chat.toolApproval.noArguments')}
          </Text>
        ) : (
          <Box style={{ display: 'grid', gridTemplateColumns: 'minmax(72px, max-content) 1fr', columnGap: 'var(--space-4)', rowGap: 'var(--space-1)' }}>
            {lines.map((line) => (
              <React.Fragment key={line.name}>
                <Text size="1" style={{ color: 'var(--slate-10)', fontFamily: 'var(--code-font-family)', lineHeight: '20px' }}>
                  {line.name}
                </Text>
                <Text size="2" style={{ color: 'var(--slate-12)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', lineHeight: '20px' }}>
                  {line.value}
                </Text>
              </React.Fragment>
            ))}
          </Box>
        )}
      </Box>
      {(anyCut || showAll) && (
        <Button size="1" variant="ghost" mt="2" onClick={() => setShowAll((value) => !value)}>
          {showAll ? t('chat.toolApproval.showLess') : t('chat.toolApproval.showAll')}
        </Button>
      )}

      <Flex mt="3" align="center" gap="4" wrap="wrap">
        <Flex gap="2">
          <Button size="2" color={approval.kind === 'destructive' ? 'red' : undefined} onClick={() => answer('allow')}>
            {t('chat.toolApproval.allow')}
          </Button>
          <Button size="2" variant="soft" color="gray" onClick={() => answer('deny')}>
            {t('chat.toolApproval.deny')}
          </Button>
        </Flex>
        {approval.canRemember !== false && (
          <Text as="label" size="1" style={{ color: 'var(--slate-11)', cursor: 'pointer' }}>
            <Flex gap="2" align="center">
              <Checkbox size="1" checked={remember} onCheckedChange={(value) => setRemember(value === true)} />
              {t('chat.toolApproval.dontAskAgain')}
            </Flex>
          </Text>
        )}
      </Flex>
    </Box>
  );
}

const RECEIPT_ICON: Record<SettledToolApproval['answer'], { name: string; color: string }> = {
  allowed: { name: 'check_circle', color: 'var(--green-9)' },
  allowedNotRun: { name: 'block', color: 'var(--amber-9)' },
  declined: { name: 'block', color: 'var(--slate-9)' },
  expired: { name: 'timer_off', color: 'var(--slate-9)' },
};

/** What was answered on this reply's cards, one line each, so the answer reads in order even
 * with the activity folded away. */
export function ToolApprovalReceipts({ items }: { items: SettledToolApproval[] }) {
  const { t } = useTranslation();
  return (
    <Flex direction="column" gap="1" data-testid="tool-approval-receipts">
      {items.map((item) => (
        <Flex key={item.key} align="center" gap="2">
          <MaterialIcon name={RECEIPT_ICON[item.answer].name} size={16} color={RECEIPT_ICON[item.answer].color} />
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {t(`chat.toolApproval.receipt.${item.answer}`, { tool: item.tool })}
          </Text>
          {item.server && <Badge size="1" variant="soft" color="gray">{item.server}</Badge>}
        </Flex>
      ))}
    </Flex>
  );
}
