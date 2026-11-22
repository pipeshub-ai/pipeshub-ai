'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Button, Callout, Dialog, Flex } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { Connector } from '../types';
import { personalConnectorHref } from '../utils/admin-access-helpers';

export interface TeamVisibilityWarningDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connector: Connector | null;
  hasPersonal: boolean;
  onContinue: () => void;
}

/** Shown before creating a team connector whose data the whole organization will be able to search. */
export function TeamVisibilityWarningDialog({
  open,
  onOpenChange,
  connector,
  hasPersonal,
  onContinue,
}: TeamVisibilityWarningDialogProps) {
  const { t } = useTranslation();
  const router = useRouter();

  if (!connector) return null;
  const name = connector.name;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Content
        style={{
          maxWidth: '37.5rem',
          padding: 'var(--space-5)',
          borderRadius: 'var(--radius-5)',
          border: '1px solid var(--olive-a3)',
        }}
      >
        <Flex align="center" gap="2" style={{ marginBottom: 'var(--space-2)' }}>
          <MaterialIcon name="warning" size={20} color="var(--amber-9)" />
          <Dialog.Title style={{ color: 'var(--slate-12)', margin: 0 }}>
            {t('workspace.connectors.teamVisibilityDialog.title', { name })}
          </Dialog.Title>
        </Flex>

        <Callout.Root color="amber" size="1" style={{ marginTop: 'var(--space-2)' }}>
          <Callout.Text>{t('workspace.connectors.teamVisibilityDialog.noPermissions', { name })}</Callout.Text>
        </Callout.Root>

        <Dialog.Description
          size="2"
          style={{ color: 'var(--slate-11)', lineHeight: '20px', marginTop: 'var(--space-3)' }}
        >
          {hasPersonal
            ? t('workspace.connectors.teamVisibilityDialog.usePersonal')
            : t('workspace.connectors.teamVisibilityDialog.teamOnly', { name })}
        </Dialog.Description>

        <Flex justify="end" gap="2" mt="4" wrap="wrap">
          <Button type="button" variant="outline" color="gray" size="2" onClick={() => onOpenChange(false)}>
            {t('workspace.connectors.teamVisibilityDialog.cancel')}
          </Button>
          <Button
            type="button"
            variant={hasPersonal ? 'soft' : 'solid'}
            color={hasPersonal ? 'gray' : undefined}
            size="2"
            onClick={onContinue}
          >
            {t('workspace.connectors.teamVisibilityDialog.continueTeam')}
          </Button>
          {hasPersonal && (
            <Button
              type="button"
              variant="solid"
              size="2"
              onClick={() => {
                onOpenChange(false);
                router.push(personalConnectorHref(connector.type));
              }}
            >
              {t('workspace.connectors.teamVisibilityDialog.goToPersonal')}
            </Button>
          )}
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
