'use client';

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { streamMessageForSlot } from '../../streaming';
import { buildStreamChatRequestForSlot } from '../../runtime';
import { useChatStore } from '../../store';
import { approvalArgumentLines } from '../../tool-approval';
import type { ToolApprovalDecision, ToolApprovalDetails } from '../../types';

function useExpired(expiresAt: number): boolean {
  const [expired, setExpired] = useState(() => Date.now() >= expiresAt);
  useEffect(() => {
    if (expired) return;
    const timer = setTimeout(() => setExpired(true), Math.max(0, expiresAt - Date.now()));
    return () => clearTimeout(timer);
  }, [expired, expiresAt]);
  return expired;
}

interface ToolApprovalCardProps {
  approval: ToolApprovalDetails;
  /** The latest reply: an older card was already answered or passed over. */
  isLatest: boolean;
}

/** Asks the person in the chat whether a tool call may run. The call itself is saved on the
 * server; the answer is the next message, carrying only the approval id and the choice. */
export function ToolApprovalCard({ approval, isLatest }: ToolApprovalCardProps) {
  const { t } = useTranslation();
  const expired = useExpired(approval.expiresAt);
  const [answered, setAnswered] = useState(false);
  const [showAll, setShowAll] = useState(false);
  // Someone viewing a conversation shared with them can't send in it.
  const canSend = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.isOwner ?? null : null)) !== false;

  const tool = approval.toolTitle || approval.toolName;
  const server = approval.serverName;
  const lines = approval.arguments ? approvalArgumentLines(approval.arguments, { full: showAll }) : [];
  const anyCut = !showAll && lines.some((line) => line.cut);
  const answerable = isLatest && canSend && !answered && !expired;

  const answer = (decision: ToolApprovalDecision) => {
    const slotId = useChatStore.getState().activeSlotId;
    if (!slotId) return;
    const text = t(`chat.toolApproval.sent.${decision}`, { tool, server });
    const request = buildStreamChatRequestForSlot(slotId, text, undefined, {
      approvalId: approval.approvalId,
      decision,
    });
    if (!request) return;
    setAnswered(true);
    void streamMessageForSlot(slotId, text, request);
  };

  return (
    <Box
      data-testid="tool-approval-card"
      style={{
        border: '1px solid var(--amber-6)',
        borderRadius: 'var(--radius-3)',
        padding: 'var(--space-3)',
        backgroundColor: 'var(--amber-a2)',
      }}
    >
      <Flex align="center" gap="2" mb="1">
        <MaterialIcon name="pending_actions" size={16} color="var(--amber-11)" />
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('chat.toolApproval.title')}
        </Text>
        {approval.kind === 'destructive' && (
          <Badge size="1" color="red" variant="soft">
            {t('chat.toolApproval.deletesData')}
          </Badge>
        )}
      </Flex>
      <Text as="p" size="2" style={{ color: 'var(--slate-12)', overflowWrap: 'anywhere' }}>
        {t('chat.toolApproval.subtitle', { tool, server })}
        {approval.toolTitle && approval.toolTitle !== approval.toolName && (
          <Text size="1" style={{ color: 'var(--slate-10)' }}>{` (${approval.toolName})`}</Text>
        )}
      </Text>

      <Box
        mt="2"
        style={{
          maxHeight: 220,
          overflowY: 'auto',
          border: '1px solid var(--slate-4)',
          borderRadius: 'var(--radius-2)',
          padding: 'var(--space-2)',
          backgroundColor: 'var(--color-panel-solid)',
        }}
      >
        {approval.argumentsPreview ? (
          <Text
            as="p"
            size="1"
            style={{ fontFamily: 'var(--code-font-family)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
          >
            {approval.argumentsPreview}
          </Text>
        ) : lines.length === 0 ? (
          <Text size="1" style={{ color: 'var(--slate-10)' }}>
            {t('chat.toolApproval.noArguments')}
          </Text>
        ) : (
          <Flex direction="column" gap="1">
            {lines.map((line) => (
              <Text key={line.name} as="p" size="1" style={{ overflowWrap: 'anywhere' }}>
                <span style={{ color: 'var(--slate-10)' }}>{line.name}: </span>
                <span style={{ color: 'var(--slate-12)', whiteSpace: 'pre-wrap' }}>{line.value}</span>
              </Text>
            ))}
          </Flex>
        )}
      </Box>
      {(anyCut || showAll) && (
        <Button size="1" variant="ghost" mt="1" onClick={() => setShowAll((value) => !value)}>
          {showAll ? t('chat.toolApproval.showLess') : t('chat.toolApproval.showAll')}
        </Button>
      )}
      {approval.argumentsPreview && (
        <Text as="p" size="1" mt="1" style={{ color: 'var(--slate-11)' }}>
          {t('chat.toolApproval.partial')}
        </Text>
      )}

      {approval.companyAlwaysAsk && (
        <Text as="p" size="1" mt="2" style={{ color: 'var(--slate-11)' }}>
          {t('chat.toolApproval.companyAlwaysAsk')}
        </Text>
      )}

      {answerable && (
        <Flex gap="2" mt="3" wrap="wrap">
          <Button size="1" onClick={() => answer('allow_once')}>
            {t('chat.toolApproval.allowOnce')}
          </Button>
          {!approval.companyAlwaysAsk && (
            <Button size="1" variant="soft" onClick={() => answer('allow_chat')}>
              {t('chat.toolApproval.allowChat')}
            </Button>
          )}
          {approval.canAlwaysAllow && !approval.companyAlwaysAsk && (
            <Button size="1" variant="soft" onClick={() => answer('always')}>
              {t('chat.toolApproval.always')}
            </Button>
          )}
          <Button size="1" variant="soft" color="red" onClick={() => answer('deny')}>
            {t('chat.toolApproval.deny')}
          </Button>
        </Flex>
      )}

      {isLatest && canSend && !answered && expired && (
        <Text as="p" size="1" mt="2" style={{ color: 'var(--slate-11)' }}>
          {t('chat.toolApproval.expired')}
        </Text>
      )}
    </Box>
  );
}
