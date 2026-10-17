'use client';

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Callout, Dialog, Flex, Text } from '@radix-ui/themes';
import { toast } from '@/lib/store/toast-store';
import { getUserFacingErrorMessage } from '@/lib/api/api-error';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { FormField } from '../../components';
import { mcpOAuthFailureMessage, useMcpOAuthPopup } from '../../mcp-servers/hooks/use-mcp-oauth-popup';
import { ApiToolsetsApi } from '../api';
import {
  buildCredentials,
  connectionMode,
  credentialFields,
  isSecretField,
  usesSignIn,
  type CredentialField,
} from '../toolset-form';
import type { ApiToolset, ApiToolsetTestResult } from '../types';
import { LabeledTextField } from './labeled-controls';

interface ApiToolsetConnectDialogProps {
  toolset: ApiToolset | null;
  isAdmin: boolean;
  onOpenChange: (open: boolean) => void;
  /** The connection changed: reload the list. */
  onChanged: () => void;
}

/** Connect, disconnect and test one toolset: the viewer's own credential, or the shared one for an admin. */
export function ApiToolsetConnectDialog({ toolset, isAdmin, onOpenChange, onChanged }: ApiToolsetConnectDialogProps) {
  const { t } = useTranslation();
  const [values, setValues] = useState<Partial<Record<CredentialField, string>>>({});
  const [busy, setBusy] = useState<'save' | 'disconnect' | 'test' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<ApiToolsetTestResult | null>(null);

  const toolsetId = toolset?.id;
  useEffect(() => {
    setValues({});
    setError(null);
    setTestResult(null);
  }, [toolsetId]);

  const mode = toolset ? connectionMode(toolset, isAdmin) : 'none';
  const shared = mode === 'shared';

  const { startOAuthPopup, status: signInStatus } = useMcpOAuthPopup({
    getAuthorizationUrl: (id) => ApiToolsetsApi.getAuthorizationUrl(id, shared),
    verifyAuthenticated: async () =>
      Boolean((await ApiToolsetsApi.listInstances()).find((i) => i.id === toolsetId)?.isConnected),
    onVerified: () => {
      toast.success(t('workspace.apiToolsets.toasts.connected'));
      onChanged();
    },
    onFailed: (failure) => setError(mcpOAuthFailureMessage(t, failure)),
  });

  if (!toolset) return null;

  const fields = credentialFields(toolset.authType);
  const credentials = buildCredentials(toolset.authType, values);
  const canManage = mode === 'own' || mode === 'shared';

  const run = async (kind: 'save' | 'disconnect' | 'test', action: () => Promise<void>) => {
    setBusy(kind);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(getUserFacingErrorMessage(e, t('workspace.apiToolsets.connect.error')));
    } finally {
      setBusy(null);
    }
  };

  const save = () =>
    run('save', async () => {
      if (!credentials) return;
      await ApiToolsetsApi.saveCredentials(toolset.id, credentials, shared);
      setValues({});
      toast.success(t('workspace.apiToolsets.toasts.connected'));
      onChanged();
    });

  const disconnect = () =>
    run('disconnect', async () => {
      await ApiToolsetsApi.deleteCredentials(toolset.id, shared);
      setTestResult(null);
      toast.success(t('workspace.apiToolsets.toasts.disconnected'));
      onChanged();
    });

  const test = () =>
    run('test', async () => {
      setTestResult(null);
      setTestResult(await ApiToolsetsApi.testConnection(toolset.id));
    });

  return (
    <Dialog.Root open onOpenChange={(v) => busy === null && onOpenChange(v)}>
      <Dialog.Content style={{ maxWidth: '32rem' }}>
        <Dialog.Title>{t('workspace.apiToolsets.connect.title', { name: toolset.name })}</Dialog.Title>
        <Dialog.Description size="2" style={{ color: 'var(--gray-11)' }}>
          {t(`workspace.apiToolsets.connect.mode.${mode}`)}
        </Dialog.Description>

        <Flex direction="column" gap="4" mt="4">
          <Flex align="center" gap="2">
            <Text size="2" weight="medium">
              {t('workspace.apiToolsets.connect.status')}
            </Text>
            <Badge color={toolset.isConnected ? 'jade' : 'gray'}>
              {t(toolset.isConnected ? 'workspace.apiToolsets.status.connected' : 'workspace.apiToolsets.status.notConnected')}
            </Badge>
          </Flex>

          {canManage && usesSignIn(toolset.authType) ? (
            <LoadingButton
              size="2"
              loading={signInStatus === 'authenticating'}
              loadingLabel={t('workspace.apiToolsets.connect.signingIn')}
              onClick={() => {
                setError(null);
                void startOAuthPopup(toolset.id);
              }}
            >
              {t(toolset.isConnected ? 'workspace.apiToolsets.connect.signInAgain' : 'workspace.apiToolsets.connect.signIn')}
            </LoadingButton>
          ) : null}

          {canManage && fields.length > 0 ? (
            <Flex direction="column" gap="3">
              {fields.map((field, index) => (
                <FormField key={field} label={t(`workspace.apiToolsets.connect.fields.${field}`)} required>
                  <LabeledTextField
                    size="2"
                    type={isSecretField(field) ? 'password' : 'text'}
                    autoComplete="off"
                    autoFocus={index === 0}
                    value={values[field] ?? ''}
                    onChange={(e) => setValues((v) => ({ ...v, [field]: e.target.value }))}
                  />
                </FormField>
              ))}
              {toolset.isConnected ? (
                <Text size="1" style={{ color: 'var(--gray-11)' }}>
                  {t('workspace.apiToolsets.connect.replaceHint')}
                </Text>
              ) : null}
            </Flex>
          ) : null}

          {error ? (
            <Callout.Root color="red" variant="surface" size="1">
              <Callout.Text>{error}</Callout.Text>
            </Callout.Root>
          ) : null}

          {testResult ? (
            <Flex direction="column" gap="2">
              <Flex align="center" gap="2">
                <Badge color={testResult.ok ? 'jade' : 'red'}>{testResult.status}</Badge>
                <Text size="2">
                  {t(testResult.ok ? 'workspace.apiToolsets.connect.testOk' : 'workspace.apiToolsets.connect.testFailed')}
                </Text>
              </Flex>
              {testResult.body ? (
                <Box
                  asChild
                  style={{
                    maxHeight: 200,
                    overflow: 'auto',
                    margin: 0,
                    padding: 'var(--space-2)',
                    background: 'var(--gray-a2)',
                    borderRadius: 'var(--radius-2)',
                    fontFamily: 'var(--code-font-family)',
                    fontSize: 12,
                    whiteSpace: 'pre-wrap',
                    overflowWrap: 'anywhere',
                  }}
                >
                  <pre>{testResult.body}</pre>
                </Box>
              ) : null}
              {testResult.truncated ? (
                <Text size="1" style={{ color: 'var(--gray-11)' }}>
                  {t('workspace.apiToolsets.connect.testTruncated')}
                </Text>
              ) : null}
            </Flex>
          ) : null}
        </Flex>

        <Flex justify="between" gap="2" mt="5" wrap="wrap">
          <Flex gap="2">
            <LoadingButton
              size="2"
              variant="outline"
              color="gray"
              disabled={busy !== null}
              loading={busy === 'test'}
              loadingLabel={t('workspace.apiToolsets.connect.testing')}
              onClick={() => void test()}
            >
              {t('workspace.apiToolsets.connect.test')}
            </LoadingButton>
            {canManage && toolset.isConnected ? (
              <LoadingButton
                size="2"
                variant="soft"
                color="red"
                disabled={busy !== null}
                loading={busy === 'disconnect'}
                loadingLabel={t('workspace.apiToolsets.connect.disconnecting')}
                onClick={() => void disconnect()}
              >
                {t('workspace.apiToolsets.connect.disconnect')}
              </LoadingButton>
            ) : null}
          </Flex>
          <Flex gap="2">
            <Dialog.Close>
              <Button variant="soft" color="gray" size="2" disabled={busy !== null}>
                {t('common.close')}
              </Button>
            </Dialog.Close>
            {canManage && fields.length > 0 ? (
              <LoadingButton
                size="2"
                disabled={!credentials || busy !== null}
                loading={busy === 'save'}
                loadingLabel={t('common.saving')}
                onClick={() => void save()}
              >
                {t('common.save')}
              </LoadingButton>
            ) : null}
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
