'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Button,
  Dialog,
  Flex,
  IconButton,
  Text,
  Badge,
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

export interface AgentToolsetCredentialsDialogProps {
  toolset: BuilderSidebarToolset;
  instanceId: string;
  agentKey: string;
  onClose: () => void;
  onSuccess: () => void;
}

export function AgentToolsetCredentialsDialog({
  toolset,
  instanceId,
  agentKey,
  onClose,
  onSuccess,
}: AgentToolsetCredentialsDialogProps) {
  const { t } = useTranslation();
  const authType = (toolset.authType || 'NONE').toUpperCase();
  const displayName = toolset.displayName || toolset.instanceName || 'Toolset';
  const iconPath = toolset.iconPath || '';

  const [schemaRaw, setSchemaRaw] = useState<unknown>(null);
  const [schemaLoading, setSchemaLoading] = useState(true);
  const [formData, setFormData] = useState<Record<string, unknown>>({});
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});
  const [saveAttempted, setSaveAttempted] = useState(false);

  const [saving, setSaving] = useState(false);
  const [authenticating, setAuthenticating] = useState(false);
  const [reauthenticating, setReauthenticating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [removeConfirmOpen, setRemoveConfirmOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isAuthenticated, setIsAuthenticated] = useState(toolset.isAuthenticated ?? false);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(
    () => () => {
      if (pollRef.current) clearInterval(pollRef.current);
    },
    []
  );

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
      const res = await ToolsetsApi.getAgentToolsets(agentKey, {
        page: 1,
        limit: 200,
        includeRegistry: false,
      });
      const row = res.toolsets.find((t) => t.instanceId === instanceId);
      return Boolean(row?.isAuthenticated);
    } catch {
      return false;
    }
  }, [agentKey, instanceId]);

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
      onSuccess();
    } catch (e) {
      setError(apiErrorDetail(e));
    } finally {
      setSaving(false);
    }
  };

  const handleOAuthAuthenticate = async () => {
    try {
      setAuthenticating(true);
      setError(null);
      const result = await ToolsetsApi.getAgentToolsetOAuthUrl(
        agentKey,
        instanceId,
        typeof window !== 'undefined' ? window.location.origin : undefined
      );
      if (!result.success || !result.authorizationUrl) {
        throw new Error(t('agentBuilder.oauthUrlFailed'));
      }
      const w = 600;
      const h = 700;
      const left = window.screen.width / 2 - w / 2;
      const top = window.screen.height / 2 - h / 2;
      const popup = window.open(
        result.authorizationUrl,
        'oauth_agent_toolset',
        `width=${w},height=${h},left=${left},top=${top},scrollbars=yes,resizable=yes`
      );
      if (!popup) {
        throw new Error(t('agentBuilder.oauthPopupBlocked'));
      }
      popup.focus();

      let closedHandled = false;
      let pollCount = 0;
      const maxPolls = 300;

      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(() => {
        pollCount += 1;
        if (pollCount >= maxPolls) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          if (!popup.closed) popup.close();
          setAuthenticating(false);
          setError(t('agentBuilder.authTimeout'));
          return;
        }
        if (popup.closed && !closedHandled) {
          closedHandled = true;
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          const run = async () => {
            for (let attempt = 0; attempt < 5; attempt += 1) {
              await new Promise((r) => setTimeout(r, 1500));
              const ok = await verifyOAuthComplete();
              if (ok) {
                setIsAuthenticated(true);
                setAuthenticating(false);
                onSuccess();
                return;
              }
            }
            setAuthenticating(false);
            setError(t('agentBuilder.authNotCompleted'));
          };
          void run();
        }
      }, 1000);
    } catch (e) {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      setError(apiErrorDetail(e));
      setAuthenticating(false);
    }
  };

  const handleReauthenticate = async () => {
    try {
      setReauthenticating(true);
      setError(null);
      await ToolsetsApi.reauthenticateAgentToolset(agentKey, instanceId);
      setIsAuthenticated(false);
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
      onSuccess();
    } catch (e) {
      setError(apiErrorDetail(e));
    } finally {
      setDeleting(false);
    }
  };

  const busy = saving || authenticating || deleting || reauthenticating;

  return (
    <>
      <Dialog.Root open onOpenChange={(o) => !o && !busy && onClose()}>
        <Dialog.Content style={{ maxWidth: 480, maxHeight: '90vh', overflow: 'auto' }}>
          <Flex align="start" justify="between" gap="3" mb="3">
            <Flex align="center" gap="3" style={{ minWidth: 0 }}>
              {iconPath ? (
                <img src={iconPath} alt="" width={40} height={40} style={{ objectFit: 'contain' }} />
              ) : (
                <MaterialIcon name="extension" size={40} />
              )}
              <Box style={{ minWidth: 0 }}>
                <Dialog.Title style={{ marginBottom: 4 }}>{t('agentBuilder.toolsetCredentialsTitle')}</Dialog.Title>
                <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                  {displayName}
                </Text>
                <Flex gap="2" wrap="wrap" mt="2">
                  <Badge size="1" color="gray">
                    {formatAuthTypeName(authType)}
                  </Badge>
                  <Badge size="1" color="green">
                    {t('agentBuilder.toolsetCredentialsBadge')}
                  </Badge>
                </Flex>
              </Box>
            </Flex>
            <IconButton variant="ghost" color="gray" onClick={() => !busy && onClose()} aria-label={t('common.close')}>
              <MaterialIcon name="close" size={20} />
            </IconButton>
          </Flex>

          <Dialog.Description size="2" mb="3" style={{ color: 'var(--slate-11)' }}>
            {t('agentBuilder.toolsetCredentialsDesc')}
          </Dialog.Description>

          {schemaLoading ? (
            <Text size="2" color="gray">
              {t('agentBuilder.loadingSchema')}
            </Text>
          ) : null}

          {error ? (
            <Box mb="3" p="2" style={{ borderRadius: 'var(--radius-2)', background: 'var(--red-3)' }}>
              <Text size="2" color="red">
                {error}
              </Text>
            </Box>
          ) : null}

          {isNoneAuthType(authType) ? (
            <Text size="2">{t('agentBuilder.noCredentialsRequired')}</Text>
          ) : null}

          {isOAuthType(authType) ? (
            <Flex direction="column" gap="3">
              <Text size="2" color="gray">
                {isAuthenticated
                  ? t('agentBuilder.oauthConnectedDesc')
                  : t('agentBuilder.oauthPendingDesc')}
              </Text>
              <Flex gap="2" wrap="wrap">
                {!isAuthenticated ? (
                  <Button onClick={() => void handleOAuthAuthenticate()} disabled={busy}>
                    {authenticating ? t('agentBuilder.waitingOAuth') : t('agentBuilder.authenticateOAuth')}
                  </Button>
                ) : null}
                {isAuthenticated ? (
                  <Button variant="soft" color="amber" onClick={() => void handleReauthenticate()} disabled={busy}>
                    {reauthenticating ? t('agentBuilder.working') : t('agentBuilder.clearReauth')}
                  </Button>
                ) : null}
                <Button variant="soft" color="red" onClick={() => setRemoveConfirmOpen(true)} disabled={busy}>
                  {t('agentBuilder.removeCredentials')}
                </Button>
              </Flex>
            </Flex>
          ) : null}

          {isCredentialAuthType(authType) && manageFields.length > 0 ? (
            <Flex direction="column" gap="4">
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
              <Flex gap="2" wrap="wrap">
                <Button onClick={() => void handleSaveCredentials()} disabled={busy}>
                  {saving
                    ? t('agentBuilder.savingCredentials')
                    : isAuthenticated
                      ? t('agentBuilder.updateCredentials')
                      : t('agentBuilder.saveCredentials')}
                </Button>
                <Button variant="soft" color="red" onClick={() => setRemoveConfirmOpen(true)} disabled={busy}>
                  {t('agentBuilder.removeCredentials')}
                </Button>
              </Flex>
            </Flex>
          ) : null}

          {isCredentialAuthType(authType) && !schemaLoading && manageFields.length === 0 ? (
            <Text size="2" color="amber">
              {t('agentBuilder.noCredentialFields')}
            </Text>
          ) : null}

          <Flex justify="end" mt="4">
            <Button variant="soft" color="gray" onClick={() => !busy && onClose()}>
              {t('common.close')}
            </Button>
          </Flex>
        </Dialog.Content>
      </Dialog.Root>

      <Dialog.Root open={removeConfirmOpen} onOpenChange={setRemoveConfirmOpen}>
        <Dialog.Content style={{ maxWidth: 400 }}>
          <Dialog.Title>{t('agentBuilder.removeCredentialsTitle')}</Dialog.Title>
          <Text size="2" mb="3" style={{ color: 'var(--slate-11)' }}>
            {t('agentBuilder.removeCredentialsDesc', { name: displayName })}
          </Text>
          <Flex gap="2" justify="end">
            <Dialog.Close>
              <Button variant="soft" color="gray">
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
