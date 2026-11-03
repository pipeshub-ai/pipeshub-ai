'use client';

import type React from 'react';
import { Badge, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useIsDemoSource } from '../use-demo-data';

interface DemoSourceBadgeProps {
  /** Connector instance the cited record came from. */
  connectorId?: string | null;
  /** Layout overrides from the parent, e.g. keeping it compact in a column. */
  style?: React.CSSProperties;
}

/**
 * "Demo" label for a source that belongs to the Acme Corp sample data.
 *
 * Demo records carry the system they imitate (Slack, Jira, …), so without it a
 * made-up Slack thread reads exactly like a real one once the company's own
 * data is connected. Renders nothing for any other source. Text, not colour
 * alone, and not inside a button.
 */
export function DemoSourceBadge({ connectorId, style }: DemoSourceBadgeProps) {
  const { t } = useTranslation();
  const isDemo = useIsDemoSource(connectorId);
  if (!isDemo) return null;

  return (
    <Tooltip content={t('demoData.badge.tooltip')}>
      <Badge size="1" variant="soft" color="orange" style={{ flexShrink: 0, ...style }}>
        {t('demoData.badge.label')}
      </Badge>
    </Tooltip>
  );
}
