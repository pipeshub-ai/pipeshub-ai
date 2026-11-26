'use client';

import { Badge, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

export interface BetaBadgeProps {
  /**
   * Overrides the default tooltip copy (`common.beta.tooltip`). null drops the tooltip, for a badge inside a row that
   * already has one: two tooltips on one hover would stack.
   */
  tooltip?: string | null;
}

/**
 * "Beta" badge for experimental features gated behind a feature flag.
 * Text label (not color-only) per GitLab Pajamas / Cloudscape guidance —
 * never place inside a `<button>`; render as a `rightSlot`/adjacent element.
 */
export function BetaBadge({ tooltip }: BetaBadgeProps) {
  const { t } = useTranslation();

  const badge = (
    <Badge size="1" variant="soft" color="amber">
      {t('common.beta.label')}
    </Badge>
  );
  if (tooltip === null) return badge;
  return <Tooltip content={tooltip ?? t('common.beta.tooltip')}>{badge}</Tooltip>;
}
