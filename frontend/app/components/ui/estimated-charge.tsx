'use client';

import React, { useId } from 'react';
import { Text, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

/**
 * "estimated (stopped early)" beside a cost that includes a call cut short before the provider reported its usage
 * (WP-66). The explanation is the hover title and, for screen readers, the label's description.
 */
export function EstimatedCharge({ testId }: { testId: string }) {
  const { t } = useTranslation();
  const hintId = useId();
  const hint = t('common.estimatedCharge.hint');
  return (
    <>
      <Text size="1" color="amber" title={hint} aria-describedby={hintId} data-testid={testId} style={{ whiteSpace: 'nowrap' }}>
        {t('common.estimatedCharge.label')}
      </Text>
      <VisuallyHidden id={hintId}>{hint}</VisuallyHidden>
    </>
  );
}
