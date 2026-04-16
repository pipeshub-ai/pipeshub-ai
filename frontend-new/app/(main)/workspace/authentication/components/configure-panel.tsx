'use client';

import React, { useRef, useCallback, useState } from 'react';
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

const METHOD_META: Record<
  ConfigurableMethod,
  { title: string; icon?: string; docUrl: string }
> = {
  google: {
    title: 'Configure Google Authentication',
    icon: 'google',
    docUrl: 'https://support.google.com/cloud/answer/6158849',
  },
  microsoft: {
    title: 'Configure Microsoft Authentication',
    icon: 'window',
    docUrl: 'https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app',
  },
  samlSso: {
    title: 'Configure SAML SSO',
    icon: 'security',
    docUrl: 'https://en.wikipedia.org/wiki/Security_Assertion_Markup_Language',
  },
  oauth: {
    title: 'Configure OAuth 2.0',
    icon: 'vpn_key',
    docUrl: 'https://oauth.net/2/',
  },
};

// ========================================
// Component
// ========================================

export function ConfigurePanel({ open, method, onClose, onSaveSuccess }: ConfigurePanelProps) {
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

  const meta = METHOD_META[method];

  const docButton = (
    <Button
      variant="outline"
      color="gray"
      size="1"
      onClick={() => window.open(meta.docUrl, '_blank')}
      style={{ cursor: 'pointer', gap: 'var(--space-1)' }}
    >
      <span className="material-icons-outlined" style={{ fontSize: 14 }}>open_in_new</span>
      <Text size="1">Documentation</Text>
    </Button>
  );

  return (
    <WorkspaceRightPanel
      open={open}
      onOpenChange={(o) => { if (!o) onClose(); }}
      title={meta.title}
      icon={meta.icon}
      headerActions={docButton}
      primaryLabel="Save"
      secondaryLabel="Cancel"
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
