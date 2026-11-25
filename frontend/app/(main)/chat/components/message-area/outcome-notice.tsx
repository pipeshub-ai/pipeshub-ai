'use client';

import React, { useState } from 'react';
import { Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { budgetStopLines } from '@/config';
import { CUT_SHORT_KEYS, cutShortHintKey } from '../../cut-short';
import { outcomeLimit, type RunOutcome } from '../../run-outcome';
import type { BudgetScope } from '../../types';
import { sendAgain } from './error-next-step';

/** What the action sends: the harness carries on from what was kept, or tries the task again (D1). */
const CONTINUE_MESSAGE = 'continue';

/** The note's lines for a partial outcome, in the reader's language (WP-89, ADR-RUN-01 D1). */
function outcomeLines(
  outcome: RunOutcome,
  t: (key: string, options?: Record<string, unknown>) => string,
  budgetScope: BudgetScope | null,
  language: string,
) {
  const limit = outcomeLimit(outcome);
  const scoped =
    budgetScope !== null && limit === 'budget' ? budgetStopLines(budgetScope, t, language, new Date()) : null;
  const stopped = limit === null ? null : scoped !== null ? scoped.stopped : t(CUT_SHORT_KEYS[limit]);
  const file = outcome.file;
  const lines: string[] = [];
  let title: string;
  if (file === undefined && outcome.missing !== undefined) {
    // The file the person asked for does not exist: what stopped it, what it gathered, whether that was kept.
    title = t('chat.outcome.unwritten', { name: outcome.missing });
    lines.push(stopped ?? t('chat.outcome.endedWithout'));
    if (outcome.sources > 0) {
      lines.push(t('chat.outcome.sources', { count: outcome.sources }));
      lines.push(t(outcome.kept ? 'chat.outcome.kept' : 'chat.outcome.lost'));
    } else lines.push(t('chat.outcome.nothingGathered'));
    return { title, lines };
  }
  title = stopped ?? t('chat.outcome.partial');
  if (file !== undefined) {
    if (limit === null) lines.push(t('chat.outcome.writtenFromGathered', { name: file.name }));
    else lines.push(t(file.by === 'engine' ? 'chat.outcome.writtenByEngine' : 'chat.outcome.savedPartial', { name: file.name }));
    if (outcome.opening) lines.push(t('chat.outcome.fullReport', { name: file.name }));
  } else lines.push(t(cutShortHintKey(limit)));
  return { title, lines };
}

/**
 * Under an answer whose run ended partial (WP-89): why it stopped, what exists (the file and its state, what was
 * gathered and whether it was kept) and the one action that fits, "Continue" when there is something to continue
 * from and "Try again" when nothing was gathered. Worded here from the run's settled outcome, never from the answer.
 */
export function OutcomeNotice({
  outcome,
  budgetScope = null,
  actionable = false,
}: {
  outcome: RunOutcome;
  budgetScope?: BudgetScope | null;
  /** Only the newest reply offers the action. */
  actionable?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const [sent, setSent] = useState(false);
  if (outcome.kind !== 'partial') return null;
  const { title, lines } = outcomeLines(outcome, t, budgetScope, i18n.language || 'en-US');
  const action = outcome.action === 'none' ? null : outcome.action;
  return (
    <Flex
      role="note"
      data-testid="outcome-notice"
      data-reason={outcome.reason}
      direction="column"
      gap="1"
      style={{
        marginTop: 'var(--space-3)',
        padding: 'var(--space-2) var(--space-3)',
        borderRadius: 'var(--radius-3)',
        border: '1px solid var(--amber-a5)',
        backgroundColor: 'var(--amber-a2)',
        alignSelf: 'flex-start',
        maxWidth: '100%',
      }}
    >
      <Flex align="center" gap="1">
        <MaterialIcon name="hourglass_bottom" size={14} color="var(--amber-11)" />
        <Text size="1" weight="medium" style={{ color: 'var(--amber-11)' }}>
          {title}
        </Text>
      </Flex>
      {lines.map((line) => (
        <Text key={line} size="1" style={{ color: 'var(--slate-11)' }}>
          {line}
        </Text>
      ))}
      {actionable && action !== null ? (
        <Flex mt="1">
          <Button
            size="2"
            variant="soft"
            data-testid="outcome-action"
            data-action={action}
            disabled={sent}
            onClick={() => sendAgain(CONTINUE_MESSAGE) && setSent(true)}
            style={{ minHeight: 44 }}
          >
            <MaterialIcon name={action === 'continue' ? 'play_arrow' : 'refresh'} size={16} />
            {t(action === 'continue' ? 'chat.outcome.continue' : 'chat.outcome.retry')}
          </Button>
        </Flex>
      ) : null}
    </Flex>
  );
}
