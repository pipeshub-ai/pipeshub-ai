'use client';

import React, { useCallback, useContext, useEffect, useMemo } from 'react';
import { Callout, Flex, Text, Select, Spinner, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useTranslation } from 'react-i18next';
import { WorkspaceRightPanelBodyPortalContext } from '@/app/(main)/workspace/components/workspace-right-panel';
import { FormField } from '@/app/(main)/workspace/components/form-field';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { ConnectorsApi } from '../../api';
import { useConnectorsStore, type ConnectorOAuthAppListRow } from '../../store';
import { OAUTH_FORM_WANTS_NEW_REGISTRATION, oauthCredentialFormDiffersFromBaseline, resolveLinkedOAuthAppId } from '../../utils/auth-helpers';
import { resolveAuthFields } from './helpers';

const MANUAL_VALUE = '__manual__';

/** List rows, form `auth`, and GET `/config` `auth` use camelCase or snake_case per API contract. */
type OAuthInstanceNameSource = Pick<
  ConnectorOAuthAppListRow,
  'oauthInstanceName' | 'oauth_instance_name'
>;

function resolveOAuthInstanceName(source: OAuthInstanceNameSource | undefined): string {
  return (source?.oauthInstanceName || source?.oauth_instance_name || '').trim();
}

function rowLabel(row: ConnectorOAuthAppListRow): string {
  return resolveOAuthInstanceName(row) || 'Unnamed OAuth app';
}

function oauthConfigPayload(full: Record<string, unknown>): Record<string, unknown> {
  const nested = full.config;
  if (nested && typeof nested === 'object') return nested as Record<string, unknown>;
  return full;
}

// ========================================
// OAuthAppSelector (legacy auth-section parity)
// ========================================

export function OAuthAppSelector() {
  const { t } = useTranslation();
  const panelBodyPortal = useContext(WorkspaceRightPanelBodyPortalContext);
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  const connectorSchema = useConnectorsStore((s) => s.connectorSchema);
  const panelConnector = useConnectorsStore((s) => s.panelConnector);
  const panelConnectorId = useConnectorsStore((s) => s.panelConnectorId);
  const connectorConfig = useConnectorsStore((s) => s.connectorConfig);
  const selectedAuthType = useConnectorsStore((s) => s.selectedAuthType);
  const selectedId = useConnectorsStore(
    (s) => s.formData.auth.oauthConfigId as string | undefined
  );
  const oauthInstanceName = useConnectorsStore(
    (s) => s.formData.auth.oauthInstanceName as string | undefined
  );
  const oauthInstanceNameError = useConnectorsStore((s) => s.formErrors.oauthInstanceName);
  const setAuthFormValue = useConnectorsStore((s) => s.setAuthFormValue);
  const oauthConfigError = useConnectorsStore((s) => s.formErrors.oauthConfigId);
  const oauthApps = useConnectorsStore((s) => s.oauthAppsList);
  const oauthAppsListPhase = useConnectorsStore((s) => s.oauthAppsListPhase);
  const fetchError = useConnectorsStore((s) => s.oauthAppsListFetchError);
  const clearOAuthAppsListState = useConnectorsStore((s) => s.clearOAuthAppsListState);
  const beginOAuthAppsListFetch = useConnectorsStore((s) => s.beginOAuthAppsListFetch);
  const finishOAuthAppsListFetch = useConnectorsStore((s) => s.finishOAuthAppsListFetch);
  const cancelOAuthAppsListFetchIfPending = useConnectorsStore(
    (s) => s.cancelOAuthAppsListFetchIfPending
  );
  const formAuth = useConnectorsStore((s) => s.formData.auth);
  const oauthCredentialBaseline = useConnectorsStore((s) => s.oauthCredentialBaseline);

  const loading = oauthAppsListPhase === 'loading';

  const connectorType = panelConnector?.type ?? '';
  const isExistingConnector = Boolean(panelConnectorId);

  const oauthFieldNames = useMemo(() => {
    if (!connectorSchema?.auth) return [];
    const fields = resolveAuthFields(connectorSchema.auth, 'OAUTH');
    return fields.map((f) => f.name).filter((n) => n !== 'oauthConfigId');
  }, [connectorSchema]);

  const populateFromConfig = useCallback(
    (cfg: Record<string, unknown>) => {
      if (isAdmin !== true) return;
      for (const name of oauthFieldNames) {
        const v = cfg[name];
        if (v !== undefined && v !== null) {
          setAuthFormValue(name, v as string | number | boolean);
        }
      }
    },
    [isAdmin, oauthFieldNames, setAuthFormValue]
  );

  const clearOAuthCredentialFields = useCallback(() => {
    for (const name of oauthFieldNames) {
      setAuthFormValue(name, '');
    }
  }, [oauthFieldNames, setAuthFormValue]);

  useEffect(() => {
    if (selectedAuthType !== 'OAUTH' || !connectorType) {
      clearOAuthAppsListState();
      return;
    }

    let cancelled = false;
    beginOAuthAppsListFetch(connectorType);

    ConnectorsApi.listOAuthConfigs(connectorType, 1, 100)
      .then((res) => {
        if (cancelled) return;
        const apps = (res.oauthConfigs ?? []) as ConnectorOAuthAppListRow[];
        finishOAuthAppsListFetch(connectorType, { ok: true, apps });
      })
      .catch(() => {
        if (!cancelled) {
          finishOAuthAppsListFetch(connectorType, {
            ok: false,
            error: 'Could not load OAuth apps for this connector.',
          });
        }
      });

    return () => {
      cancelled = true;
      cancelOAuthAppsListFetchIfPending(connectorType);
    };
  }, [
    selectedAuthType,
    connectorType,
    clearOAuthAppsListState,
    beginOAuthAppsListFetch,
    finishOAuthAppsListFetch,
    cancelOAuthAppsListFetchIfPending,
  ]);

  // After schema + list are ready, hydrate credential fields for admins (list API may include full config).
  useEffect(() => {
    if (selectedAuthType !== 'OAUTH' || isAdmin !== true) return;
    const id = useConnectorsStore.getState().formData.auth.oauthConfigId as string | undefined;
    if (!id || !oauthFieldNames.length || oauthApps.length === 0) return;
    const app = oauthApps.find((a) => a._id === id);
    if (app?.config && typeof app.config === 'object') {
      populateFromConfig(app.config);
      return;
    }
    if (app) {
      let cancelled = false;
      ConnectorsApi.getOAuthConfig(connectorType, id)
        .then((full) => {
          if (cancelled) return;
          const currentId =
            useConnectorsStore.getState().formData.auth.oauthConfigId as string | undefined;
          if ((currentId ?? '').trim() !== id.trim()) return;
          populateFromConfig(oauthConfigPayload(full));
        })
        .catch(() => {});
      return () => {
        cancelled = true;
      };
    }
    return undefined;
  }, [
    selectedAuthType,
    isAdmin,
    oauthApps,
    oauthFieldNames,
    connectorType,
    populateFromConfig,
  ]);

  /**
   * Clear stale oauthConfigId only when creating a new instance and the server reports zero
   * registrations. In edit mode, keep the saved id even if the list is empty (deleted app,
   * transient API, or permissions).
   */
  useEffect(() => {
    if (panelConnectorId) return;
    if (selectedAuthType !== 'OAUTH' || loading || oauthApps.length > 0) return;
    if (fetchError) return;
    const id = useConnectorsStore.getState().formData.auth.oauthConfigId as string | undefined;
    if (id?.trim()) {
      setAuthFormValue('oauthConfigId', undefined);
    }
  }, [
    panelConnectorId,
    selectedAuthType,
    loading,
    oauthApps.length,
    fetchError,
    setAuthFormValue,
  ]);

  const selectedIdTrimmed = (selectedId ?? '').trim();

  const savedOAuthAppId = useMemo(() => {
    const authCfg = connectorConfig?.config?.auth as { oauthConfigId?: string } | undefined;
    const id = authCfg?.oauthConfigId;
    return typeof id === 'string' ? id.trim() : '';
  }, [connectorConfig?.config?.auth]);

  const currentLinkedOAuthAppId = useMemo(
    () => resolveLinkedOAuthAppId(formAuth, connectorConfig),
    [formAuth, connectorConfig]
  );

  const oauthCredentialBaselineKey =
    panelConnectorId && currentLinkedOAuthAppId
      ? `${panelConnectorId}:${currentLinkedOAuthAppId}`
      : '';

  const oauthCredentialsDirty = useMemo(
    () =>
      oauthCredentialFormDiffersFromBaseline(
        formAuth as Record<string, unknown>,
        oauthFieldNames,
        oauthCredentialBaseline,
        oauthCredentialBaselineKey
      ),
    [formAuth, oauthFieldNames, oauthCredentialBaseline, oauthCredentialBaselineKey]
  );

  const showOAuthReauthWarning =
    isExistingConnector &&
    selectedAuthType === 'OAUTH' &&
    !loading &&
    (savedOAuthAppId !== currentLinkedOAuthAppId || oauthCredentialsDirty);

  /** New OAuth registration: no `oauthConfigId` (Create new, or empty list with implicit new app). */
  const showNewOAuthAppName = isAdmin === true && !selectedIdTrimmed;

  /**
   * Default OAuth registration name from the connector product type (e.g. "Jira", "Slack") so the
   * pre-fill is always the service name regardless of what the instance was named by the user.
   * Covers both "no saved apps yet" and "Create new" chosen from the dropdown while other apps exist.
   */
  useEffect(() => {
    if (selectedAuthType !== 'OAUTH' || isAdmin !== true) return;
    if (selectedIdTrimmed) return;
    if (loading || fetchError) return;
    const inst = panelConnector?.type?.trim() || '';
    if (!inst) return;
    const cur = String(useConnectorsStore.getState().formData.auth.oauthInstanceName ?? '').trim();
    if (!cur) {
      setAuthFormValue('oauthInstanceName', inst);
    }
  }, [
    selectedAuthType,
    isAdmin,
    selectedIdTrimmed,
    loading,
    fetchError,
    panelConnector?.type,
    setAuthFormValue,
  ]);

  /** Name comes from persisted GET /config auth only — form `auth` has oauthConfigId + credentials, not instance name. */
  const unlistedRegistrationLabel = useMemo(() => {
    const name = resolveOAuthInstanceName(
      connectorConfig?.config?.auth as OAuthInstanceNameSource | undefined
    );
    return name ? `${name} (linked)` : 'Linked OAuth registration';
  }, [connectorConfig?.config?.auth]);

  const showUnlistedRegistrationItem = useMemo(() => {
    if (!selectedIdTrimmed || loading) return false;
    return !oauthApps.some((a) => a._id === selectedIdTrimmed);
  }, [selectedIdTrimmed, loading, oauthApps]);

  const showOAuthSelect =
    loading ||
    oauthApps.length > 0 ||
    showUnlistedRegistrationItem ||
    (isAdmin === true && isExistingConnector && !fetchError);

  const radixValue = useMemo(() => {
    if (selectedAuthType !== 'OAUTH') return undefined;
    if (selectedIdTrimmed) return selectedIdTrimmed;
    if (isAdmin === true) return MANUAL_VALUE;
    return undefined;
  }, [selectedAuthType, selectedIdTrimmed, isAdmin]);

  const handleValueChange = (value: string) => {
    setAuthFormValue('oauthInstanceName', '');
    if (value === MANUAL_VALUE) {
      setAuthFormValue('oauthConfigId', undefined);
      setAuthFormValue(OAUTH_FORM_WANTS_NEW_REGISTRATION, true);
      clearOAuthCredentialFields();
      return;
    }
    setAuthFormValue(OAUTH_FORM_WANTS_NEW_REGISTRATION, undefined);
    setAuthFormValue('oauthConfigId', value);
    const app = oauthApps.find((a) => a._id === value);
    if (app?.config && typeof app.config === 'object') {
      populateFromConfig(app.config);
      return;
    }
    if (isAdmin === true && app && connectorType) {
      void ConnectorsApi.getOAuthConfig(connectorType, value)
        .then((full) => {
          const currentId =
            useConnectorsStore.getState().formData.auth.oauthConfigId as string | undefined;
          if ((currentId ?? '').trim() !== value.trim()) return;
          populateFromConfig(oauthConfigPayload(full));
        })
        .catch(() => {
          /* non-fatal */
        });
    }
  };

  if (selectedAuthType !== 'OAUTH' || !panelConnector) return null;

  /** Only when the list is empty: avoids duplicating the main helper under this section (index). */
  const oauthAppAuxiliaryDescription =
    !loading && !fetchError && oauthApps.length === 0
      ? (() => {
          if (showUnlistedRegistrationItem) {
            if (isAdmin === true) {
              return isExistingConnector
                ? 'This instance is linked to an OAuth registration that does not appear in the list. Choose another saved app below or keep the current link.'
                : 'This instance is linked to an OAuth registration that does not appear in the list. Choose another app below, use Create new, or keep the current link.';
            }
            return 'This instance is linked to an OAuth registration that does not appear in the list. You can switch once your administrator adds more saved apps.';
          }
          if (isProfileInitialized && isAdmin === false) {
            return 'No OAuth apps are registered yet. Ask an administrator to add one in workspace connector settings.';
          }
          if (isAdmin === true) {
            return null;
          }
          return 'Enter OAuth client credentials below, or pick a saved app once one is available.';
        })()
      : null;

  return (
    <Flex direction="column" gap="3" style={{ width: '100%' }}>
      <Flex direction="column" gap="1">
        <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          OAuth app
        </Text>
        {oauthAppAuxiliaryDescription ? (
          <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55, maxWidth: '100%' }}>
            {oauthAppAuxiliaryDescription}
          </Text>
        ) : null}
      </Flex>

      {fetchError && (
        <Text size="1" color="red">
          {fetchError}
        </Text>
      )}

      {loading ? (
        <Flex align="center" gap="2" py="2">
          <Spinner />
          <Text size="2" color="gray">
            Loading OAuth configurations…
          </Text>
        </Flex>
      ) : showOAuthSelect ? (
        <Flex direction="column" gap="1" style={{ width: '100%' }}>
          <Select.Root value={radixValue} onValueChange={handleValueChange}>
            <Select.Trigger
              data-ph-oauth-app-select
              color={oauthConfigError ? 'red' : undefined}
              data-invalid={oauthConfigError ? true : undefined}
              style={{
                width: '100%',
                minHeight: 40,
                height: 'auto',
                minWidth: 0,
                alignItems: 'center',
              }}
              placeholder={
                isAdmin === true
                  ? 'Select OAuth app or create new…'
                  : 'Select an OAuth app (required)…'
              }
            />
            <Select.Content
              position="popper"
              style={{ zIndex: 10000 }}
              container={panelBodyPortal ?? undefined}
            >
              {isAdmin === true ? (
                <Select.Item value={MANUAL_VALUE}>Create new OAuth app</Select.Item>
              ) : null}
              {oauthApps.map((app) => (
                <Select.Item key={app._id} value={app._id}>
                  {rowLabel(app)}
                </Select.Item>
              ))}
              {showUnlistedRegistrationItem ? (
                <Select.Item value={selectedIdTrimmed}>{unlistedRegistrationLabel}</Select.Item>
              ) : null}
            </Select.Content>
          </Select.Root>
          {oauthConfigError ? (
            <Text size="1" color="red">
              {oauthConfigError}
            </Text>
          ) : null}
        </Flex>
      ) : null}

      {showOAuthReauthWarning ? (
        <Callout.Root color="amber" size="1" variant="surface">
          <Callout.Icon>
            <MaterialIcon name="warning" size={14} color="var(--amber-11)" />
          </Callout.Icon>
          <Callout.Text>
            {t('workspace.connectors.authTab.oauthAppChangeReauthWarning')}
          </Callout.Text>
        </Callout.Root>
      ) : null}

      {showNewOAuthAppName ? (
        <Flex direction="column" gap="1" style={{ width: '100%' }} data-ph-oauth-app-name>
          <FormField
            label={t('workspace.connectors.authTab.oauthAppNameLabel')}
            required
            error={oauthInstanceNameError ?? undefined}
          >
            <TextField.Root
              size="2"
              type="text"
              value={oauthInstanceName ?? ''}
              onChange={(e) => setAuthFormValue('oauthInstanceName', e.target.value)}
              placeholder={t('workspace.connectors.authTab.oauthAppNamePlaceholder', {
                name: panelConnector.name,
              })}
              aria-invalid={oauthInstanceNameError ? true : undefined}
              color={oauthInstanceNameError ? 'red' : undefined}
              style={{ width: '100%' }}
            />
          </FormField>
          <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
            {t('workspace.connectors.authTab.oauthAppNameHelper')}
          </Text>
        </Flex>
      ) : null}
    </Flex>
  );
}
