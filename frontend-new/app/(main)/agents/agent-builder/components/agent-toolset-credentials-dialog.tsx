'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Badge,
  Box,
  Button,
  Callout,
  Dialog,
  Flex,
  IconButton,
  Separator,
  Spinner,
  Text,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SchemaFormField } from '@/app/(main)/workspace/connectors/components/schema-form-field';
import type { AuthSchemaField } from '@/app/(main)/workspace/connectors/types';
import { isNoneAuthType, isOAuthType, isCredentialAuthType } from '@/app/(main)/workspace/connectors/utils/auth-helpers';
import { formatAuthTypeName } from '@/app/(main)/workspace/connectors/components/authenticate-tab/helpers';
import { ToolsetsApi, type BuilderSidebarToolset } from '../../toolsets-api';
import {
  apiErrorDetail,
  authFieldsForType,
  getToolsetAuthConfigFromSchema,
} from './toolset-agent-auth-helpers';
import {
  toolsetDialogActionGridStyle,
  toolsetDialogBackdropStyle,
  toolsetDialogPanelStyle,
} from './toolset-config-dialog-styles';
import { useToolsetOauthPopupFlow } from '../hooks/use-toolset-oauth-popup-flow';

export interface AgentToolsetCredentialsDialogProps {
  toolset: BuilderSidebarToolset;
  instanceId: string;
  agentKey: string;
  onClose: () => void;
  onSuccess: () => void;
  /** Optional banner / toast line (e.g. OAuth success or cancelled). */
  onNotify?: (message: string) => void;
}

export function AgentToolsetCredentialsDialog({
  toolset,
  instanceId,
  agentKey,
  onClose,
  onSuccess,
  onNotify,
}: AgentToolsetCredentialsDialogProps) {
  const { t } = useTranslation();
  const authType = (toolset.authType || 'NONE').toUpperCase();
  const displayName = toolset.displayName || toolset.instanceName || t('agentBuilder.toolsetDefaultName');
  const subtitle =
    toolset.instanceName && toolset.instanceName !== displayName ? toolset.instanceName : null;
  const iconPath = toolset.iconPath || '';
  const tools = toolset.tools || [];
  const [iconBroken, setIconBroken] = useState(false);
  const [toolsExpanded, setToolsExpanded] = useState(false);

  const [schemaRaw, setSchemaRaw] = useState<unknown>(null);
  const [schemaLoading, setSchemaLoading] = useState(true);
  const [formData, setFormData] = useState<Record<string, unknown>>({});
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});
  const [saveAttempted, setSaveAttempted] = useState(false);

  const [saving, setSaving] = useState(false);
  const [reauthenticating, setReauthenticating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [removeConfirmOpen, setRemoveConfirmOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isAuthenticated, setIsAuthenticated] = useState(toolset.isAuthenticated ?? false);

  useEffect(() => {
    setIsAuthenticated(toolset.isAuthenticated ?? false);
  }, [toolset.isAuthenticated]);

  useEffect(() => {
    setIconBroken(false);
  }, [iconPath]);

  const authConfig = useMemo(() => getToolsetAuthConfigFromSchema(schemaRaw), [schemaRaw]);

  const manageFields: AuthSchemaField[] = useMemo(
    () => authFieldsForType(authConfig, authType),
    [authConfig, authType]
  );

  useEffect(() => {
    const toolsetType = toolset.toolsetType?.trim();
    if (!toolsetType) {
      setSchemaLoading(false);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        setSchemaLoading(true);
        const s = await ToolsetsApi.getToolsetRegistrySchema(toolsetType);
        if (!cancelled) setSchemaRaw(s);
      } catch {
        if (!cancelled) setSchemaRaw(null);
      } finally {
        if (!cancelled) setSchemaLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [toolset.toolsetType]);

  useEffect(() => {
    if (!toolset.auth || authType === 'OAUTH' || isNoneAuthType(authType)) return;
    const hydrated: Record<string, unknown> = {};
    manageFields.forEach((field) => {
      const v = toolset.auth?.[field.name];
      if (v !== undefined && v !== null) {
        hydrated[field.name] = Array.isArray(v) ? v.join(',') : v;
      }
    });
    if (Object.keys(hydrated).length > 0) {
      setFormData((prev) => ({ ...hydrated, ...prev }));
    }
  }, [toolset.auth, authType, manageFields]);

  const setField = useCallback((name: string, value: unknown) => {
    setFormData((p) => ({ ...p, [name]: value }));
    setFormErrors((p) => {
      const n = { ...p };
      delete n[name];
      return n;
    });
  }, []);

  const validateForm = useCallback(() => {
    const errors: Record<string, string> = {};
    manageFields.forEach((field) => {
      const value = formData[field.name];
      if (field.required && (value === undefined || value === null || String(value).trim() === '')) {
        errors[field.name] = t('agentBuilder.fieldRequired', { field: field.displayName });
      }
    });
    setFormErrors(errors);
    return Object.keys(errors).length === 0;
  }, [manageFields, formData, t]);

  const verifyOAuthComplete = useCallback(async (): Promise<boolean> => {
    try {
      const row = await ToolsetsApi.findAgentToolsetByInstanceId(agentKey, instanceId);
      return Boolean(row?.isAuthenticated);
    } catch {
      return false;
    }
  }, [agentKey, instanceId]);

  const onOAuthVerified = useCallback(() => {
    setIsAuthenticated(true);
    onSuccess();
  }, [onSuccess]);

  const onOAuthIncomplete = useCallback(() => {
    setError(t('agentBuilder.oauthSignInIncomplete'));
  }, [t]);

  const { authenticating, authenticatingRef, beginOAuth, cancelForUserDismissal } = useToolsetOauthPopupFlow({
    t,
    verifyAuthenticated: verifyOAuthComplete,
    onVerified: onOAuthVerified,
    onNotify,
    onIncomplete: onOAuthIncomplete,
  });

  const dismissLocked = saving || deleting || reauthenticating;

  const requestDismiss = useCallback(() => {
    if (dismissLocked) return;
    if (authenticatingRef.current) {
      cancelForUserDismissal();
    }
    onClose();
  }, [authenticatingRef, cancelForUserDismissal, dismissLocked, onClose]);

  const handleSaveCredentials = async () => {
    setSaveAttempted(true);
    if (!validateForm()) {
      setError(t('agentBuilder.fillRequiredFields'));
      return;
    }
    try {
      setSaving(true);
      setError(null);
      if (isAuthenticated) {
        await ToolsetsApi.updateAgentToolsetCredentials(agentKey, instanceId, formData);
      } else {
        await ToolsetsApi.authenticateAgentToolset(agentKey, instanceId, formData);
      }
      setIsAuthenticated(true);
      onNotify?.(t('agentBuilder.toolsetAuthUpdated'));
      onSuccess();
    } catch (e) {
      setError(apiErrorDetail(e));
    } finally {
      setSaving(false);
    }
  };

  const handleOAuthAuthenticate = async () => {
    setError(null);
    await beginOAuth(
      async () => {
        const result = await ToolsetsApi.getAgentToolsetOAuthUrl(
          agentKey,
          instanceId,
          typeof window !== 'undefined' ? window.location.origin : undefined
        );
        if (!result.success || !result.authorizationUrl) {
          throw new Error(t('agentBuilder.oauthUrlFailed'));
        }
        return {
          authorizationUrl: result.authorizationUrl,
          windowName: 'oauth_agent_toolset',
        };
      },
      {
        onTimeout: () => setError(t('agentBuilder.authTimeout')),
        onOpenError: (e) => setError(apiErrorDetail(e)),
      }
    );
  };

  const handleReauthenticate = async () => {
    try {
      setReauthenticating(true);
      setError(null);
      await ToolsetsApi.reauthenticateAgentToolset(agentKey, instanceId);
      setIsAuthenticated(false);
      onNotify?.(t('agentBuilder.toolsetAuthUpdated'));
      onSuccess();
    } catch (e) {
      setError(apiErrorDetail(e));
    } finally {
      setReauthenticating(false);
    }
  };

  const handleRemoveConfirmed = async () => {
    setRemoveConfirmOpen(false);
    try {
      setDeleting(true);
      setError(null);
      await ToolsetsApi.removeAgentToolsetCredentials(agentKey, instanceId);
      setIsAuthenticated(false);
      onNotify?.(t('agentBuilder.toolsetAuthUpdated'));
      onSuccess();
    } catch (e) {
      setError(apiErrorDetail(e));
    } finally {
      setDeleting(false);
    }
  };

  const busy = saving || authenticating || deleting || reauthenticating;

  const handleMainOpenChange = (open: boolean) => {
    if (!open && !dismissLocked) requestDismiss();
  };

  return (
    <>
      <Dialog.Root open onOpenChange={handleMainOpenChange}>
        <Box
          style={{
            ...toolsetDialogBackdropStyle,
            cursor: dismissLocked ? 'not-allowed' : 'pointer',
          }}
          onClick={() => requestDismiss()}
        />
        <Dialog.Content style={{ ...toolsetDialogPanelStyle, maxHeight: 'min(90vh, 44rem)', overflow: 'auto' }}>
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
                {iconPath && !iconBroken ? (
                  <img
                    src={iconPath}
                    alt=""
                    width={32}
                    height={32}
                    style={{ objectFit: 'contain' }}
                    onError={() => setIconBroken(true)}
                  />
                ) : (
                  <MaterialIcon name="extension" size={28} color="var(--slate-11)" />
                )}
              </Box>
              <Box style={{ minWidth: 0 }}>
                <Dialog.Title style={{ marginBottom: 4 }}>{t('agentBuilder.toolsetCredentialsTitle')}</Dialog.Title>
                <Text size="3" weight="bold" style={{ color: 'var(--slate-12)', display: 'block' }}>
                  {displayName}
                </Text>
                {subtitle ? (
                  <Text size="2" style={{ color: 'var(--slate-11)', display: 'block', marginTop: 2 }}>
                    {subtitle}
                  </Text>
                ) : null}
                <Flex gap="2" wrap="wrap" mt="2">
                  <Badge size="1" color="gray">
                    {formatAuthTypeName(authType)}
                  </Badge>
                  <Badge size="1" color="gray" variant="surface">
                    {t('agentBuilder.toolsetCredentialsBadge')}
                  </Badge>
                </Flex>
              </Box>
            </Flex>
            <IconButton variant="ghost" color="gray" onClick={() => requestDismiss()} disabled={dismissLocked} aria-label={t('common.close')}>
              <MaterialIcon name="close" size={20} />
            </IconButton>
          </Flex>

          <Dialog.Description size="2" mb="3" style={{ color: 'var(--slate-11)' }}>
            {t('agentBuilder.toolsetCredentialsDesc')}
          </Dialog.Description>

          <Callout.Root color="jade" variant="surface" size="1" mb="3">
            <Callout.Icon>
              <MaterialIcon name="smart_toy" size={18} />
            </Callout.Icon>
            <Callout.Text size="1" style={{ color: 'var(--slate-11)' }}>
              {t('agentBuilder.agentCredentialScopeCallout')}
            </Callout.Text>
          </Callout.Root>

          {schemaLoading ? (
            <Flex align="center" gap="3" py="4" justify="center">
              <Spinner size="2" />
              <Text size="2" color="gray">
                {t('agentBuilder.loadingSchema')}
              </Text>
            </Flex>
          ) : null}

          {!schemaLoading && toolset.description ? (
            <Text size="2" mb="3" style={{ color: 'var(--slate-11)', lineHeight: 1.55 }}>
              {toolset.description}
            </Text>
          ) : null}

          {!schemaLoading && error ? (
            <Callout.Root color="red" variant="surface" size="1" mb="3">
              <Callout.Text style={{ flex: 1, minWidth: 0 }}>{error}</Callout.Text>
            </Callout.Root>
          ) : null}

          {!schemaLoading && isNoneAuthType(authType) ? (
            <Text size="2">{t('agentBuilder.noCredentialsRequired')}</Text>
          ) : null}

          {!schemaLoading && isOAuthType(authType) ? (
            <Flex direction="column" gap="3" width="100%">
              <Callout.Root color="blue" variant="surface" size="1">
                <Callout.Text size="1" style={{ color: 'var(--slate-11)' }}>
                  {isAuthenticated ? t('agentBuilder.oauthConnectedDesc') : t('agentBuilder.oauthPendingDesc')}
                </Callout.Text>
              </Callout.Root>
            </Flex>
          ) : null}

          {!schemaLoading && isCredentialAuthType(authType) && manageFields.length > 0 ? (
            <Flex direction="column" gap="4">
              <Separator size="4" />
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {t('agentBuilder.agentCredentialsFieldsHeading')}
              </Text>
              {manageFields.map((field) => (
                <SchemaFormField
                  key={field.name}
                  field={field}
                  value={formData[field.name]}
                  onChange={setField}
                  error={saveAttempted ? formErrors[field.name] : undefined}
                  disabled={busy}
                />
              ))}
              {isAuthenticated ? (
                <Callout.Root color="green" variant="surface" size="1">
                  <Callout.Text size="1">{t('agentBuilder.credentialUpdateHint')}</Callout.Text>
                </Callout.Root>
              ) : null}
            </Flex>
          ) : null}

          {!schemaLoading && isCredentialAuthType(authType) && manageFields.length === 0 ? (
            <Callout.Root color="amber" variant="surface" size="1" mt="2">
              <Callout.Text size="1">{t('agentBuilder.noCredentialFields')}</Callout.Text>
            </Callout.Root>
          ) : null}

          {!schemaLoading && tools.length > 0 ? (
            <Box mt="4">
              <Text size="2" weight="medium" mb="2" style={{ color: 'var(--slate-12)', display: 'block' }}>
                {t('agentBuilder.availableToolsHeading', { count: tools.length })}
              </Text>
              <Flex gap="2" wrap="wrap" align="center">
                {(toolsExpanded ? tools : tools.slice(0, 12)).map((tool) => (
                  <Badge key={tool.fullName || tool.name} size="1" color="gray" variant="surface">
                    {tool.name}
                  </Badge>
                ))}
                {tools.length > 12 ? (
                  <Button type="button" size="1" variant="soft" color="gray" onClick={() => setToolsExpanded((v) => !v)}>
                    {toolsExpanded ? t('agentBuilder.showFewerTools') : t('agentBuilder.moreItems', { count: tools.length - 12 })}
                  </Button>
                ) : null}
              </Flex>
            </Box>
          ) : null}

          <Separator size="4" my="4" />

          <Flex width="100%" gap="3" align="center" justify="between" wrap="wrap" style={{ minWidth: 0 }}>
            <Box style={{ flex: '1 1 14rem', minWidth: 0 }}>
              {!schemaLoading && isOAuthType(authType) ? (
                <Box style={toolsetDialogActionGridStyle}>
                  <Button size="2" onClick={() => void handleOAuthAuthenticate()} disabled={busy}>
                    {authenticating
                      ? t('agentBuilder.waitingOAuth')
                      : isAuthenticated
                        ? t('agentBuilder.reconnectOAuth')
                        : t('agentBuilder.authenticateOAuth')}
                  </Button>
                  {isAuthenticated ? (
                    <Button size="2" variant="soft" color="amber" onClick={() => void handleReauthenticate()} disabled={busy}>
                      {reauthenticating ? t('agentBuilder.working') : t('agentBuilder.reauthenticateCta')}
                    </Button>
                  ) : null}
                  {isAuthenticated ? (
                    <Button size="2" variant="soft" color="red" onClick={() => setRemoveConfirmOpen(true)} disabled={busy}>
                      {t('agentBuilder.removeCredentials')}
                    </Button>
                  ) : null}
                </Box>
              ) : null}

              {!schemaLoading && isCredentialAuthType(authType) && manageFields.length > 0 ? (
                <Box style={toolsetDialogActionGridStyle}>
                  <Button size="2" onClick={() => void handleSaveCredentials()} disabled={busy}>
                    {saving
                      ? t('agentBuilder.savingCredentials')
                      : isAuthenticated
                        ? t('agentBuilder.updateCredentials')
                        : t('agentBuilder.saveCredentials')}
                  </Button>
                  {isAuthenticated ? (
                    <Button size="2" variant="soft" color="amber" onClick={() => void handleReauthenticate()} disabled={busy}>
                      {reauthenticating ? t('agentBuilder.working') : t('agentBuilder.reauthenticateCta')}
                    </Button>
                  ) : null}
                  {isAuthenticated ? (
                    <Button size="2" variant="soft" color="red" onClick={() => setRemoveConfirmOpen(true)} disabled={busy}>
                      {t('agentBuilder.removeCredentials')}
                    </Button>
                  ) : null}
                </Box>
              ) : null}
            </Box>

            <Button size="2" variant="soft" color="gray" onClick={() => requestDismiss()} disabled={dismissLocked} style={{ flexShrink: 0 }}>
              {isAuthenticated ? t('common.close') : t('action.cancel')}
            </Button>
          </Flex>
          </Box>
        </Dialog.Content>
      </Dialog.Root>

      <Dialog.Root open={removeConfirmOpen} onOpenChange={setRemoveConfirmOpen}>
        <Box
          style={{
            ...toolsetDialogBackdropStyle,
            zIndex: 1001,
            cursor: deleting ? 'not-allowed' : 'pointer',
          }}
          onClick={() => !deleting && setRemoveConfirmOpen(false)}
        />
        <Dialog.Content
          style={{
            ...toolsetDialogPanelStyle,
            maxWidth: 'min(28rem, calc(100vw - 2rem))',
            zIndex: 1002,
          }}
        >
          <Dialog.Title>{t('agentBuilder.removeCredentialsTitle')}</Dialog.Title>
          <Text size="2" mb="3" style={{ color: 'var(--slate-11)' }}>
            {t('agentBuilder.removeCredentialsDesc', { name: displayName })}
          </Text>
          <Flex gap="2" justify="end">
            <Dialog.Close>
              <Button variant="soft" color="gray" disabled={deleting}>
                {t('action.cancel')}
              </Button>
            </Dialog.Close>
            <Button color="red" onClick={() => void handleRemoveConfirmed()} disabled={deleting}>
              {deleting ? t('agentBuilder.removing') : t('agentBuilder.remove')}
            </Button>
          </Flex>
        </Dialog.Content>
      </Dialog.Root>
    </>
  );
}
