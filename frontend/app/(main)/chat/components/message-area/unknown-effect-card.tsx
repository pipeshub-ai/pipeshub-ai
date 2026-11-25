'use client';

import React from 'react';
import NextLink from 'next/link';
import { Badge, Button, Card, Flex, Link, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { approvalAction } from '../../approval-preview';
import type { ApprovalDecision, PendingApproval } from '../../types';
import { ChildAttribution } from './approval-card';
import { harnessHttpCode } from '@/lib/contracts/harness/codes';
import { MarkedText } from './display-safety';
import { workspacePageLinks } from '@/config';

export interface UnknownEffectCardProps {
  effect: PendingApproval;
  /** Absent: the card only shows what became of it. */
  onDecide?: (decision: Extract<ApprovalDecision, 'answered' | 'denied'>) => void;
}

const REFUSAL_KEYS: Record<string, string> = {
  [harnessHttpCode('NOT_ALLOWED_TO_DECIDE')]: 'unknownEffectCard.refusedNotAllowed',
  [harnessHttpCode('INTERACTION_NOT_OPEN')]: 'unknownEffectCard.refusedClosed',
  [harnessHttpCode('RUN_CANCELLING')]: 'unknownEffectCard.refusedClosed',
};

/**
 * An action the agent may or may not have done (P2.9): the run waits for a
 * person to say which (UX3, R1 F1). The user can usually check the app the
 * action went to, so the card asks them; it never re-runs it from here, which
 * could do it twice. Where the build has a page for admins to retry it, an
 * admin also sees a link to it.
 */
export function UnknownEffectCard({ effect, onDecide }: UnknownEffectCardProps) {
  const { t } = useTranslation();
  const retryPage = workspacePageLinks['unknown-effects'];
  const retryHref = useUserStore(selectIsAdmin) === true ? retryPage : undefined;
  const { action, app } = approvalAction(effect.toolName, effect.displayName);
  const open = (effect.status === 'pending' || effect.status === 'sending') && onDecide !== undefined;
  const sending = effect.status === 'sending';
  const headingId = `unknown-effect-${effect.childRunId ?? 'run'}-${effect.interactionId}-title`;
  const whyId = `unknown-effect-${effect.childRunId ?? 'run'}-${effect.interactionId}-why`;
  const outcome =
    effect.status === 'answered'
      ? { color: 'green' as const, label: t('unknownEffectCard.markedDone') }
      : effect.status === 'denied'
        ? { color: 'gray' as const, label: t('unknownEffectCard.markedNotDone') }
        : effect.status === 'approved'
          ? { color: 'green' as const, label: t('unknownEffectCard.retried') }
          : effect.status === 'expired' || effect.status === 'closed'
            ? { color: 'gray' as const, label: t('unknownEffectCard.closed') }
            : null;

  return (
    <Card
      role="region"
      aria-labelledby={headingId}
      aria-describedby={whyId}
      data-testid="unknown-effect-card"
      data-status={effect.status}
      style={{ marginTop: 'var(--space-3)', backgroundColor: 'var(--amber-2)' }}
    >
      <Flex direction="column" gap="3">
        {effect.childRunId ? <ChildAttribution approval={effect} /> : null}
        <Flex align="center" gap="2">
          <MaterialIcon name="help" size={18} color="var(--amber-11)" />
          <Text id={headingId} size="2" weight="bold">
            <MarkedText text={app ? t('unknownEffectCard.titleIn', { action, app }) : t('unknownEffectCard.title', { action })} />
          </Text>
          {outcome ? (
            <Badge color={outcome.color} variant="soft">
              {outcome.label}
            </Badge>
          ) : null}
        </Flex>
        <Text id={whyId} size="2" color="gray">
          {app ? t('unknownEffectCard.whyIn', { app }) : t('unknownEffectCard.why')}
        </Text>
        {effect.reconcile ? (
          <Text size="2" color="gray">
            {t('unknownEffectCard.checked')}
          </Text>
        ) : null}
        {effect.argsSummary ? <Text size="2">{effect.argsSummary}</Text> : null}
        {effect.errorCode ? (
          <Text size="2" color="red" role="alert">
            {t(REFUSAL_KEYS[effect.errorCode] ?? 'unknownEffectCard.failed')}
          </Text>
        ) : null}
        {open ? (
          <Flex gap="2" justify="end" align="center" wrap="wrap">
            {retryHref !== undefined ? (
              <Link asChild size="2">
                <NextLink href={retryHref}>{t('unknownEffectCard.adminRetry')}</NextLink>
              </Link>
            ) : null}
            <Button
              variant="soft"
              color="gray"
              disabled={sending}
              onClick={() => onDecide?.('denied')}
              aria-label={t('unknownEffectCard.notDoneLabel', { action })}
              style={{ minHeight: '36px' }}
            >
              {t('unknownEffectCard.notDone')}
            </Button>
            <Button
              disabled={sending}
              onClick={() => onDecide?.('answered')}
              aria-label={t('unknownEffectCard.doneLabel', { action })}
              style={{ minHeight: '36px' }}
            >
              {t('unknownEffectCard.done')}
            </Button>
          </Flex>
        ) : null}
        {open && retryPage !== undefined && retryHref === undefined ? (
          <Text size="1" color="gray">
            {t('unknownEffectCard.adminNote')}
          </Text>
        ) : null}
      </Flex>
    </Card>
  );
}
