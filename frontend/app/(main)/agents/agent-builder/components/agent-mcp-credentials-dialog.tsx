'use client';

import React, { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Callout, Dialog, Flex, IconButton, Separator, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { isProcessedError } from '@/lib/api';
import { McpServersApi } from '../../../workspace/mcp-servers/api';
import {
  buildMultiEnvAuthPayload,
  isMultiEnvAuthComplete,
  needsMultiEnvAuth,
} from '../../../workspace/mcp-servers/stdio-env-auth';
import type { McpMyServerEntry } from '../../../workspace/mcp-servers/types';
import { useMcpOAuthPopup } from '../../../workspace/mcp-servers/hooks/use-mcp-oauth-popup';
import {
  toolsetDialogBackdropStyle,
  toolsetDialogFooterPrimaryClusterStyle,
  toolsetDialogFooterToolbarStyle,
  toolsetDialogPanelStyle,
  toolsetDialogPrimaryActionsStyle,
} from './toolset-config-dialog-styles';

function extractErrorDetail(e: unknown): string | undefined {
  return isProcessedError(e) ? e.message : e instanceof Error ? e.message : undefined;
}

export interface McpCredentialsDialogProps {
  instance: McpMyServerEntry;
  /** Present + non-null → scope every call to the agent-key MCP proxy routes (service-account agents). */
  agentKey?: string | null;
  onClose: () => void;
  /** Refresh the sidebar's MCP list; must not block closing the dialog. */
  onSuccess: () => void | Promise<void>;
  onNotify?: (message: string) => void;
}

/**
 * Attach-time credentials dialog for one MCP server instance inside the agent builder —
 * mirrors `AgentToolsetCredentialsDialog` but far simpler, since MCP auth is just
 * api-token / header / OAuth (no registry-driven schema form). Scoped generically by
 * `agentKey`: personal (`McpServersApi.authenticate`/`getOAuthAuthorizationUrl`) when
 * absent, agent-key (`...AgentInstance`/`getAgentOAuthAuthorizationUrl`) when present.
 */
export function McpCredentialsDialog({
  instance,
  agentKey,
  onClose,
  onSuccess,
  onNotify,
}: McpCredentialsDialogProps) {
  const { t } = useTranslation();
  const isAgentScoped = Boolean(agentKey);
  const isHeaderMode = instance.authMode === 'headers';
  const isOAuthMode = instance.authMode === 'oauth';
  const isNoneMode = instance.authMode === 'none' || instance.useAdminAuth;

  const [apiToken, setApiToken] = useState('');
  const [headerValue, setHeaderValue] = useState('');
  const [envValues, setEnvValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [removeConfirmOpen, setRemoveConfirmOpen] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isAuthenticated, setIsAuthenticated] = useState(instance.isAuthenticated);

  const verifyAuthenticated = useCallback(async (): Promise<boolean> => {
    const found = agentKey
      ? await McpServersApi.findAgentMcpServerByInstanceId(agentKey, instance._id)
      : (await McpServersApi.getMyMcpServers(false)).instances.find((i) => i._id === instance._id);
    return Boolean(found?.isAuthenticated);
  }, [agentKey, instance._id]);

  const { startOAuthPopup, status: oauthStatus } = useMcpOAuthPopup({
    verifyAuthenticated,
    onVerified: () => {
      setIsAuthenticated(true);
      onNotify?.(t('agentBuilder.mcpAuthUpdatedNotify'));
      void Promise.resolve(onSuccess()).catch(() => undefined);
      onClose();
    },
    getAuthorizationUrl: agentKey
      ? (id: string) => McpServersApi.getAgentOAuthAuthorizationUrl(agentKey, id, window.location.origin)
      : undefined,
  });

  const authenticating = oauthStatus === 'authenticating';
  const busy = saving || removing || authenticating;

  const requiredEnv = instance.requiredEnv ?? [];
  const optionalEnv = instance.optionalEnv ?? [];
  const multiEnv = !isHeaderMode && !isOAuthMode && !isNoneMode && needsMultiEnvAuth(requiredEnv);
  const envKeys = multiEnv ? [...requiredEnv, ...optionalEnv] : [];

  const isFieldValid = isHeaderMode
    ? headerValue.trim().length > 0
    : multiEnv
      ? isMultiEnvAuthComplete(requiredEnv, envValues)
      : apiToken.trim().length > 0;

  const handleSaveCredentials = async () => {
    if (!isFieldValid) return;
    setSaving(true);
    setError(null);
    try {
      const payload = isHeaderMode
        ? { headerValue: headerValue.trim() }
        : multiEnv
          ? buildMultiEnvAuthPayload(requiredEnv, optionalEnv, envValues)
          : { apiToken: apiToken.trim() };
      if (isAgentScoped) {
        if (isAuthenticated) {
          await McpServersApi.updateAgentInstanceCredentials(agentKey!, instance._id, payload);
        } else {
          await McpServersApi.authenticateAgentInstance(agentKey!, instance._id, payload);
        }
      } else if (isAuthenticated) {
        await McpServersApi.updateCredentials(instance._id, payload);
      } else {
        await McpServersApi.authenticate(instance._id, payload);
      }
      setIsAuthenticated(true);
      onNotify?.(t('agentBuilder.mcpAuthUpdatedNotify'));
      await Promise.resolve(onSuccess()).catch(() => undefined);
      onClose();
    } catch (e) {
      setError(extractErrorDetail(e) || t('agentBuilder.mcpAuthSaveError'));
    } finally {
      setSaving(false);
    }
  };

  const handleRemoveConfirmed = async () => {
    setRemoveConfirmOpen(false);
    setRemoving(true);
    setError(null);
    try {
      if (isAgentScoped) {
        await McpServersApi.removeAgentInstanceCredentials(agentKey!, instance._id);
      } else {
        await McpServersApi.removeCredentials(instance._id);
      }
      setIsAuthenticated(false);
      onNotify?.(t('agentBuilder.mcpAuthUpdatedNotify'));
      await Promise.resolve(onSuccess()).catch(() => undefined);
      onClose();
    } catch (e) {
      setError(extractErrorDetail(e) || t('agentBuilder.mcpAuthRemoveError'));
    } finally {
      setRemoving(false);
    }
  };

  const handleOAuthConnect = async () => {
    setError(null);
    if (isAuthenticated) {
      try {
        if (isAgentScoped) await McpServersApi.reauthenticateAgentInstance(agentKey!, instance._id);
        else await McpServersApi.reauthenticate(instance._id);
      } catch (e) {
        setError(extractErrorDetail(e) || t('agentBuilder.mcpAuthSaveError'));
        return;
      }
    }
    await startOAuthPopup(instance._id);
  };

  const displayName = instance.name || instance.typeId || t('agentBuilder.mcpServerDefaultName');
  const tools = instance.tools || [];

  const showFooterPrimaryCluster = isOAuthMode || (!isOAuthMode && !isNoneMode);

  return (
    <>
      <Dialog.Root open onOpenChange={(open) => !open && !busy && onClose()}>
        <Box
          style={{ ...toolsetDialogBackdropStyle, cursor: busy ? 'not-allowed' : 'pointer' }}
          onClick={() => !busy && onClose()}
        />
        <Dialog.Content style={{ ...toolsetDialogPanelStyle, maxHeight: 'min(90vh, 40rem)', overflow: 'auto' }}>
          <Box style={{ width: '100%', minWidth: 0 }}>
            <Flex align="start" justify="between" gap="3" mb="3">
              <Flex align="center" gap="3" style={{ minWidth: 0, flex: 1 }}>
                <Box
                  style={{
                    width: 44,
                    height: 44,
                    borderRadius: 'var(--radius-3)',
                    border: '1px solid var(--gray-a4)',
                    background: 'var(--gray-2)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexShrink: 0,
                  }}
                >
                  <MaterialIcon name="hub" size={28} color="var(--slate-11)" />
                </Box>
                <Box style={{ minWidth: 0 }}>
                  <Dialog.Title style={{ marginBottom: 4 }}>{t('agentBuilder.mcpCredentialsTitle')}</Dialog.Title>
                  <Text size="3" weight="bold" style={{ color: 'var(--slate-12)', display: 'block' }}>
                    {displayName}
                  </Text>
                  <Flex gap="2" wrap="wrap" mt="2">
                    <Badge size="1" color="gray">
                      {instance.authMode}
                    </Badge>
                  </Flex>
                </Box>
              </Flex>
              <IconButton variant="ghost" color="gray" onClick={() => !busy && onClose()} disabled={busy} aria-label={t('common.close')}>
                <MaterialIcon name="close" size={20} />
              </IconButton>
            </Flex>

            <Dialog.Description size="2" mb="3" style={{ color: 'var(--slate-11)' }}>
              {t('agentBuilder.mcpCredentialsDesc')}
            </Dialog.Description>

            {isAgentScoped ? (
              <Callout.Root color="jade" variant="surface" size="1" mb="3">
                <Callout.Icon>
                  <MaterialIcon name="smart_toy" size={18} />
                </Callout.Icon>
                <Callout.Text size="1" style={{ color: 'var(--slate-11)' }}>
                  {t('agentBuilder.agentCredentialScopeCallout')}
                </Callout.Text>
              </Callout.Root>
            ) : null}

            {error ? (
              <Callout.Root color="red" variant="surface" size="1" mb="3">
                <Callout.Text style={{ flex: 1, minWidth: 0 }}>{error}</Callout.Text>
              </Callout.Root>
            ) : null}

            {isNoneMode ? <Text size="2">{t('agentBuilder.noCredentialsRequired')}</Text> : null}

            {isOAuthMode ? (
              <Callout.Root color="blue" variant="surface" size="1">
                <Callout.Icon>
                  <MaterialIcon name={isAuthenticated ? 'verified_user' : 'link'} size={18} />
                </Callout.Icon>
                <Callout.Text size="1" style={{ color: 'var(--slate-11)' }}>
                  {isAuthenticated ? t('agentBuilder.oauthConnectedDesc') : t('agentBuilder.oauthPendingDesc')}
                </Callout.Text>
              </Callout.Root>
            ) : null}

            {!isOAuthMode && !isNoneMode ? (
              <Flex direction="column" gap="4">
                <Separator size="4" />
                {isHeaderMode ? (
                  <Box>
                    <Text size="2" weight="medium" mb="2" style={{ color: 'var(--slate-12)', display: 'block' }}>
                      {t('workspace.mcpServers.form.headerValue', { headerName: instance.headerName || 'Authorization' })}
                    </Text>
                    <TextField.Root
                      size="2"
                      type="password"
                      value={headerValue}
                      onChange={(e) => setHeaderValue(e.target.value)}
                      disabled={busy}
                      autoFocus
                    />
                  </Box>
                ) : multiEnv ? (
                  envKeys.map((envKey, index) => (
                    <Box key={envKey}>
                      <Text size="2" weight="medium" mb="2" style={{ color: 'var(--slate-12)', display: 'block' }}>
                        {envKey}
                        {!requiredEnv.includes(envKey) ? ' (optional)' : ''}
                      </Text>
                      <TextField.Root
                        size="2"
                        type={envKey.toLowerCase().includes('token') || envKey.toLowerCase().includes('key') ? 'password' : 'text'}
                        value={envValues[envKey] ?? ''}
                        onChange={(e) => setEnvValues((prev) => ({ ...prev, [envKey]: e.target.value }))}
                        disabled={busy}
                        autoFocus={index === 0}
                      />
                    </Box>
                  ))
                ) : (
                  <Box>
                    <Text size="2" weight="medium" mb="2" style={{ color: 'var(--slate-12)', display: 'block' }}>
                      {t('workspace.mcpServers.form.apiToken')}
                    </Text>
                    <TextField.Root
                      size="2"
                      type="password"
                      value={apiToken}
                      onChange={(e) => setApiToken(e.target.value)}
                      disabled={busy}
                      autoFocus
                    />
                  </Box>
                )}
                {isAuthenticated ? (
                  <Callout.Root color="green" variant="surface" size="1">
                    <Callout.Text size="1">{t('agentBuilder.credentialUpdateHint')}</Callout.Text>
                  </Callout.Root>
                ) : null}
              </Flex>
            ) : null}

            {tools.length > 0 ? (
              <Box mt="4">
                <Text size="2" weight="medium" mb="2" style={{ color: 'var(--slate-12)', display: 'block' }}>
                  {t('agentBuilder.availableToolsHeading', { count: tools.length })}
                </Text>
                <Flex gap="2" wrap="wrap" align="center">
                  {tools.slice(0, 12).map((tool) => (
                    <Badge key={tool.namespacedName || tool.name} size="1" color="gray" variant="surface">
                      {tool.name}
                    </Badge>
                  ))}
                  {tools.length > 12 ? (
                    <Badge size="1" color="gray" variant="soft">
                      {t('agentBuilder.moreItems', { count: tools.length - 12 })}
                    </Badge>
                  ) : null}
                </Flex>
              </Box>
            ) : null}

            <Separator size="4" my="4" />

            {showFooterPrimaryCluster ? (
              <Box style={toolsetDialogFooterToolbarStyle}>
                {isOAuthMode ? (
                  <Flex wrap="wrap" gap="2" style={{ ...toolsetDialogPrimaryActionsStyle, ...toolsetDialogFooterPrimaryClusterStyle }}>
                    <Button size="2" variant="soft" color="green" onClick={() => void handleOAuthConnect()} disabled={busy}>
                      {authenticating
                        ? t('agentBuilder.waitingOAuth')
                        : isAuthenticated
                          ? t('agentBuilder.reconnectOAuth')
                          : t('agentBuilder.authenticateOAuth')}
                    </Button>
                    {isAuthenticated ? (
                      <Button size="2" variant="soft" color="red" onClick={() => setRemoveConfirmOpen(true)} disabled={busy}>
                        {t('agentBuilder.disconnectOAuth')}
                      </Button>
                    ) : null}
                  </Flex>
                ) : (
                  <Flex wrap="wrap" gap="2" style={{ ...toolsetDialogPrimaryActionsStyle, ...toolsetDialogFooterPrimaryClusterStyle }}>
                    <LoadingButton
                      size="2"
                      variant="soft"
                      color="green"
                      onClick={() => void handleSaveCredentials()}
                      disabled={!isFieldValid || (busy && !saving)}
                      loading={saving}
                      loadingLabel={t('agentBuilder.savingCredentials')}
                    >
                      {isAuthenticated ? t('agentBuilder.updateCredentials') : t('agentBuilder.saveCredentials')}
                    </LoadingButton>
                    {isAuthenticated ? (
                      <Button size="2" variant="soft" color="red" onClick={() => setRemoveConfirmOpen(true)} disabled={busy}>
                        {t('agentBuilder.removeCredentials')}
                      </Button>
                    ) : null}
                  </Flex>
                )}
                <Box style={{ flexShrink: 0, marginInlineStart: 'auto' }}>
                  <Button size="2" variant="soft" color="gray" onClick={() => !busy && onClose()} disabled={busy}>
                    {isAuthenticated ? t('common.close') : t('action.cancel')}
                  </Button>
                </Box>
              </Box>
            ) : (
              <Flex justify="end" width="100%">
                <Button size="2" variant="soft" color="gray" onClick={() => !busy && onClose()} disabled={busy}>
                  {t('common.close')}
                </Button>
              </Flex>
            )}
          </Box>
        </Dialog.Content>
      </Dialog.Root>

      <Dialog.Root open={removeConfirmOpen} onOpenChange={setRemoveConfirmOpen}>
        <Box
          style={{ ...toolsetDialogBackdropStyle, zIndex: 1001, cursor: removing ? 'not-allowed' : 'pointer' }}
          onClick={() => !removing && setRemoveConfirmOpen(false)}
        />
        <Dialog.Content style={{ ...toolsetDialogPanelStyle, maxWidth: 'min(28rem, calc(100vw - 2rem))', zIndex: 1002 }}>
          <Dialog.Title>
            {isOAuthMode
              ? t('agentBuilder.disconnectOAuthTitle', { name: displayName })
              : t('agentBuilder.removeCredentialsTitle')}
          </Dialog.Title>
          <Text size="2" mb="3" style={{ color: 'var(--slate-11)' }}>
            {isOAuthMode
              ? t('agentBuilder.disconnectOAuthDesc', { name: displayName })
              : t('agentBuilder.removeCredentialsDesc', { name: displayName })}
          </Text>
          <Flex gap="2" justify="end">
            <Dialog.Close>
              <Button variant="soft" color="gray" disabled={removing}>
                {t('action.cancel')}
              </Button>
            </Dialog.Close>
            <Button color="red" onClick={() => void handleRemoveConfirmed()} disabled={removing}>
              {removing
                ? isOAuthMode
                  ? t('agentBuilder.disconnectOAuthProgress')
                  : t('agentBuilder.removing')
                : isOAuthMode
                  ? t('agentBuilder.disconnectOAuth')
                  : t('agentBuilder.remove')}
            </Button>
          </Flex>
        </Dialog.Content>
      </Dialog.Root>
    </>
  );
}
