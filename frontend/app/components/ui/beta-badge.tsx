'use client';

import { Badge, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

export interface BetaBadgeProps {
  /** Overrides the default tooltip copy (`common.beta.tooltip`). */
  tooltip?: string;
}

/**
 * "Beta" badge for experimental features gated behind a feature flag.
 * Text label (not color-only) per GitLab Pajamas / Cloudscape guidance —
 * never place inside a `<button>`; render as a `rightSlot`/adjacent element.
 */
export function BetaBadge({ tooltip }: BetaBadgeProps) {
  const { t } = useTranslation();

  return (
    <Tooltip content={tooltip ?? t('common.beta.tooltip')}>
      <Badge size="1" variant="soft" color="amber">
        {t('common.beta.label')}
      </Badge>
    </Tooltip>
  );
}
