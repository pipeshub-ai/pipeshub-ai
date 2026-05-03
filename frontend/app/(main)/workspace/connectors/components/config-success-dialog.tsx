'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text, Button } from '@radix-ui/themes';

// ========================================
// Props
// ========================================

interface ConfigSuccessDialogProps {
  open: boolean;
  connectorName: string;
  onStartSyncing: () => void;
  onDoLater: () => void;
}

// ========================================
// ConfigSuccessDialog
// ========================================

export function ConfigSuccessDialog({
  open,
  connectorName,
  onStartSyncing,
  onDoLater,
}: ConfigSuccessDialogProps) {
  const { t } = useTranslation();
  if (!open) return null;

  return (
    <Flex
      align="center"
      justify="center"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
      }}
    >
      {/* Backdrop */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          backgroundColor: 'rgba(0, 0, 0, 0.4)',
        }}
      />

      {/* Dialog card */}
      <Flex
        direction="column"
        gap="3"
        style={{
          position: 'relative',
          zIndex: 1,
          width: 420,
          maxWidth: 'calc(100vw - 32px)',
          padding: 24,
          backgroundColor: 'var(--gray-2)',
          border: '1px solid var(--gray-a4)',
          borderRadius: 'var(--radius-4)',
          boxShadow:
            '0px 0px 0px 1px var(--gray-a3), 0px 12px 60px rgba(0,0,0,0.3), 0px 16px 64px var(--gray-a2)',
        }}
      >
        <Text size="4" weight="bold" style={{ color: 'var(--gray-12)' }}>
          {t('workspace.connectors.successDialog.title')}
        </Text>

        <Text size="2" style={{ color: 'var(--gray-11)', lineHeight: '20px' }}>
          {t('workspace.connectors.successDialog.description', { name: connectorName })}{' '}
          {t('workspace.connectors.successDialog.descriptionSuffix')}
        </Text>

        <Flex align="center" gap="3" style={{ marginTop: 4 }}>
          <Button
            variant="outline"
            color="gray"
            size="2"
            onClick={onDoLater}
            style={{ cursor: 'pointer' }}
          >
            {t('workspace.connectors.successDialog.later')}
          </Button>
          <Button
            variant="solid"
            size="2"
            onClick={onStartSyncing}
            style={{ cursor: 'pointer' }}
          >
            {t('workspace.connectors.successDialog.startSync')}
          </Button>
        </Flex>
      </Flex>
    </Flex>
  );
}
