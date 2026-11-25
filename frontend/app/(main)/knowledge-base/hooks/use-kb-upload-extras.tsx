'use client';

import React, { type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useEnterprisePrompt } from '@/lib/hooks/use-enterprise-prompt';
import { EnterpriseBadge } from '@/app/components/ui/enterprise-badge';

export interface KbUploadExtras {
  /** Rendered in the upload panel above its footer. */
  panel: ReactNode;
  onUploadStart?: () => void;
  onFileSucceeded?: (recordId: string) => void;
  onUploadEnd?: () => void;
}

/** Extras for uploading into a collection. Editions plug in via config.ts. */
export function useKbUploadExtras(_kbId: string | null): KbUploadExtras {
  const { t } = useTranslation();
  const show = useEnterprisePrompt();
  return {
    panel: show ? (
      <Flex align="center" gap="2" data-testid="kb-upload-extras-notice">
        <MaterialIcon name="low_priority" size={14} color="var(--slate-11)" />
        <Text size="1" style={{ color: 'var(--slate-11)' }}>
          {t('kb.uploadExtrasNotice')}
        </Text>
        <EnterpriseBadge tooltip={t('kb.uploadExtrasNoticeEnterprise')} />
      </Flex>
    ) : null,
  };
}
