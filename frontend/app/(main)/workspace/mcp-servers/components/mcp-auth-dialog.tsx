'use client';

import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Dialog, Flex, Text, TextField } from '@radix-ui/themes';
import { toast } from '@/lib/store/toast-store';
import { isProcessedError } from '@/lib/api';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { FormField } from '../../components';
import { McpServersApi } from '../api';
import {
  buildMultiEnvAuthPayload,
  isMultiEnvAuthComplete,
  needsMultiEnvAuth,
} from '../stdio-env-auth';
import type { McpMyServerEntry } from '../types';

/**
 * Auth dialog for `api_token` / `headers` MCP auth modes. Shared by the personal
 * (self-service) and team (admin) pages — both authenticate against the same
 * per-user credential endpoint, so there is no scope-specific behavior here.
 */
interface McpAuthDialogProps {
  instance: McpMyServerEntry | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAuthenticated: () => void;
}

export function McpAuthDialog({ instance, open, onOpenChange, onAuthenticated }: McpAuthDialogProps) {
  const { t } = useTranslation();
  const [apiToken, setApiToken] = useState('');
  const [headerValue, setHeaderValue] = useState('');
  const [envValues, setEnvValues] = useState<Record<string, string>>({});
  const [isSaving, setIsSaving] = useState(false);

  const requiredEnv = instance?.requiredEnv ?? [];
  const optionalEnv = instance?.optionalEnv ?? [];
  const multiEnv = needsMultiEnvAuth(requiredEnv);
  const envKeys = useMemo(
    () => (multiEnv ? [...requiredEnv, ...optionalEnv] : []),
    [multiEnv, requiredEnv, optionalEnv]
  );

  useEffect(() => {
    if (open) {
      setApiToken('');
      setHeaderValue('');
      setEnvValues({});
    }
  }, [open, instance?._id]);

  if (!instance) return null;

  const isHeaderMode = instance.authMode === 'headers';
  const isValid = isHeaderMode
    ? headerValue.trim().length > 0
    : multiEnv
      ? isMultiEnvAuthComplete(requiredEnv, envValues)
      : apiToken.trim().length > 0;

  const handleSubmit = async () => {
    if (!isValid) return;
    setIsSaving(true);
    try {
      const payload = isHeaderMode
        ? { headerValue: headerValue.trim() }
        : multiEnv
          ? buildMultiEnvAuthPayload(requiredEnv, optionalEnv, envValues)
          : { apiToken: apiToken.trim() };
      await McpServersApi.authenticate(instance._id, payload);
      toast.success(t('workspace.mcpServers.toasts.authenticated'));
      onOpenChange(false);
      onAuthenticated();
    } catch (error) {
      const detail = isProcessedError(error) ? error.message : undefined;
      toast.error(t('workspace.mcpServers.toasts.authenticateError'), detail ? { description: detail } : undefined);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={(v) => !isSaving && onOpenChange(v)}>
      <Dialog.Content
        style={{
          maxWidth: '30rem',
          padding: 'var(--space-5)',
          backgroundColor: 'var(--color-panel-solid)',
          borderRadius: 'var(--radius-5)',
          border: '1px solid var(--olive-a3)',
        }}
      >
        <Dialog.Title style={{ color: 'var(--slate-12)' }}>
          {t('workspace.mcpServers.authDialog.title', { name: instance.name })}
        </Dialog.Title>
        <Dialog.Description size="2" style={{ color: 'var(--slate-11)', marginTop: 4 }}>
          {t('workspace.mcpServers.authDialog.description')}
        </Dialog.Description>

        <Flex direction="column" gap="4" mt="4">
          {isHeaderMode ? (
            <FormField
              label={t('workspace.mcpServers.form.headerValue', { headerName: instance.headerName || 'Authorization' })}
              required
            >
              <TextField.Root
                size="2"
                type="password"
                value={headerValue}
                onChange={(e) => setHeaderValue(e.target.value)}
                autoFocus
              />
            </FormField>
          ) : multiEnv ? (
            envKeys.map((envKey, index) => {
              const isRequired = requiredEnv.includes(envKey);
              return (
                <FormField
                  key={envKey}
                  label={envKey}
                  required={isRequired}
                  optional={!isRequired}
                >
                  <TextField.Root
                    size="2"
                    type={envKey.toLowerCase().includes('token') || envKey.toLowerCase().includes('key') ? 'password' : 'text'}
                    value={envValues[envKey] ?? ''}
                    onChange={(e) => setEnvValues((prev) => ({ ...prev, [envKey]: e.target.value }))}
                    autoFocus={index === 0}
                  />
                </FormField>
              );
            })
          ) : (
            <FormField label={t('workspace.mcpServers.form.apiToken')} required>
              <TextField.Root
                size="2"
                type="password"
                value={apiToken}
                onChange={(e) => setApiToken(e.target.value)}
                autoFocus
              />
            </FormField>
          )}
        </Flex>

        <Flex justify="end" gap="2" mt="5">
          <Dialog.Close>
            <Text
              as="span"
              size="2"
              style={{ padding: '8px 14px', cursor: 'pointer', color: 'var(--gray-11)' }}
            >
              {t('common.cancel')}
            </Text>
          </Dialog.Close>
          <LoadingButton
            type="button"
            variant="solid"
            size="2"
            disabled={!isValid}
            loading={isSaving}
            loadingLabel={t('common.saving')}
            onClick={() => void handleSubmit()}
          >
            {t('common.save')}
          </LoadingButton>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
