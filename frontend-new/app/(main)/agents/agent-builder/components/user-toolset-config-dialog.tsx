'use client';

import React, { startTransition, useCallback, useEffect, useMemo, useState } from 'react';
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
import { LoadingButton } from '@/app/components/ui/loading-button';
import { SchemaFormField } from '@/app/(main)/workspace/connectors/components/schema-form-field';
import type { AuthSchemaField } from '@/app/(main)/workspace/connectors/types';
import {
  isNoneAuthType,
  isOAuthType,
  isCredentialAuthType,
} from '@/app/(main)/workspace/connectors/utils/auth-helpers';
import { formatAuthTypeName } from '@/app/(main)/workspace/connectors/components/authenticate-tab/helpers';
import { ToolsetsApi, type BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import {
  apiErrorDetail,
  authFieldsForType,
  getToolsetAuthConfigFromSchema,
  isOrgOAuthAppCredentialFieldName,
} from './toolset-agent-auth-helpers';
import {
  toolsetDialogFooterPrimaryClusterStyle,
  toolsetDialogFooterToolbarStyle,
  toolsetDialogPanelStyle,
  toolsetDialogPrimaryActionsStyle,
} from './toolset-config-dialog-styles';
import { useToolsetOauthPopupFlow } from '../hooks/use-toolset-oauth-popup-flow';
import {
  useWorkspaceDrawerNestedModalHost,
  useWorkspaceRightPanelBodyRefresh,
  WORKSPACE_DRAWER_POPPER_Z_INDEX,
} from '@/app/(main)/workspace/components/workspace-right-panel';

export interface UserToolsetConfigDialogProps {
  toolset: BuilderSidebarToolset;
  instanceId: string;
  onClose: () => void;
  /** Refresh toolsets / follow-up work; must not block closing the dialog. */
  onSuccess: () => void | Promise<void>;
  onNotify?: (message: string) => void;
  /** When true, renders body only (no modal shell) for use inside e.g. `WorkspaceRightPanel`. */
  embedded?: boolean;
}

/**
 * User-scoped toolset auth (workspace “my toolsets”): OAuth popup + verify, non-OAuth fields,
 * and remove credentials (OAuth uses reauthenticate; non-OAuth uses DELETE credentials).
 */
export function UserToolsetConfigDialog({
  toolset,
  instanceId,
  onClose,
  onSuccess,
  onNotify,
  embedded = false,
}: UserToolsetConfigDialogProps) {
  const { t } = useTranslation();
  const authType = (toolset.authType || 'NONE').toUpperCase();
  const productName =
    toolset.displayName || toolset.name || t('agentBuilder.toolsetDefaultName');
  const instanceLabel = (toolset.instanceName ?? '').trim();
  const displayName = instanceLabel || productName;
  const subtitle =
    instanceLabel && productName && instanceLabel !== productName ? productName : null;
  const iconPath = toolset.iconPath || '';
  const [iconBroken, setIconBroken] = useState(false);

  const [schemaRaw, setSchemaRaw] = useState<unknown>(null);
  const [schemaLoading, setSchemaLoading] = useState(true);
  const [formData, setFormData] = useState<Record<string, unknown>>({});
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});
  const [saveAttempted, setSaveAttempted] = useState(false);

  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [removeConfirmOpen, setRemoveConfirmOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isAuthenticated, setIsAuthenticated] = useState(toolset.isAuthenticated ?? false);

  const nestedModalHost = useWorkspaceDrawerNestedModalHost(embedded);
  const drawerBodyRefresh = useWorkspaceRightPanelBodyRefresh();

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

  /** End users must never see org OAuth app secrets (client id / secret), even if mis-tagged in schema. */
  const userCredentialFields = useMemo(
    () => manageFields.filter((f) => !isOrgOAuthAppCredentialFieldName(f.name)),
    [manageFields]
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
    userCredentialFields.forEach((field) => {
      const v = toolset.auth?.[field.name];
      if (v !== undefined && v !== null) {
        hydrated[field.name] = Array.isArray(v) ? v.join(',') : v;
      }
    });
    if (Object.keys(hydrated).length > 0) {
      setFormData((prev) => ({ ...hydrated, ...prev }));
    }
  }, [toolset.auth, authType, userCredentialFields]);

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
    userCredentialFields.forEach((field) => {
      const value = formData[field.name];
      if (field.required && (value === undefined || value === null || String(value).trim() === '')) {
        errors[field.name] = t('agentBuilder.fieldRequired', { field: field.displayName });
      }
    });
    setFormErrors(errors);
    if (Object.keys(errors).length > 0) {
      requestAnimationFrame(() => {
        const first = Object.keys(errors)[0];
        document
          .querySelector(`[data-ph-auth-field="${first}"]`)
          ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
    }
    return Object.keys(errors).length === 0;
  }, [userCredentialFields, formData, t]);

  const verifyOAuthComplete = useCallback(async (): Promise<boolean> => {
    try {
      const row = await ToolsetsApi.findMyToolsetByInstanceId(instanceId);
      return Boolean(row?.isAuthenticated);
    } catch {
      return false;
    }
  }, [instanceId]);

  const onOAuthVerified = useCallback(() => {
    setIsAuthenticated(true);
    if (embedded) {
      drawerBodyRefresh.requestRefresh();
    }
    // Close immediately: parent `onClose` already refreshes the list. Do not wait on
    // `onSuccess` — a slow or stuck `refreshToolsets` would leave this dialog open forever.
    startTransition(() => {
      onClose();
    });
    void Promise.resolve(onSuccess()).catch(() => {
      /* extra refresh failed; list was already updated from onClose where applicable */
    });
  }, [drawerBodyRefresh, embedded, onClose, onSuccess]);

  const onOAuthIncomplete = useCallback(() => {
    setError(t('agentBuilder.oauthSignInIncomplete'));
  }, [t]);

  const { authenticating, authenticatingRef, beginOAuth, cancelForUserDismissal, stopOAuthUi } =
    useToolsetOauthPopupFlow({
      t,
      verifyAuthenticated: verifyOAuthComplete,
      onVerified: onOAuthVerified,
      onNotify,
      onIncomplete: onOAuthIncomplete,
      onOAuthPopupError: (msg) => setError(msg),
    });

  useEffect(() => {
    if (!embedded) return;
    return () => {
      stopOAuthUi();
    };
  }, [embedded, stopOAuthUi]);

  const dismissLocked = saving || deleting;

  const requestDismiss = useCallback(() => {
    if (dismissLocked) return;
    if (authenticatingRef.current) {
      cancelForUserDismissal();
    }
    onClose();
  }, [authenticatingRef, cancelForUserDismissal, dismissLocked, onClose]);

  const handleSaveCredentials = async () => {
    setSaveAttempted(true);
    setError(null);
    if (!validateForm()) {
      return;
    }
    try {
      setSaving(true);
      setError(null);
      const safeAuthPayload = Object.fromEntries(
        Object.entries(formData).filter(([k]) => !isOrgOAuthAppCredentialFieldName(k))
      );
      if (isAuthenticated) {
        await ToolsetsApi.updateMyToolsetCredentials(instanceId, safeAuthPayload);
      } else {
        await ToolsetsApi.authenticateMyToolsetInstance(instanceId, safeAuthPayload);
      }
      setIsAuthenticated(true);
      onNotify?.(t('agentBuilder.toolsetAuthUpdated'));
      onSuccess();
      onClose();
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
        const result = await ToolsetsApi.getInstanceOAuthAuthorizationUrl(
          instanceId,
          typeof window !== 'undefined' ? window.location.origin : undefined
        );
        if (!result.success || !result.authorizationUrl) {
          throw new Error(t('agentBuilder.oauthUrlFailed'));
        }
        return {
          authorizationUrl: result.authorizationUrl,
          windowName: 'oauth_user_toolset',
        };
      },
      {
        onTimeout: () => setError(t('agentBuilder.authTimeout')),
        onOpenError: (e) => setError(apiErrorDetail(e)),
      }
    );
  };

  const handleRemoveConfirmed = async () => {
    setRemoveConfirmOpen(false);
    try {
      setDeleting(true);
      setError(null);
      if (isOAuthType(authType)) {
        await ToolsetsApi.reauthenticateMyToolsetInstance(instanceId);
      } else {
        await ToolsetsApi.removeMyToolsetCredentials(instanceId);
      }
      setIsAuthenticated(false);
      onNotify?.(t('agentBuilder.toolsetAuthUpdated'));
      onSuccess();
      onClose();
    } catch (e) {
      setError(apiErrorDetail(e));
    } finally {
      setDeleting(false);
    }
  };

  const busy = saving || authenticating || deleting;
  const oauthDisconnectFlow = isOAuthType(authType);

  const showFooterPrimaryCluster =
    !schemaLoading &&
    (isOAuthType(authType) || (isCredentialAuthType(authType) && userCredentialFields.length > 0));

  const handleMainOpenChange = (open: boolean) => {
    if (!open && !dismissLocked) requestDismiss();
  };

  const mainBody = (
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
          <Box style={{ minWidth: 0, flex: 1 }}>
            {embedded ? (
              <>
                <Text as="div" size="4" weight="bold" style={{ color: 'var(--slate-12)', display: 'block' }}>
                  {displayName}
                </Text>
                {subtitle ? (
                  <Text as="div" size="2" color="gray" mt="1" style={{ lineHeight: 1.5 }}>
                    {subtitle}
                  </Text>
                ) : null}
              </>
            ) : (
              <>
                <Dialog.Title style={{ marginBottom: 4 }}>{t('agentBuilder.userToolsetConfigureTitle')}</Dialog.Title>
                <Text size="3" weight="bold" style={{ color: 'var(--slate-12)', display: 'block' }}>
                  {displayName}
                </Text>
                {subtitle ? (
                  <Text size="2" style={{ color: 'var(--slate-11)', display: 'block', marginTop: 2 }}>
                    {subtitle}
                  </Text>
                ) : null}
              </>
            )}
            <Flex gap="2" wrap="wrap" mt="2">
              {toolset.category ? (
                <Badge size="1" color="gray" variant={embedded ? 'soft' : undefined}>
                  {toolset.category}
                </Badge>
              ) : null}
              <Badge size="1" color="gray" variant={embedded ? 'soft' : undefined}>
                {formatAuthTypeName(authType)}
              </Badge>
            </Flex>
          </Box>
        </Flex>
        {!embedded ? (
          <IconButton variant="ghost" color="gray" onClick={() => requestDismiss()} disabled={dismissLocked} aria-label={t('common.close')}>
            <MaterialIcon name="close" size={20} />
          </IconButton>
        ) : null}
      </Flex>

      {embedded ? (
        <Text as="div" size="2" mb="3" color="gray" style={{ lineHeight: 1.55 }}>
          {t('agentBuilder.userToolsetDesc')}
        </Text>
      ) : (
        <Dialog.Description size="2" mb="3" style={{ color: 'var(--slate-11)' }}>
          {t('agentBuilder.userToolsetDesc')}
        </Dialog.Description>
      )}

            {schemaLoading ? (
              <Flex align="center" gap="3" py="4" justify="center">
                <Spinner size="2" />
                <Text size="2" color="gray">
                  {t('agentBuilder.loadingSchema')}
                </Text>
              </Flex>
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
                  <Callout.Icon>
                    <MaterialIcon name={isAuthenticated ? 'verified_user' : 'link'} size={18} />
                  </Callout.Icon>
                  <Callout.Text size="1" style={{ color: 'var(--slate-11)' }}>
                    {isAuthenticated ? t('agentBuilder.userToolsetOAuthConnected') : t('agentBuilder.userToolsetOAuthPending')}
                  </Callout.Text>
                </Callout.Root>
              </Flex>
            ) : null}

            {!schemaLoading && isCredentialAuthType(authType) && userCredentialFields.length > 0 ? (
              <Flex direction="column" gap="4" width="100%" mt="2">
                <Separator size="4" />
                <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                  {t('agentBuilder.userCredentialsFieldsHeading')}
                </Text>
                {userCredentialFields.map((field) => (
                  <SchemaFormField
                    key={field.name}
                    field={field}
                    value={formData[field.name]}
                    onChange={setField}
                    error={saveAttempted ? formErrors[field.name] : undefined}
                    disabled={busy}
                    selectPortalZIndex={embedded ? WORKSPACE_DRAWER_POPPER_Z_INDEX : undefined}
                  />
                ))}
                {isAuthenticated ? (
                  <Callout.Root color="green" variant="surface" size="1">
                    <Callout.Text size="1">{t('agentBuilder.userCredentialUpdateHint')}</Callout.Text>
                  </Callout.Root>
                ) : null}
              </Flex>
            ) : null}

            {!schemaLoading && isCredentialAuthType(authType) && userCredentialFields.length === 0 ? (
              <Callout.Root color="amber" variant="surface" size="1" mt="2">
                <Callout.Text size="1">{t('agentBuilder.noCredentialFields')}</Callout.Text>
              </Callout.Root>
            ) : null}

            <Separator size="4" my="4" />

      {showFooterPrimaryCluster ? (
        <Box style={toolsetDialogFooterToolbarStyle}>
          {isOAuthType(authType) ? (
            <Flex
              wrap="wrap"
              gap="2"
              style={{
                ...toolsetDialogPrimaryActionsStyle,
                ...toolsetDialogFooterPrimaryClusterStyle,
              }}
            >
              <Button
                size="2"
                variant="soft"
                color="green"
                onClick={() => void handleOAuthAuthenticate()}
                disabled={busy}
              >
                {authenticating
                  ? t('agentBuilder.waitingOAuth')
                  : isAuthenticated
                    ? t('agentBuilder.reconnectOAuth')
                    : t('agentBuilder.authenticateOAuth')}
              </Button>
              {isAuthenticated ? (
                <Button
                  size="2"
                  variant="soft"
                  color="red"
                  onClick={() => setRemoveConfirmOpen(true)}
                  disabled={busy}
                >
                  {t('agentBuilder.disconnectOAuth')}
                </Button>
              ) : null}
            </Flex>
          ) : null}
          {isCredentialAuthType(authType) && userCredentialFields.length > 0 ? (
            <Flex
              wrap="wrap"
              gap="2"
              style={{
                ...toolsetDialogPrimaryActionsStyle,
                ...toolsetDialogFooterPrimaryClusterStyle,
              }}
            >
              <LoadingButton
                size="2"
                variant="soft"
                color="green"
                onClick={() => void handleSaveCredentials()}
                disabled={busy && !saving}
                loading={saving}
                loadingLabel={t('agentBuilder.savingCredentials')}
              >
                {isAuthenticated
                  ? t('agentBuilder.updateCredentials')
                  : t('agentBuilder.saveCredentials')}
              </LoadingButton>
              {isAuthenticated ? (
                <Button
                  size="2"
                  variant="soft"
                  color="red"
                  onClick={() => setRemoveConfirmOpen(true)}
                  disabled={busy}
                >
                  {t('agentBuilder.removeCredentials')}
                </Button>
              ) : null}
            </Flex>
          ) : null}
          <Box style={{ flexShrink: 0, marginInlineStart: 'auto' }}>
            <Button size="2" variant="soft" color="gray" onClick={() => requestDismiss()} disabled={dismissLocked}>
              {isAuthenticated ? t('common.close') : t('action.cancel')}
            </Button>
          </Box>
        </Box>
      ) : (
        <Flex justify="end" width="100%">
          <Button size="2" variant="soft" color="gray" onClick={() => requestDismiss()} disabled={dismissLocked}>
            {isAuthenticated ? t('common.close') : t('action.cancel')}
          </Button>
        </Flex>
      )}
    </Box>
  );

  return (
    <>
      {embedded ? (
        mainBody
      ) : (
        <Dialog.Root open onOpenChange={handleMainOpenChange}>
          <Dialog.Content style={{ ...toolsetDialogPanelStyle, maxHeight: 'min(90vh, 44rem)', overflow: 'auto' }}>
            {mainBody}
          </Dialog.Content>
        </Dialog.Root>
      )}

      {removeConfirmOpen && (!embedded || nestedModalHost) ? (
        <Dialog.Root open onOpenChange={(o) => !o && !deleting && setRemoveConfirmOpen(false)}>
          <Dialog.Content
            container={embedded ? nestedModalHost ?? undefined : undefined}
            style={{
              ...toolsetDialogPanelStyle,
              maxWidth: 'min(28rem, calc(100vw - 2rem))',
            }}
          >
            <Dialog.Title>
              {oauthDisconnectFlow
                ? t('agentBuilder.disconnectOAuthTitle', { name: displayName })
                : t('agentBuilder.removeCredentialsTitle')}
            </Dialog.Title>
            <Text size="2" mb="3" style={{ color: 'var(--slate-11)' }}>
              {oauthDisconnectFlow
                ? t('agentBuilder.userToolsetDisconnectOAuthDesc', { name: displayName })
                : t('agentBuilder.userToolsetRemoveDesc', { name: displayName })}
            </Text>
            <Flex gap="2" justify="end" wrap="wrap">
              <Dialog.Close>
                <Button variant="soft" color="gray" disabled={deleting}>
                  {t('action.cancel')}
                </Button>
              </Dialog.Close>
              <Button color="red" onClick={() => void handleRemoveConfirmed()} disabled={deleting}>
                {deleting
                  ? oauthDisconnectFlow
                    ? t('agentBuilder.disconnectOAuthProgress')
                    : t('agentBuilder.removing')
                  : oauthDisconnectFlow
                    ? t('agentBuilder.disconnectOAuth')
                    : t('agentBuilder.remove')}
              </Button>
            </Flex>
          </Dialog.Content>
        </Dialog.Root>
      ) : null}
    </>
  );
}
