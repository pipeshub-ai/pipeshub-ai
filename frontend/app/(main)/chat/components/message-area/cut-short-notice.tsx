'use client';

import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { CutShortReason } from '@/lib/contracts/harness/generated/core/enums';
import { budgetStopLines } from '@/config';
import { CUT_SHORT_KEYS, cutShortHintKey } from '../../cut-short';
import type { BudgetScope } from '../../types';

/**
 * Under an answer a limit cut short (BUDGETEND): the answer is what the run gathered before its spending limit, turn
 * limit or working time ran out, and the person can send "continue" to carry on. A budget beyond the run's own that
 * the answer names is worded by `budgetStopLines` (config.ts) where it says something.
 */
export function CutShortNotice({
  reason,
  budgetScope = null,
  now,
}: {
  reason: CutShortReason;
  budgetScope?: BudgetScope | null;
  /** For tests: the moment the notice is read. */
  now?: Date;
}) {
  const { t, i18n } = useTranslation();
  const scoped =
    reason === 'budget' && budgetScope !== null
      ? budgetStopLines(budgetScope, t, i18n.language || 'en-US', now ?? new Date())
      : null;
  return (
    <Flex
      role="note"
      data-testid="cut-short-notice"
      data-reason={reason}
      {...(scoped !== null ? { 'data-budget-scope': budgetScope } : {})}
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
          {scoped !== null ? scoped.stopped : t(CUT_SHORT_KEYS[reason])}
        </Text>
      </Flex>
      <Text size="1" style={{ color: 'var(--slate-11)' }}>
        {scoped !== null ? scoped.hint : t(cutShortHintKey(reason))}
      </Text>
    </Flex>
  );
}
