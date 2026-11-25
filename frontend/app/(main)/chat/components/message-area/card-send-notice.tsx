'use client';

import React from 'react';
import { Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useCardSend, type SendRetryReason } from '../../card-send';

/** Literal keys, so `i18n:keys` checks each one. */
const REASON_KEYS: Record<SendRetryReason, string> = {
  restarting: 'cardSend.reason.restarting',
  offline: 'cardSend.reason.offline',
  server: 'cardSend.reason.server',
};

export interface CardSendNoticeProps {
  /** The card's send key, or a batch card's keys (the first with a send state is shown). */
  sendKey: string | readonly string[] | null | undefined;
  /** What the card sends: an answer (a question, a decision) or an engine sign-in's code. */
  what?: 'answer' | 'code';
  /** The words of a refusal the shared send keeps (a question's); without it a refusal shows nothing here. */
  refusalText?: (code: string | null) => string;
}

/**
 * CARDFAIL: what a card says while its answer is not sent: retrying ("Couldn't send your answer: PipesHub is
 * restarting. Trying again…"), given up (the same reason and Try again), or refused (why). Nothing once sent.
 */
export function CardSendNotice({ sendKey, what = 'answer', refusalText }: CardSendNoticeProps) {
  const { t } = useTranslation();
  const state = useCardSend(sendKey);
  if (!state) return null;
  if (state.phase === 'refused') {
    if (!refusalText) return null;
    return (
      <Text as="p" size="2" color="red" role="alert" data-testid="card-send-notice" data-phase="refused" style={{ margin: 0 }}>
        {refusalText(state.code)}
      </Text>
    );
  }
  const reason = t(REASON_KEYS[state.reason]);
  if (state.phase === 'retrying') {
    return (
      <Flex
        align="center"
        gap="2"
        role="status"
        aria-live="polite"
        data-testid="card-send-notice"
        data-phase="retrying"
        data-reason={state.reason}
      >
        <MaterialIcon name="sync" size={16} color="var(--amber-11)" />
        <Text size="2" style={{ color: 'var(--amber-11)' }}>
          {what === 'code' ? t('cardSend.codeRetrying', { reason }) : t('cardSend.retrying', { reason })}
        </Text>
      </Flex>
    );
  }
  return (
    <Flex
      align="center"
      justify="between"
      gap="2"
      wrap="wrap"
      role="alert"
      data-testid="card-send-notice"
      data-phase="stopped"
      data-reason={state.reason}
    >
      <Flex align="center" gap="2" style={{ minWidth: 0, flex: '1 1 14rem' }}>
        <MaterialIcon name="error_outline" size={16} color="var(--red-11)" />
        <Text size="2" color="red">
          {what === 'code' ? t('cardSend.codeStopped', { reason }) : t('cardSend.stopped', { reason })}
        </Text>
      </Flex>
      <Button type="button" size="2" variant="soft" onClick={() => state.retry()} style={{ minHeight: 44 }}>
        <MaterialIcon name="refresh" size={16} />
        {t('cardSend.tryAgain')}
      </Button>
    </Flex>
  );
}
