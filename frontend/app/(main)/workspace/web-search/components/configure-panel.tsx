'use client';

import { useTranslation } from 'react-i18next';
import React, { useEffect, useState, useCallback, useRef } from 'react';
import { Flex, Text, Button, TextField, Callout } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { WorkspaceRightPanel } from '../../components/workspace-right-panel';
import { ConfirmationDialog } from '../../components/confirmation-dialog';
import { isProcessedError } from '@/lib/api';
import { WebSearchApi } from '../api';
import type {
  ConfigurableProvider,
  WebSearchProviderMeta,
  ConfiguredWebSearchProvider,
  WebSearchProviderAgentUsage,
} from '../types';

// ========================================
// Types
// ========================================

interface ConfigurePanelProps {
  open: boolean;
  provider: ConfigurableProvider | null;
  providerMeta: WebSearchProviderMeta | null;
  existingProvider: ConfiguredWebSearchProvider | null;
  onClose: () => void;
  onSaveSuccess: (params: { provider: ConfigurableProvider; isEdit: boolean }) => void;
  onDeleteSuccess: (params: {
    provider: ConfigurableProvider;
    wasDefault: boolean;
  }) => void;
}

// ========================================
// Component
// ========================================

export function ConfigurePanel({
  open,
  provider,
  providerMeta,
  existingProvider,
  onClose,
  onSaveSuccess,
  onDeleteSuccess,
}: ConfigurePanelProps) {
  const { t } = useTranslation();
  const [apiKey, setApiKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteUsageAgents, setDeleteUsageAgents] = useState<WebSearchProviderAgentUsage[]>([]);
  const [isCheckingUsage, setIsCheckingUsage] = useState(false);
  const usageCheckedRef = useRef(false);

  const isEdit = !!existingProvider;

  useEffect(() => {
    if (!open) return;
    setApiKey(existingProvider?.configuration?.apiKey ?? '');
    setShowKey(false);
    setErrorMessage(null);
    setIsSaving(false);
    setIsDeleting(false);
    setDeleteDialogOpen(false);
    setDeleteUsageAgents([]);
    setIsCheckingUsage(false);
    usageCheckedRef.current = false;
  }, [open, existingProvider]);

  const handleOpen = useCallback(
    (isOpen: boolean) => {
      if (!isOpen && !isSaving && !isDeleting) {
        onClose();
      }
    },
    [onClose, isSaving, isDeleting],
  );

  const handleSave = async () => {
    if (!provider || !providerMeta || !apiKey.trim()) return;

    setIsSaving(true);
    setErrorMessage(null);

    try {
      const providerData = {
        provider,
        configuration: { apiKey: apiKey.trim() },
        isDefault: existingProvider?.isDefault ?? false,
      };

      if (existingProvider) {
        await WebSearchApi.updateProvider(existingProvider.providerKey, providerData);
      } else {
        await WebSearchApi.addProvider(providerData);
      }

      onSaveSuccess({ provider, isEdit });
      onClose();
    } catch (err) {
      const message = isProcessedError(err)
        ? err.message
        : err instanceof Error
          ? err.message
          : 'Failed to save provider';
      setErrorMessage(message);
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!provider || !existingProvider) return;
    setIsDeleting(true);
    setErrorMessage(null);
    try {
      await WebSearchApi.deleteProvider(existingProvider.providerKey);
      onDeleteSuccess({
        provider,
        wasDefault: existingProvider.isDefault,
      });
      setDeleteDialogOpen(false);
      onClose();
    } catch (err) {
      const message = isProcessedError(err)
        ? err.message
        : err instanceof Error
          ? err.message
          : 'Failed to delete provider';
      setErrorMessage(message);
      setDeleteDialogOpen(false);
    } finally {
      setIsDeleting(false);
    }
  };

  if (!provider || !providerMeta) return null;

  const docButton = (
    <Button
      variant="outline"
      color="gray"
      size="1"
      onClick={() => window.open(providerMeta.docUrl, '_blank')}
      style={{ cursor: 'pointer', gap: 'var(--space-1)' }}
    >
      <span className="material-icons-outlined" style={{ fontSize: 14 }}>
        open_in_new
      </span>
      <Text size="1">{t('workspace.bots.documentation')}</Text>
    </Button>
  );

  const headerIcon =
    providerMeta.iconType === 'image' ? (
      <img
        src={providerMeta.icon}
        alt={providerMeta.label}
        style={{ width: 20, height: 20, objectFit: 'contain' }}
      />
    ) : (
      <MaterialIcon name={providerMeta.icon} size={20} color="var(--slate-12)" />
    );

  return (
    <>
      <WorkspaceRightPanel
        open={open}
        onOpenChange={handleOpen}
        title={providerMeta.label}
        icon={headerIcon}
        headerActions={docButton}
        primaryLabel={isEdit ? 'Update' : 'Save'}
        secondaryLabel="Cancel"
        primaryDisabled={!apiKey.trim()}
        primaryLoading={isSaving}
        onPrimaryClick={handleSave}
        onSecondaryClick={() => handleOpen(false)}
      >
        <Flex direction="column" gap="4">
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {providerMeta.description}
          </Text>

          {isEdit && (
            <Callout.Root color="blue" size="1" variant="soft">
              <Callout.Icon>
                <MaterialIcon name="info" size={14} color="var(--blue-11)" />
              </Callout.Icon>
              <Callout.Text>
                {existingProvider?.isDefault
                  ? 'This provider is currently set as default. Updating the API key will keep it as default.'
                  : 'Editing an existing configuration. Changes take effect immediately after saving.'}
              </Callout.Text>
            </Callout.Root>
          )}

          <Flex direction="column" gap="1">
            <Text size="1" weight="medium" style={{ color: 'var(--slate-12)' }}>
              API Key
            </Text>
            <TextField.Root
              type={showKey ? 'text' : 'password'}
              placeholder={providerMeta.apiKeyPlaceholder}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              disabled={isSaving || isDeleting}
            >
              <TextField.Slot>
                <MaterialIcon name="key" size={16} color="var(--slate-9)" />
              </TextField.Slot>
              <TextField.Slot side="right">
                <span
                  onClick={() => setShowKey((v) => !v)}
                  style={{ cursor: 'pointer', display: 'flex', alignItems: 'center' }}
                >
                  <MaterialIcon
                    name={showKey ? 'visibility_off' : 'visibility'}
                    size={16}
                    color="var(--slate-9)"
                  />
                </span>
              </TextField.Slot>
            </TextField.Root>
            {providerMeta.apiKeyHelperText && (
              <Text size="1" style={{ color: 'var(--slate-10)' }}>
                {providerMeta.apiKeyHelperText}
              </Text>
            )}
          </Flex>

          {errorMessage && (
            <Callout.Root color="red" size="1" variant="soft">
              <Callout.Icon>
                <MaterialIcon name="error" size={14} color="var(--red-11)" />
              </Callout.Icon>
              <Callout.Text>{errorMessage}</Callout.Text>
            </Callout.Root>
          )}

          {isEdit && (
            <Flex
              direction="column"
              gap="2"
              style={{
                marginTop: 8,
                padding: '12px',
                borderRadius: 'var(--radius-2)',
                border: '1px solid var(--red-5)',
                backgroundColor: 'var(--red-2)',
              }}
            >
              <Text size="1" weight="medium" style={{ color: 'var(--slate-12)' }}>
                Danger zone
              </Text>
              <Text size="1" style={{ color: 'var(--slate-11)', fontWeight: 300 }}>
                Remove this provider&apos;s configuration. If it is the default, DuckDuckGo will
                be used instead.
              </Text>
              <Flex>
                <Button
                  variant="outline"
                  color="red"
                  size="1"
                  onClick={async () => {
                    if (!provider || !existingProvider) return;
                    setDeleteDialogOpen(true);
                    if (!usageCheckedRef.current) {
                      setIsCheckingUsage(true);
                      try {
                        const agents = await WebSearchApi.getProviderUsage(existingProvider.provider);
                        setDeleteUsageAgents(agents);
                      } finally {
                        setIsCheckingUsage(false);
                        usageCheckedRef.current = true;
                      }
                    }
                  }}
                  disabled={isSaving || isDeleting}
                  style={{
                    cursor: isSaving || isDeleting ? 'not-allowed' : 'pointer',
                    gap: 4,
                  }}
                >
                  <MaterialIcon name="delete" size={14} color="var(--red-11)" />
                  Delete provider
                </Button>
              </Flex>
            </Flex>
          )}
        </Flex>
      </WorkspaceRightPanel>

      <ConfirmationDialog
        open={deleteDialogOpen}
        onOpenChange={(open) => {
          setDeleteDialogOpen(open);
          if (!open) setDeleteUsageAgents([]);
        }}
        title={
          !isCheckingUsage && deleteUsageAgents.length > 0
            ? `Cannot delete ${providerMeta.label}`
            : `Delete ${providerMeta.label} configuration?`
        }
        message={
          <Flex direction="column" gap="2">
            {isCheckingUsage && (
              <Text size="2" style={{ color: 'var(--slate-10)', fontStyle: 'italic' }}>
                Checking agent usage…
              </Text>
            )}
            {!isCheckingUsage && deleteUsageAgents.length > 0 && (
              <Flex
                direction="column"
                gap="1"
                style={{
                  padding: '10px 12px',
                  borderRadius: 'var(--radius-2)',
                  border: '1px solid var(--red-5)',
                  backgroundColor: 'var(--red-2)',
                }}
              >
                <Text size="1" weight="medium" style={{ color: 'var(--red-11)' }}>
                  This provider is currently used by {deleteUsageAgents.length}{' '}
                  {deleteUsageAgents.length === 1 ? 'agent' : 'agents'}:
                </Text>
                <Flex direction="column" gap="1" style={{ paddingLeft: 4 }}>
                  {deleteUsageAgents.map((agent) => (
                    <Text key={agent._key} size="1" style={{ color: 'var(--slate-12)' }}>
                      • <Text weight="medium" size="1">{agent.name}</Text>
                      {agent.creatorName && (
                        <Text size="1" style={{ color: 'var(--slate-10)' }}>
                          {' '}(created by {agent.creatorName})
                        </Text>
                      )}
                    </Text>
                  ))}
                </Flex>
                <Text size="1" style={{ color: 'var(--red-11)', marginTop: 2 }}>
                  Please remove the web search configuration from these agents before deleting this provider.
                </Text>
              </Flex>
            )}
            {!isCheckingUsage && deleteUsageAgents.length === 0 && (
              <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
                {`This will permanently remove your ${providerMeta.label} API key. ${
                  existingProvider?.isDefault
                    ? 'DuckDuckGo will become the default web search provider.'
                    : 'You can reconfigure it later.'
                }`}
              </Text>
            )}
          </Flex>
        }
        hideConfirm={isCheckingUsage || deleteUsageAgents.length > 0}
        confirmLabel="Delete"
        confirmLoadingLabel="Deleting..."
        confirmVariant="danger"
        isLoading={isDeleting}
        onConfirm={handleDelete}
      />
    </>
  );
}
