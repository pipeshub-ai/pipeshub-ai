'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Dialog, Flex } from '@radix-ui/themes';

// ========================================
// Props
// ========================================

interface ConfigSuccessDialogProps {
  open: boolean;
  connectorName: string;
  onStartSyncing: () => void;
  onDoLater: () => void;
  /** Shown under the description, before the buttons. */
  children?: React.ReactNode;
}

// The dialog opens after the setup panel has closed, so Radix has no trigger to give
// focus back to; land keyboard users on the page's heading instead of the body.
function focusPageHeading(event: Event) {
  const heading = document.querySelector<HTMLElement>('main h1, h1');
  if (!heading) return;
  event.preventDefault();
  if (!heading.hasAttribute('tabindex')) heading.setAttribute('tabindex', '-1');
  heading.focus({ preventScroll: true });
}

// ========================================
// ConfigSuccessDialog
// ========================================

export function ConfigSuccessDialog({
  open,
  connectorName,
  onStartSyncing,
  onDoLater,
  children,
}: ConfigSuccessDialogProps) {
  const { t } = useTranslation();

  return (
    // Escape or a click outside means the same as "I'll do it later".
    <Dialog.Root open={open} onOpenChange={(next) => !next && onDoLater()}>
      <Dialog.Content
        maxWidth="420px"
        style={{ padding: 'var(--space-5)' }}
        onCloseAutoFocus={focusPageHeading}
      >
        <Flex direction="column" gap="3">
          <Dialog.Title size="4" weight="bold" style={{ color: 'var(--gray-12)', margin: 0 }}>
            {t('workspace.connectors.successDialog.title')}
          </Dialog.Title>

          <Dialog.Description size="2" style={{ color: 'var(--gray-11)' }}>
            {t('workspace.connectors.successDialog.description', { name: connectorName })}{' '}
            {t('workspace.connectors.successDialog.descriptionSuffix')}
          </Dialog.Description>

          {children}

          <Flex align="center" gap="3" style={{ marginTop: 'var(--space-1)' }}>
            <Button variant="outline" color="gray" size="2" onClick={onDoLater} style={{ cursor: 'pointer' }}>
              {t('workspace.connectors.successDialog.later')}
            </Button>
            <Button variant="solid" size="2" onClick={onStartSyncing} style={{ cursor: 'pointer' }}>
              {t('workspace.connectors.successDialog.startSync')}
            </Button>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
