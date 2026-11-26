'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useEnterprisePrompt } from '@/lib/hooks/use-enterprise-prompt';
import { EnterpriseBadge } from '@/app/components/ui/enterprise-badge';

/** Indexing priority is an Enterprise Edition feature; admins see this line where it would be. */
export function IndexingPriorityNotice() {
  const { t } = useTranslation();
  const show = useEnterprisePrompt();
  if (!show) return null;
  return (
    <Flex align="center" gap="2" mt="3" data-testid="indexing-priority-notice">
      <MaterialIcon name="low_priority" size={14} color="var(--slate-11)" />
      <Text size="1" style={{ color: 'var(--slate-11)' }}>
        {t('workspace.connectors.indexPriorityNotice')}
      </Text>
      <EnterpriseBadge tooltip={t('workspace.connectors.indexPriorityNoticeEnterprise')} />
    </Flex>
  );
}
