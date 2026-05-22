'use client';

import React, { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Dialog, Button, Flex } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { useToastStore } from '@/lib/store/toast-store';
import { useConnectorsStore } from '../store';
import { ConnectorsApi } from '../api';

// ========================================
// Types
// ========================================

export interface DisableFirstDialogProps {
  /** Controls open/close */
  open: boolean;
  onOpenChange: (open: boolean) => void;

  /** Connector instance ID to toggle off before proceeding */
  connectorId: string;

  /** Optional display name used in the message body */
  connectorName?: string;

  /**
   * Short phrase describing what will happen after disabling.
   * E.g. "remove this connector instance" or "save configuration changes".
   */
  actionLabel: string;

  /**
   * Called immediately after the connector has been successfully disabled.
   * The parent is responsible for handling errors from this callback; any
   * exceptions thrown here are silently swallowed so as not to double-toast.
   */
  onProceed: () => void | Promise<void>;

  /**
   * Portal container for overlay + content. Pass the host from
   * {@link useWorkspaceDrawerNestedModalHost} when this dialog must stack
   * above {@link WorkspaceRightPanel}.
   */
  container?: HTMLElement | null;
}

// ========================================
// Component
// ========================================

/**
 * DisableFirstDialog — shown when the user attempts an action (delete, save
 * config) on a connector that is currently enabled. Disables the connector
 * via the toggle API and then calls `onProceed`.
 *
 * Render it unconditionally and control visibility with `open`. Only mount it
 * when the connector *is* active — when the connector is already disabled,
 * skip this dialog and go straight to the action.
 */
export function DisableFirstDialog({
  open,
  onOpenChange,
  connectorId,
  connectorName,
  actionLabel,
  onProceed,
  container,
}: DisableFirstDialogProps) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const [isBusy, setIsBusy] = useState(false);

  const handleProceed = useCallback(async () => {
    setIsBusy(true);
    try {
      await ConnectorsApi.toggleConnector(connectorId, 'sync');
    } catch (err: unknown) {
      setIsBusy(false);
      const message =
        err instanceof Error
          ? err.message
          : t('workspace.connectors.disableFirstDialog.errorFallback');
      addToast({
        variant: 'error',
        title: t('workspace.connectors.disableFirstDialog.errorTitle'),
        description: message,
      });
      return;
    }

    // Optimistically patch the store so that any retry (e.g. after a failed
    // save-config) reads isActive=false and skips this dialog, preventing the
    // enable/disable oscillation that would otherwise occur.
    useConnectorsStore.setState((s) => ({
      panelConnector: s.panelConnector
        ? { ...s.panelConnector, isActive: false }
        : s.panelConnector,
      instances: s.instances.map((i) =>
        i._key === connectorId ? { ...i, isActive: false } : i
      ),
      activeConnectors: s.activeConnectors.map((c) =>
        c._key === connectorId ? { ...c, isActive: false } : c
      ),
      selectedInstance:
        s.selectedInstance?._key === connectorId
          ? { ...s.selectedInstance, isActive: false }
          : s.selectedInstance,
    }));

    // Close dialog first, then run the follow-up action.
    // Closing before calling onProceed avoids "setState on unmounted component"
    // warnings when onProceed navigates away or removes the connector.
    setIsBusy(false);
    onOpenChange(false);
    try {
      await onProceed();
    } catch {
      // Parent is expected to handle errors from the action via its own
      // toast / error state; swallow here to prevent double-toasting.
    }
  }, [connectorId, onProceed, onOpenChange, addToast, t]);

  return (
    <Dialog.Root open={open} onOpenChange={(v) => { if (!isBusy) onOpenChange(v); }}>
      <Dialog.Content
        container={container ?? undefined}
        // Prevent accidental dismissal while the toggle API call is in-flight.
        onPointerDownOutside={(e) => { if (isBusy) e.preventDefault(); }}
        onEscapeKeyDown={(e) => { if (isBusy) e.preventDefault(); }}
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
          <MaterialIcon name="warning_amber" size={20} color="var(--amber-9)" />
          <Dialog.Title style={{ color: 'var(--slate-12)', margin: 0 }}>
            {t('workspace.connectors.disableFirstDialog.title')}
          </Dialog.Title>
        </Flex>

        <Dialog.Description
          size="2"
          style={{ color: 'var(--slate-11)', lineHeight: '20px', marginTop: 'var(--space-1)' }}
        >
          {connectorName
            ? t('workspace.connectors.disableFirstDialog.message', {
                name: `"${connectorName}"`,
                action: actionLabel,
              })
            : t('workspace.connectors.disableFirstDialog.messageNoName', { action: actionLabel })}
        </Dialog.Description>

        <Flex justify="end" gap="2" mt="4">
          <Button
            type="button"
            variant="outline"
            color="gray"
            size="2"
            disabled={isBusy}
            onClick={() => onOpenChange(false)}
            style={{ cursor: isBusy ? 'not-allowed' : 'pointer' }}
          >
            {t('workspace.connectors.disableFirstDialog.cancel')}
          </Button>
          <LoadingButton
            type="button"
            variant="solid"
            size="2"
            onClick={() => void handleProceed()}
            loading={isBusy}
            loadingLabel={t('workspace.connectors.disableFirstDialog.proceeding')}
          >
            {t('workspace.connectors.disableFirstDialog.proceed')}
          </LoadingButton>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
