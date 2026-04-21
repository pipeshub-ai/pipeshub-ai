'use client';

import React, { useRef, useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Text, Button } from '@radix-ui/themes';
import { WorkspaceRightPanel } from '../../components/workspace-right-panel';
import type { ConfigurableMethod } from '../types';
import ProviderConfigForm from './forms/provider-config-form';
import type { ProviderConfigFormRef } from './forms';

// ========================================
// Types
// ========================================

interface ConfigurePanelProps {
  open: boolean;
  method: ConfigurableMethod | null;
  onClose: () => void;
  onSaveSuccess: (method: ConfigurableMethod) => void;
}

// ── Per-method display info ────────────────────────────────

const METHOD_DOC_URLS: Record<ConfigurableMethod, string> = {
  google: 'https://support.google.com/cloud/answer/6158849',
  microsoft: 'https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app',
  samlSso: 'https://en.wikipedia.org/wiki/Security_Assertion_Markup_Language',
  oauth: 'https://oauth.net/2/',
};

const METHOD_ICONS: Record<ConfigurableMethod, string> = {
  google: 'google',
  microsoft: 'window',
  samlSso: 'security',
  oauth: 'vpn_key',
};

// ========================================
// Component
// ========================================

export function ConfigurePanel({ open, method, onClose, onSaveSuccess }: ConfigurePanelProps) {
  const { t } = useTranslation();
  const [isSaving, setIsSaving] = useState(false);
  const [isFormValid, setIsFormValid] = useState(false);

  const formRef = useRef<ProviderConfigFormRef>(null);

  const handleValidChange = useCallback((valid: boolean) => {
    setIsFormValid(valid);
  }, []);

  const handleSave = async () => {
    if (!method) return;
    setIsSaving(true);
    try {
      const success = (await formRef.current?.submit()) ?? false;
      if (success) {
        onSaveSuccess(method);
        onClose();
      }
    } finally {
      setIsSaving(false);
    }
  };

  if (!method) return null;

  const panelTitleKey = `workspace.authentication.panels.${method}` as const;

  const docButton = (
    <Button
      variant="outline"
      color="gray"
      size="1"
      onClick={() => window.open(meta.docUrl, '_blank')}
      style={{ cursor: 'pointer', gap: 'var(--space-1)' }}
    >
      <span className="material-icons-outlined" style={{ fontSize: 14 }}>open_in_new</span>
      <Text size="1">{t('workspaceMenu.documentation')}</Text>
    </Button>
  );

  return (
    <WorkspaceRightPanel
      open={open}
      onOpenChange={(o) => { if (!o) onClose(); }}
      title={t(panelTitleKey)}
      icon={METHOD_ICONS[method]}
      headerActions={docButton}
      primaryLabel={t('action.save')}
      secondaryLabel={t('action.cancel')}
      primaryDisabled={!isFormValid}
      primaryLoading={isSaving}
      onPrimaryClick={handleSave}
      onSecondaryClick={onClose}
    >
      <ProviderConfigForm
        key={method}
        ref={formRef}
        method={method}
        onValidChange={handleValidChange}
      />
    </WorkspaceRightPanel>
  );
}
