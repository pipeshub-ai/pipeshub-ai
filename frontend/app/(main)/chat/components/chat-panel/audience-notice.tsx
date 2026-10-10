'use client';

import { useId } from 'react';
import { Checkbox, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

interface AudienceNoticeProps {
  /** People who can see this chat, the owner included; unknown for people who cannot see the list. */
  participantCount?: number;
  /** Agent chats only: offer the per-turn "Share tool results" choice. */
  isAgentChat: boolean;
  shareToolResults: boolean;
  onShareToolResultsChange: (value: boolean) => void;
}

/**
 * Composer disclosure for a collaborative chat (UX-04, D3v2): who sees what you send. Files added to the
 * turn are shared with that audience; raw tool results stay private unless the box is ticked for this turn.
 */
export function AudienceNotice({
  participantCount,
  isAgentChat,
  shareToolResults,
  onShareToolResultsChange,
}: AudienceNoticeProps) {
  const { t } = useTranslation();
  const checkboxId = useId();
  const text =
    participantCount !== undefined
      ? t('chat.collab.consent.audience', { count: participantCount })
      : t('chat.collab.consent.audienceUnknown');
  return (
    <Flex
      direction="column"
      gap="1"
      data-testid="audience-notice"
      style={{ width: '100%', marginBottom: 'var(--space-2)', padding: '0 var(--space-1)' }}
    >
      <Flex align="start" gap="2">
        <MaterialIcon name="group" size={14} color="var(--slate-10)" />
        <Text size="1" style={{ color: 'var(--slate-11)' }}>
          {text}
        </Text>
      </Flex>
      {isAgentChat && (
        <Flex align="center" gap="2" as="div">
          <Checkbox
            id={checkboxId}
            size="1"
            checked={shareToolResults}
            onCheckedChange={(v) => onShareToolResultsChange(v === true)}
          />
          <Text as="label" htmlFor={checkboxId} size="1" style={{ color: 'var(--slate-11)', cursor: 'pointer' }}>
            {t('chat.collab.consent.shareToolResults')}
          </Text>
        </Flex>
      )}
    </Flex>
  );
}
