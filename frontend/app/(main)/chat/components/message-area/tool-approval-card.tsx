'use client';

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Checkbox, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { streamMessageForSlot } from '../../streaming';
import { buildStreamChatRequestForSlot } from '../../runtime';
import { useChatStore } from '../../store';
import { approvalArgumentLines, approvalToolLabel, toolApprovalResumeQuery, type SettledToolApproval } from '../../tool-approval';
import type { ApprovalStubStatus, ToolApprovalDecision, ToolApprovalDetails } from '../../types';
import { answerSlotIdNow, useAnswerSlotId, useScopedSlotId } from './answer-slot-scope';

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
  /** Who answers it instead of the conversation's owner: this person, or the viewer when `null`. */
  readOnlyFor?: { name: string | null } | null;
}

/** Asks the person whether a tool call may run. The call itself is saved on the server; the
 * answer carries on this same reply, sending only the approval id and the choice. Where it can't
 * be answered (expired, an older reply, a conversation shared with the viewer, someone else's to
 * answer) it is one line. */
export function ToolApprovalCard({ approval, isLatest, rowId, readOnlyFor }: ToolApprovalCardProps) {
  const { t } = useTranslation();
  const minutesLeft = useMinutesLeft(approval.expiresAt);
  const [remember, setRemember] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const scopedSlotId = useScopedSlotId();
  const answerSlotId = useAnswerSlotId();
  // Someone viewing a conversation shared with them can't send in it.
  const ownerRule = useChatStore((s) => (answerSlotId ? s.slots[answerSlotId]?.isOwner ?? null : null)) !== false;
  const canSend = readOnlyFor === undefined ? ownerRule : readOnlyFor === null;

  const tool = approvalToolLabel(approval);
  const server = approval.serverName;
  const expired = minutesLeft === 0;

  if (expired || !isLatest || !canSend) {
    // Someone else's card: "Ask again" is its asker's to do.
    const expiredNotice = readOnlyFor ? 'requestExpired' : 'expired';
    const notice = expired ? expiredNotice : readOnlyFor ? (isLatest ? 'waitingFor' : 'passed') : canSend ? 'passed' : 'waiting';
    const name = readOnlyFor?.name || t('askUserQuestion.unknownPerson');
    return (
      <Flex data-testid="tool-approval-card" align="start" gap="2" style={{ color: 'var(--text-secondary)' }}>
        <MaterialIcon name={expired ? 'timer_off' : 'pending_actions'} size={16} color="var(--text-secondary)" style={{ marginTop: 3 }} />
        <Text size="2">{t(`chat.toolApproval.notice.${notice}`, { tool, server, name })}</Text>
      </Flex>
    );
  }

  // A restricted card shows the whole call: what it would send is the decision.
  const restricted = approval.restricted === true;
  const lines = approval.arguments ? approvalArgumentLines(approval.arguments, { full: showAll || restricted }) : [];
  const anyCut = !showAll && lines.some((line) => line.cut);
  // Where it goes as it reads and as it is reached, so a look-alike name can't pass for another.
  const hostLabel = (h: { host: string; unicode: string }) => (h.unicode === h.host ? h.host : `${h.unicode} (${h.host})`);
  const destination = approval.hosts?.length ? approval.hosts.map(hostLabel).join(', ') : server;

  const answer = (decision: ToolApprovalDecision) => {
    const slotId = answerSlotIdNow(scopedSlotId);
    if (!slotId || useChatStore.getState().slots[slotId]?.isStreaming) return;
    const query = toolApprovalResumeQuery(decision);
    const request = buildStreamChatRequestForSlot(slotId, query, undefined, {
      approvalId: approval.approvalId,
      decision,
      ...(remember && approval.canRemember !== false && !restricted ? { remember: true } : {}),
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
            <Text size="3" weight="medium" title={approval.toolName} style={{ color: 'var(--text-primary)', overflowWrap: 'anywhere' }}>
              {tool}
            </Text>
            <Badge size="1" variant="soft" color="gray">{server}</Badge>
            {approval.kind === 'destructive' && (
              <Badge size="1" variant="soft" color="red">
                {t('chat.toolApproval.cantBeUndone')}
              </Badge>
            )}
          </Flex>
          <Text as="p" size="1" style={{ color: 'var(--text-secondary)' }}>
            {t('chat.toolApproval.needsApproval')}
            {' · '}
            {minutesLeft <= 1 ? t('chat.toolApproval.expiresSoon') : t('chat.toolApproval.expiresIn', { count: minutesLeft })}
          </Text>
        </Box>
      </Flex>

      {restricted && (
        <Box mt="3" data-testid="tool-approval-restricted">
          <Text as="p" size="2" style={{ color: 'var(--text-primary)', overflowWrap: 'anywhere' }}>
            {t('chat.toolApproval.restricted.notice', { destination })}
          </Text>
          {approval.hosts && approval.hosts.length > 0 && (
            <Flex mt="1" gap="2" align="baseline" wrap="wrap">
              <Text size="1" style={{ color: 'var(--text-secondary)' }}>
                {t('chat.toolApproval.restricted.goesTo')}
              </Text>
              {approval.hosts.map((h) => (
                <Text key={h.host} size="1" style={{ fontFamily: 'var(--code-font-family)', overflowWrap: 'anywhere' }}>
                  {hostLabel(h)}
                </Text>
              ))}
            </Flex>
          )}
        </Box>
      )}

      <Box
        mt="3"
        style={{
          border: '1px solid var(--gray-a5)',
          borderRadius: 'var(--radius-3)',
          backgroundColor: 'var(--color-panel-solid)',
          padding: 'var(--space-2) var(--space-3)',
          maxHeight: showAll || restricted ? 360 : 220,
          overflowY: 'auto',
        }}
      >
        {approval.argumentsPreview ? (
          <>
            <Text as="p" size="1" style={{ fontFamily: 'var(--code-font-family)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
              {approval.argumentsPreview}
            </Text>
            <Text as="p" size="1" mt="1" style={{ color: 'var(--slate-11)' }}>
              {t('chat.toolApproval.partial')}
            </Text>
          </>
        ) : lines.length === 0 ? (
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {t('chat.toolApproval.noArguments')}
          </Text>
        ) : (
          <Box style={{ display: 'grid', gridTemplateColumns: 'minmax(72px, max-content) 1fr', columnGap: 'var(--space-4)', rowGap: 'var(--space-1)' }}>
            {lines.map((line) => (
              <React.Fragment key={line.name}>
                <Text size="1" style={{ color: 'var(--text-secondary)', fontFamily: 'var(--code-font-family)', lineHeight: 'var(--line-height-2)' }}>
                  {line.name}
                </Text>
                <Text size="2" style={{ color: 'var(--text-primary)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', lineHeight: 'var(--line-height-2)' }}>
                  {line.value}
                </Text>
              </React.Fragment>
            ))}
          </Box>
        )}
      </Box>
      {!restricted && (anyCut || showAll) && (
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
        {approval.canRemember !== false && !restricted && (
          <Text as="label" size="1" style={{ color: 'var(--text-secondary)', cursor: 'pointer' }}>
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
  allowed: { name: 'check_circle', color: 'var(--green-11)' },
  allowedNotRun: { name: 'block', color: 'var(--amber-11)' },
  accessLost: { name: 'block', color: 'var(--amber-11)' },
  declined: { name: 'block', color: 'var(--slate-11)' },
  expired: { name: 'timer_off', color: 'var(--slate-11)' },
};

const STUB_NOTICE: Record<ApprovalStubStatus, { key: string; icon: string }> = {
  approved: { key: 'approvedBy', icon: 'check_circle' },
  not_run: { key: 'approvedNotRun', icon: 'block' },
  denied: { key: 'declinedBy', icon: 'block' },
  expired: { key: 'requestExpired', icon: 'timer_off' },
};

/** Someone else's approval card once it no longer waits, from the server's stub of it: one line, as their waiting card is. */
export function ToolApprovalStubNotice({ status, name }: { status: ApprovalStubStatus; name: string | null }) {
  const { t } = useTranslation();
  const notice = STUB_NOTICE[status];
  return (
    <Flex data-testid="tool-approval-card" align="start" gap="2" style={{ color: 'var(--text-secondary)' }}>
      <MaterialIcon name={notice.icon} size={16} color="var(--text-secondary)" style={{ marginTop: 3 }} />
      <Text size="2">{t(`chat.toolApproval.notice.${notice.key}`, { name: name || t('askUserQuestion.unknownPerson') })}</Text>
    </Flex>
  );
}

/** What was answered on this reply's cards, one line each, so the answer reads in order even
 * with the activity folded away. */
export function ToolApprovalReceipts({ items }: { items: SettledToolApproval[] }) {
  const { t } = useTranslation();
  return (
    <Flex direction="column" gap="1" data-testid="tool-approval-receipts">
      {items.map((item) => (
        <Flex key={item.key} align="center" gap="2">
          <MaterialIcon name={RECEIPT_ICON[item.answer].name} size={16} color={RECEIPT_ICON[item.answer].color} />
          <Text size="2" style={{ color: 'var(--text-secondary)' }}>
            {t(`chat.toolApproval.receipt.${item.answer}`, { tool: item.tool })}
          </Text>
          {item.server && <Badge size="1" variant="soft" color="gray">{item.server}</Badge>}
        </Flex>
      ))}
    </Flex>
  );
}
