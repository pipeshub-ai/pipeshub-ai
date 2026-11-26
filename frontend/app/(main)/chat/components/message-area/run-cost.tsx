'use client';

import React, { useId } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { EstimatedCharge } from '@/app/components/ui/estimated-charge';
import { formatSpend } from '../../durable-run-ui';
import type { RunBudgetView } from '../../types';

/** A durable run's spend from the budget ledger: USD and tokens against its caps. */
export function RunCost({ budget }: { budget: RunBudgetView }) {
  const { t } = useTranslation();
  const labelId = useId();
  const spend = formatSpend(budget);
  return (
    <Flex role="group" aria-labelledby={labelId} align="center" gap="2" wrap="wrap" data-testid="run-cost">
      <MaterialIcon name="payments" size={16} color="var(--olive-11)" />
      <Text id={labelId} size="1" weight="bold">
        {t('durableRun.cost.label')}
      </Text>
      <Text size="1" color="gray" data-testid="run-cost-usd">
        {spend.cap === null
          ? t('durableRun.cost.spent', { spent: spend.spent })
          : t('durableRun.cost.spentOf', { spent: spend.spent, cap: spend.cap })}
      </Text>
      {(budget.estimated ?? 0) > 0 ? <EstimatedCharge testId="run-cost-estimated" /> : null}
      <Text size="1" color="gray">
        {spend.tokenCap === null
          ? t('durableRun.cost.tokens', { tokens: spend.tokens })
          : t('durableRun.cost.tokensOf', { tokens: spend.tokens, cap: spend.tokenCap })}
      </Text>
      {budget.exhausted ? (
        <Text size="1" color="red">
          {t('durableRun.cost.exhausted')}
        </Text>
      ) : null}
    </Flex>
  );
}
