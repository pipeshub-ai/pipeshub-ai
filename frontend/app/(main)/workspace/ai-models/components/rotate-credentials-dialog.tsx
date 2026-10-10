'use client';

import React, { useState } from 'react';
import { Button, Dialog, Flex, Text, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { AIModelsApi } from '../api';
import type { ConfiguredModel } from '../types';

interface RotateCredentialsDialogProps {
  model: ConfiguredModel | null;
  onClose: () => void;
  onSaved: () => void;
}

export function RotateCredentialsDialog({ model, onClose, onSaved }: RotateCredentialsDialogProps) {
  const { t } = useTranslation();
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const close = () => {
    setApiKey('');
    setError(null);
    onClose();
  };

  const save = async () => {
    if (!model?.connectionId || !apiKey.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await AIModelsApi.rotateConnectionCredentials(model.connectionId, { apiKey: apiKey.trim() });
      setApiKey('');
      onSaved();
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('workspace.aiModels.rotateCredentialsFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog.Root open={Boolean(model)} onOpenChange={(open) => { if (!open) close(); }}>
      <Dialog.Content style={{ maxWidth: 420 }}>
        <Dialog.Title>{t('workspace.aiModels.rotateCredentialsTitle')}</Dialog.Title>
        <Flex direction="column" gap="3">
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {t('workspace.aiModels.rotateCredentialsHint')}
          </Text>
          <TextField.Root
            type="password"
            value={apiKey}
            placeholder={t('workspace.aiModels.rotateCredentialsPlaceholder')}
            onChange={(event) => setApiKey(event.target.value)}
          />
          {error ? (
            <Text size="2" style={{ color: 'var(--red-11)' }}>{error}</Text>
          ) : null}
          <Flex justify="end" gap="2">
            <Button type="button" variant="soft" color="gray" onClick={close}>
              {t('workspace.aiModels.cancel')}
            </Button>
            <Button type="button" disabled={!apiKey.trim() || saving} onClick={() => void save()}>
              {t('workspace.aiModels.rotateCredentialsSave')}
            </Button>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
