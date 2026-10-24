'use client';

import { Badge, Flex, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

export interface EnterpriseBadgeProps {
  /** Overrides the default tooltip copy (`common.enterprise.tooltip`). */
  tooltip?: string;
}

/**
 * "Available in EE" tag for a feature that is part of the Enterprise Edition, shown where the
 * feature would be. A text label, like `BetaBadge`; never place it inside a `<button>`.
 */
export function EnterpriseBadge({ tooltip }: EnterpriseBadgeProps) {
  const { t } = useTranslation();

  return (
    <Tooltip content={tooltip ?? t('common.enterprise.tooltip')}>
      <Badge size="1" variant="soft" color="gray" style={{ gap: 4, flexShrink: 0 }}>
        <MaterialIcon name="workspace_premium" size={12} color="currentColor" />
        {t('common.enterprise.label')}
      </Badge>
    </Tooltip>
  );
}

export interface EnterpriseFeatureNoticeProps {
  /** What the feature is, e.g. "Organization approval rules". */
  title: string;
  /** One line on what it does. */
  description: string;
}

/** A section-sized stand-in for an Enterprise Edition feature: what it is, and where it is available. */
export function EnterpriseFeatureNotice({ title, description }: EnterpriseFeatureNoticeProps) {
  return (
    <Flex
      align="start"
      gap="3"
      data-testid="enterprise-feature-notice"
      style={{
        padding: 'var(--space-3)',
        border: '1px dashed var(--gray-a6)',
        borderRadius: 'var(--radius-3)',
        backgroundColor: 'var(--gray-a2)',
      }}
    >
      <Flex
        align="center"
        justify="center"
        style={{
          width: 32,
          height: 32,
          flexShrink: 0,
          borderRadius: 'var(--radius-2)',
          backgroundColor: 'var(--gray-a3)',
        }}
      >
        <MaterialIcon name="workspace_premium" size={18} color="var(--gray-11)" />
      </Flex>
      <Flex direction="column" gap="1" style={{ minWidth: 0, flex: 1 }}>
        <Flex align="center" gap="2" wrap="wrap">
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {title}
          </Text>
          <EnterpriseBadge />
        </Flex>
        <Text size="1" style={{ color: 'var(--gray-11)' }}>
          {description}
        </Text>
      </Flex>
    </Flex>
  );
}
