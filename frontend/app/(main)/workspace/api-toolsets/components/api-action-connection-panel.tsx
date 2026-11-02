'use client';

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Callout, Flex, Separator, Text } from '@radix-ui/themes';
import { toast } from '@/lib/store/toast-store';
import { getUserFacingErrorMessage } from '@/lib/api/api-error';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FormField, WorkspaceRightPanel } from '../../components';
import { mcpOAuthFailureMessage, useMcpOAuthPopup } from '../../mcp-servers/hooks/use-mcp-oauth-popup';
import { ApiToolsetsApi } from '../api';
import {
  buildCredentials,
  connectionMode,
  connectionStatus,
  credentialFields,
  isSecretField,
  usesSignIn,
  type CredentialField,
} from '../toolset-form';
import type { ApiToolset, ApiToolsetTestResult } from '../types';
import { ApiOperationsChecklist } from './api-operations-checklist';
import { LabeledTextField } from './labeled-controls';
import { PanelSection } from './panel-section';

interface ApiActionConnectionPanelProps {
  toolset: ApiToolset | null;
  isAdmin: boolean;
  onOpenChange: (open: boolean) => void;
  /** The connection changed: reload the list. */
  onChanged: () => void;
}

/** Connect, test and disconnect one API action: the viewer's own credential, or the shared one for an admin. */
export function ApiActionConnectionPanel({ toolset, isAdmin, onOpenChange, onChanged }: ApiActionConnectionPanelProps) {
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
    desktopFamily: 'toolset',
    completeCallback: ApiToolsetsApi.completeOAuthCallback,
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
  const canSave = canManage && fields.length > 0;
  const status = connectionStatus(toolset);

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
    <WorkspaceRightPanel
      open
      onOpenChange={(v) => busy === null && onOpenChange(v)}
      title={t('workspace.apiToolsets.connect.panelTitle')}
      icon={<MaterialIcon name="api" size={20} color="var(--accent-11)" />}
      hideFooter={!canSave}
      primaryLabel={t('common.save')}
      secondaryLabel={t('common.close')}
      primaryDisabled={!credentials || busy !== null}
      primaryLoading={busy === 'save'}
      onPrimaryClick={() => void save()}
      onSecondaryClick={() => onOpenChange(false)}
    >
      <Flex direction="column" gap="6" style={{ minWidth: 0, padding: '4px 0' }}>
        <Box>
          <Text as="div" size="4" weight="bold" style={{ color: 'var(--slate-12)', overflowWrap: 'anywhere' }}>
            {toolset.name}
          </Text>
          <Text as="div" size="2" color="gray" mt="1" style={{ lineHeight: 1.5 }}>
            {toolset.description || toolset.specTitle}
          </Text>
          <Flex gap="2" mt="3" wrap="wrap">
            <Badge variant="soft" color="gray">
              {t(`workspace.apiToolsets.authType.${toolset.authType}`)}
            </Badge>
            {toolset.useSharedCredential ? (
              <Badge variant="soft" color="blue">
                {t('workspace.apiToolsets.status.shared')}
              </Badge>
            ) : null}
          </Flex>
        </Box>

        <Separator size="4" />

        <PanelSection
          title={t('workspace.apiToolsets.connect.connectionHeading')}
          description={t(`workspace.apiToolsets.connect.mode.${mode}`)}
        >
          <Flex align="center" justify="between" gap="2" wrap="wrap">
            <Flex align="center" gap="2">
              <Text size="2" weight="medium">
                {t('workspace.apiToolsets.connect.status')}
              </Text>
              <Badge color={status === 'notConnected' ? 'amber' : status === 'connected' ? 'jade' : 'gray'}>
                {t(`workspace.apiToolsets.status.${status}`)}
              </Badge>
            </Flex>
            {canManage && toolset.isConnected ? (
              <LoadingButton
                size="1"
                variant="ghost"
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
              <MaterialIcon name="login" size={16} />
              {t(toolset.isConnected ? 'workspace.apiToolsets.connect.signInAgain' : 'workspace.apiToolsets.connect.signIn')}
            </LoadingButton>
          ) : null}

          {canSave
            ? fields.map((field, index) => (
                <FormField key={field} label={t(`workspace.apiToolsets.connect.fields.${field}`)} required>
                  <LabeledTextField
                    size="2"
                    type={isSecretField(field) ? 'password' : 'text'}
                    autoComplete="off"
                    autoFocus={index === 0 && !toolset.isConnected}
                    placeholder={toolset.isConnected ? t('workspace.apiToolsets.connect.replaceHint') : undefined}
                    value={values[field] ?? ''}
                    onChange={(e) => setValues((v) => ({ ...v, [field]: e.target.value }))}
                  />
                </FormField>
              ))
            : null}
        </PanelSection>

        <PanelSection
          title={t('workspace.apiToolsets.connect.testHeading')}
          description={t('workspace.apiToolsets.connect.testHint')}
          aside={
            <Button
              size="1"
              variant="soft"
              color="gray"
              disabled={busy !== null || status === 'notConnected'}
              onClick={() => void test()}
              style={{ cursor: 'pointer' }}
            >
              <MaterialIcon name="play_arrow" size={16} />
              {t(busy === 'test' ? 'workspace.apiToolsets.connect.testing' : 'workspace.apiToolsets.connect.test')}
            </Button>
          }
        >
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
                    maxHeight: 220,
                    overflow: 'auto',
                    margin: 0,
                    padding: 'var(--space-3)',
                    background: 'var(--color-surface)',
                    border: '1px solid var(--olive-4)',
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
          ) : (
            <Text size="1" style={{ color: 'var(--gray-10)' }}>
              {t(status === 'notConnected' ? 'workspace.apiToolsets.connect.testNeedsConnection' : 'workspace.apiToolsets.connect.testIdle')}
            </Text>
          )}
        </PanelSection>

        {error ? (
          <Callout.Root color="red" variant="surface" size="1">
            <Callout.Icon>
              <MaterialIcon name="error_outline" size={16} />
            </Callout.Icon>
            <Callout.Text>{error}</Callout.Text>
          </Callout.Root>
        ) : null}

        <Flex direction="column" gap="3">
          <Text size="2" weight="bold" style={{ color: 'var(--slate-12)' }}>
            {t('workspace.apiToolsets.connect.operationsHeading', { count: toolset.operations.length })}
          </Text>
          <ApiOperationsChecklist operations={toolset.operations} />
        </Flex>
      </Flex>
    </WorkspaceRightPanel>
  );
}
