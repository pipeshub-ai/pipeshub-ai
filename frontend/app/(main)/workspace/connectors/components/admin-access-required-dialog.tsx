'use client';

import React, { useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Dialog, Button, Flex } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { Connector } from '../types';
import { getPersonalConnectorRedirectType } from '../utils/admin-access-helpers';

export type AdminAccessDialogPhase = 'question' | 'redirect';

export interface AdminAccessRequiredDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connector: Connector | null;
  phase: AdminAccessDialogPhase;
  onPhaseChange: (phase: AdminAccessDialogPhase) => void;
  onConfirmAdmin: () => void;
}

export function AdminAccessRequiredDialog({
  open,
  onOpenChange,
  connector,
  phase,
  onPhaseChange,
  onConfirmAdmin,
}: AdminAccessRequiredDialogProps) {
  const { t } = useTranslation();
  const router = useRouter();

  const appGroup = connector?.appGroup ?? connector?.name ?? '';
  const connectorName = connector?.name ?? '';
  const personalConnectorType = connector
    ? getPersonalConnectorRedirectType(connector)
    : undefined;

  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      if (!nextOpen) {
        onPhaseChange('question');
      }
      onOpenChange(nextOpen);
    },
    [onOpenChange, onPhaseChange]
  );

  const handleGoToPersonal = useCallback(() => {
    handleOpenChange(false);
    if (personalConnectorType) {
      router.push(
        `/workspace/connectors/personal/?connectorType=${encodeURIComponent(personalConnectorType)}`
      );
    } else {
      router.push('/workspace/connectors/personal/');
    }
  }, [handleOpenChange, personalConnectorType, router]);

  if (!connector) {
    return null;
  }

  const isQuestion = phase === 'question';

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Content
        style={{
          maxWidth: '37.5rem',
          padding: 'var(--space-5)',
          backgroundColor: 'var(--color-panel-solid)',
          borderRadius: 'var(--radius-5)',
          border: '1px solid var(--olive-a3)',
          boxShadow:
            '0 16px 36px -20px rgba(0, 6, 46, 0.2), 0 16px 64px rgba(0, 0, 85, 0.02), 0 12px 60px rgba(0, 0, 0, 0.15)',
        }}
      >
        <Flex align="center" gap="2" style={{ marginBottom: 'var(--space-2)' }}>
          <MaterialIcon
            name={isQuestion ? 'admin_panel_settings' : 'info'}
            size={20}
            color={isQuestion ? 'var(--amber-9)' : 'var(--blue-9)'}
          />
          <Dialog.Title style={{ color: 'var(--slate-12)', margin: 0 }}>
            {isQuestion
              ? t('workspace.connectors.adminAccessDialog.title')
              : t('workspace.connectors.adminAccessDialog.redirectTitle')}
          </Dialog.Title>
        </Flex>

        <Dialog.Description
          size="2"
          style={{ color: 'var(--slate-11)', lineHeight: '20px', marginTop: 'var(--space-1)' }}
        >
          {isQuestion
            ? t('workspace.connectors.adminAccessDialog.question', { appGroup })
            : t('workspace.connectors.adminAccessDialog.redirectMessage', {
                appGroup,
                connectorName,
              })}
        </Dialog.Description>

        <Flex justify="end" gap="2" mt="4">
          {isQuestion ? (
            <>
              <Button
                type="button"
                variant="outline"
                color="gray"
                size="2"
                onClick={() => onPhaseChange('redirect')}
              >
                {t('workspace.connectors.adminAccessDialog.noNotAdmin')}
              </Button>
              <Button type="button" variant="solid" size="2" onClick={onConfirmAdmin}>
                {t('workspace.connectors.adminAccessDialog.yesContinue')}
              </Button>
            </>
          ) : (
            <>
              <Button
                type="button"
                variant="outline"
                color="gray"
                size="2"
                onClick={() => handleOpenChange(false)}
              >
                {t('workspace.connectors.adminAccessDialog.cancel')}
              </Button>
              <Button type="button" variant="solid" size="2" onClick={handleGoToPersonal}>
                {t('workspace.connectors.adminAccessDialog.goToPersonal', { appGroup })}
              </Button>
            </>
          )}
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
