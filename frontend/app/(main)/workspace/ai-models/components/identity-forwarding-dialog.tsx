'use client';

import React, { useEffect, useState } from 'react';
import { Button, Code, Dialog, Flex, Select, Switch, Text, TextArea, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { toast } from '@/lib/store/toast-store';
import { AIModelsApi } from '../api';
import type {
  ConfiguredModel,
  IdentityForwardingMode,
  IdentityForwardingPreset,
  IdentityForwardingSettings,
  IdentityPreview,
} from '../types';

const TEMPLATE_PLACEHOLDERS = '{{subject}}, {{groups}}, {{workload}}, {{request_id}}';
const SERVICE_KEY_FIELD = 'identityForwardingServiceApiKey';

const PRESETS: ReadonlyArray<{ value: IdentityForwardingPreset; label?: string; labelKey?: string }> = [
  { value: 'litellm', label: 'LiteLLM' },
  { value: 'portkey', label: 'Portkey' },
  { value: 'helicone', label: 'Helicone' },
  { value: 'cloudflare', label: 'Cloudflare AI Gateway' },
  { value: 'apim', label: 'Azure API Management' },
  { value: 'custom', labelKey: 'workspace.aiModels.identity.presetCustom' },
];

function templatesToText(templates: unknown): string {
  if (!templates || typeof templates !== 'object') return '';
  return Object.entries(templates as Record<string, unknown>)
    .map(([name, value]) => `${name}: ${String(value)}`)
    .join('\n');
}

function textToTemplates(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split('\n')) {
    const at = line.indexOf(':');
    if (at < 1) continue;
    const name = line.slice(0, at).trim();
    const value = line.slice(at + 1).trim();
    if (name && value) out[name] = value;
  }
  return out;
}

interface IdentityForwardingDialogProps {
  model: ConfiguredModel | null;
  onClose: () => void;
  onSaved: () => void;
}

export function IdentityForwardingDialog({ model, onClose, onSaved }: IdentityForwardingDialogProps) {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useState(false);
  const [mode, setMode] = useState<IdentityForwardingMode>('headers');
  const [preset, setPreset] = useState<IdentityForwardingPreset>('litellm');
  const [templates, setTemplates] = useState('');
  const [jwtAudience, setJwtAudience] = useState('');
  const [serviceAuth, setServiceAuth] = useState<IdentityForwardingSettings['serviceAuth']>('apiKey');
  const [serviceKey, setServiceKey] = useState('');
  const [clearServiceKey, setClearServiceKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<IdentityPreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const hasSavedKey = Boolean(model?.configuredFields?.includes(SERVICE_KEY_FIELD));

  useEffect(() => {
    if (!model) return;
    const cfg = model.configuration ?? {};
    setEnabled(cfg.identityForwardingEnabled === true);
    // signedJwt is set through the API or the enterprise edition; keep it rather than downgrade it on save.
    const storedMode = cfg.identityForwardingMode;
    setMode(storedMode === 'template' || storedMode === 'signedJwt' ? storedMode : 'headers');
    setJwtAudience(typeof cfg.identityForwardingJwtAudience === 'string' ? cfg.identityForwardingJwtAudience : '');
    setServiceAuth(cfg.identityForwardingServiceAuth === 'jwt' ? 'jwt' : 'apiKey');
    setPreset((cfg.identityForwardingPreset as IdentityForwardingPreset) || 'litellm');
    setTemplates(templatesToText(cfg.identityForwardingHeaderTemplates));
    setServiceKey('');
    setClearServiceKey(false);
    setPreview(null);
    setError(null);
  }, [model]);

  const useTemplate = mode === 'template';
  const needsTemplates = useTemplate && preset === 'custom';

  const formConfiguration = (): Record<string, unknown> => ({
    identityForwardingEnabled: enabled,
    identityForwardingMode: mode,
    identityForwardingPreset: preset,
    identityForwardingHeaderTemplates: textToTemplates(templates),
    ...(serviceKey.trim() ? { [SERVICE_KEY_FIELD]: serviceKey.trim() } : {}),
  });

  const runPreview = async () => {
    if (!model?.connectionId) return;
    setPreviewing(true);
    setError(null);
    try {
      setPreview(
        await AIModelsApi.previewIdentity({
          connectionId: model.connectionId,
          workload: 'chat',
          configuration: formConfiguration(),
        }),
      );
    } catch (err: unknown) {
      setPreview(null);
      setError(err instanceof Error ? err.message : t('workspace.aiModels.identity.previewFailed'));
    } finally {
      setPreviewing(false);
    }
  };

  const save = async () => {
    if (!model?.connectionId) return;
    setSaving(true);
    setError(null);
    try {
      let serviceApiKey: string | undefined;
      if (clearServiceKey) serviceApiKey = '';
      else if (serviceKey.trim()) serviceApiKey = serviceKey.trim();
      await AIModelsApi.setConnectionIdentityForwarding(model.connectionId, {
        enabled,
        mode,
        preset,
        headerTemplates: textToTemplates(templates),
        serviceAuth,
        ...(jwtAudience ? { jwtAudience } : {}),
        ...(serviceApiKey !== undefined ? { serviceApiKey } : {}),
      });
      toast.success(t('workspace.aiModels.identity.saved'));
      onSaved();
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('workspace.aiModels.identity.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog.Root open={Boolean(model)} onOpenChange={(open) => { if (!open) onClose(); }}>
      <Dialog.Content style={{ maxWidth: 560 }}>
        <Dialog.Title>{t('workspace.aiModels.identity.title')}</Dialog.Title>
        <Flex direction="column" gap="4">
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {t('workspace.aiModels.identity.hint')}
          </Text>

          <Flex align="center" justify="between" gap="3">
            <Text size="2" weight="medium">{t('workspace.aiModels.identity.enabled')}</Text>
            <Switch checked={enabled} onCheckedChange={setEnabled} />
          </Flex>

          {enabled ? (
            <>
              <Flex direction="column" gap="1">
                <Text size="2" weight="medium">{t('workspace.aiModels.identity.format')}</Text>
                <Select.Root value={mode} onValueChange={(value) => setMode(value as IdentityForwardingMode)}>
                  <Select.Trigger />
                  <Select.Content>
                    <Select.Item value="headers">{t('workspace.aiModels.identity.formatHeaders')}</Select.Item>
                    <Select.Item value="template">{t('workspace.aiModels.identity.formatTemplate')}</Select.Item>
                    {mode === 'signedJwt' ? (
                      <Select.Item value="signedJwt">{t('workspace.aiModels.identity.formatSignedJwt')}</Select.Item>
                    ) : null}
                  </Select.Content>
                </Select.Root>
              </Flex>

              {useTemplate ? (
                <Flex direction="column" gap="1">
                  <Text size="2" weight="medium">{t('workspace.aiModels.identity.preset')}</Text>
                  <Select.Root value={preset} onValueChange={(value) => setPreset(value as IdentityForwardingPreset)}>
                    <Select.Trigger />
                    <Select.Content>
                      {PRESETS.map((item) => (
                        <Select.Item key={item.value} value={item.value}>
                          {item.labelKey ? t(item.labelKey) : item.label}
                        </Select.Item>
                      ))}
                    </Select.Content>
                  </Select.Root>
                </Flex>
              ) : null}

              {needsTemplates ? (
                <Flex direction="column" gap="1">
                  <Text size="2" weight="medium">{t('workspace.aiModels.identity.templates')}</Text>
                  <TextArea
                    value={templates}
                    rows={4}
                    placeholder="x-end-user: {{subject}}"
                    onChange={(event) => setTemplates(event.target.value)}
                  />
                  <Text size="1" style={{ color: 'var(--gray-11)' }}>
                    {t('workspace.aiModels.identity.templatesHint', { placeholders: TEMPLATE_PLACEHOLDERS })}
                  </Text>
                </Flex>
              ) : null}

              <Flex direction="column" gap="1">
                <Text size="2" weight="medium">{t('workspace.aiModels.identity.serviceKey')}</Text>
                <TextField.Root
                  type="password"
                  value={serviceKey}
                  disabled={clearServiceKey}
                  placeholder={
                    hasSavedKey
                      ? t('workspace.aiModels.identity.serviceKeySaved')
                      : t('workspace.aiModels.identity.serviceKeyPlaceholder')
                  }
                  onChange={(event) => setServiceKey(event.target.value)}
                />
                <Text size="1" style={{ color: 'var(--gray-11)' }}>
                  {t('workspace.aiModels.identity.serviceKeyHint')}
                </Text>
                {hasSavedKey ? (
                  <Flex align="center" gap="2">
                    <Switch size="1" checked={clearServiceKey} onCheckedChange={setClearServiceKey} />
                    <Text size="1">{t('workspace.aiModels.identity.serviceKeyRemove')}</Text>
                  </Flex>
                ) : null}
              </Flex>
            </>
          ) : null}

          {preview ? (
            <Flex direction="column" gap="2">
              <Text size="2" weight="medium">{t('workspace.aiModels.identity.previewTitle')}</Text>
              {preview.headers ? (
                <Flex direction="column" gap="1">
                  {Object.entries(preview.headers).map(([name, value]) => (
                    <Text key={name} size="1" style={{ wordBreak: 'break-all' }}>
                      <Code>{name}</Code>: {value}
                    </Text>
                  ))}
                </Flex>
              ) : (
                <Text size="2" style={{ color: 'var(--gray-11)' }}>
                  {t('workspace.aiModels.identity.previewNone')}
                </Text>
              )}
              {preview.notes.map((note) => (
                <Text key={note} size="1" style={{ color: 'var(--amber-11)' }}>{note}</Text>
              ))}
            </Flex>
          ) : null}

          {error ? <Text size="2" style={{ color: 'var(--red-11)' }}>{error}</Text> : null}

          <Flex justify="between" gap="2">
            <Button type="button" variant="soft" disabled={previewing || saving} onClick={() => void runPreview()}>
              {t('workspace.aiModels.identity.preview')}
            </Button>
            <Flex gap="2">
              <Button type="button" variant="soft" color="gray" onClick={onClose}>
                {t('workspace.aiModels.cancel')}
              </Button>
              <Button type="button" disabled={saving} onClick={() => void save()}>
                {t('workspace.aiModels.identity.save')}
              </Button>
            </Flex>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
