'use client';

import React, { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text, Button, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { WorkspaceRightPanel } from '../../components/workspace-right-panel';
import { WebSearchApi } from '../api';
import type {
  ConfigurableProvider,
  WebSearchProviderMeta,
  ConfiguredWebSearchProvider,
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
  onSaveSuccess: (provider: ConfigurableProvider) => void;
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
}: ConfigurePanelProps) {
  const { t } = useTranslation();
  const [apiKey, setApiKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [savingStatusText, setSavingStatusText] = useState('');

  const handleOpen = useCallback(
    (isOpen: boolean) => {
      if (!isOpen) {
        onClose();
        setApiKey('');
        setShowKey(false);
        setSavingStatusText('');
      }
    },
    [onClose],
  );

  const handleSave = async () => {
    if (!provider || !providerMeta || !apiKey.trim()) return;

    setIsSaving(true);
    setSavingStatusText(t('workspace.webSearch.verifying'));

    try {
      const providerData = {
        provider,
        configuration: { apiKey: apiKey.trim() },
        isDefault: false,
      };

      if (existingProvider) {
        await WebSearchApi.updateProvider(existingProvider.providerKey, providerData);
      } else {
        await WebSearchApi.addProvider(providerData);
      }

      setSavingStatusText('');
      onSaveSuccess(provider);
      onClose();
      setApiKey('');
      setShowKey(false);
    } catch {
      setSavingStatusText('');
    } finally {
      setIsSaving(false);
    }
  };

  if (!provider || !providerMeta) return null;

  const docButton = (
    <Button
      variant="outline"
      color="gray"
      size="1"
      onClick={() => window.open(providerMeta.docUrl, '_blank')}
      style={{ cursor: 'pointer', gap: 4 }}
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
    <WorkspaceRightPanel
      open={open}
      onOpenChange={handleOpen}
      title={providerMeta.label}
      icon={headerIcon}
      headerActions={docButton}
      primaryLabel={t('action.save')}
      secondaryLabel={t('action.cancel')}
      primaryDisabled={!apiKey.trim()}
      primaryLoading={isSaving}
      onPrimaryClick={handleSave}
      onSecondaryClick={() => handleOpen(false)}
    >
      <Flex direction="column" gap="4">
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          {providerMeta.description}
        </Text>

        <Flex direction="column" gap="1">
          <TextField.Root
            type={showKey ? 'text' : 'password'}
            placeholder={providerMeta.apiKeyPlaceholder}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
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
      </Flex>

      {savingStatusText && (
        <Flex
          align="center"
          gap="2"
          style={{
            position: 'absolute',
            bottom: 60,
            left: '50%',
            transform: 'translateX(-50%)',
            backgroundColor: 'var(--slate-2)',
            border: '1px solid var(--slate-5)',
            borderRadius: 'var(--radius-2)',
            padding: '8px 16px',
            boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
          }}
        >
          <MaterialIcon name="hourglass_empty" size={16} color="var(--slate-10)" />
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {savingStatusText}
          </Text>
        </Flex>
      )}
    </WorkspaceRightPanel>
  );
}
