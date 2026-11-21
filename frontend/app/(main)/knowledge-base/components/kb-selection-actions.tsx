'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useEnterprisePrompt } from '@/lib/hooks/use-enterprise-prompt';
import { EnterpriseBadge } from '@/app/components/ui/enterprise-badge';
import type { PageViewMode } from '../types';
import type { KbTableItem } from '../utils/kb-table-item-actions';

export interface KbSelectionActionsProps {
  selectedItems: KbTableItem[];
  pageViewMode: PageViewMode;
  /** Called by an edition's actions after they change the selected items. */
  onChanged?: () => void;
}

/** Extra actions at the end of the selection bar. Editions plug in via config.ts. */
export function KbSelectionActions(_props: KbSelectionActionsProps) {
  const { t } = useTranslation();
  const show = useEnterprisePrompt();
  if (!show) return null;
  return (
    <Flex align="center" gap="2" data-testid="kb-selection-actions-notice">
      <MaterialIcon name="low_priority" size={14} color="var(--slate-11)" />
      <Text size="1" style={{ color: 'var(--slate-11)' }}>
        {t('kb.selectionActionsNotice')}
      </Text>
      <EnterpriseBadge tooltip={t('kb.selectionActionsNoticeEnterprise')} />
    </Flex>
  );
}
