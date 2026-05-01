'use client';

import React, { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Flex, Text, Select, Spinner } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { WorkspaceRightPanelBodyPortalContext } from '@/app/(main)/workspace/components/workspace-right-panel';
import { FormField } from '@/app/(main)/workspace/components/form-field';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { ConnectorsApi } from '../../api';
import { useConnectorsStore } from '../../store';
import { resolveAuthFields } from './helpers';

const MANUAL_VALUE = '__manual__';

type OAuthListRow = {
  _id: string;
  oauthInstanceName?: string;
  oauth_instance_name?: string;
  config?: Record<string, unknown>;
  appGroup?: string;
};

/** List rows, form `auth`, and GET `/config` `auth` use camelCase or snake_case per API contract. */
type OAuthInstanceNameSource = Pick<
  OAuthListRow,
  'oauthInstanceName' | 'oauth_instance_name'
>;

function resolveOAuthInstanceName(source: OAuthInstanceNameSource | undefined): string {
  return (source?.oauthInstanceName || source?.oauth_instance_name || '').trim();
}

function rowLabel(row: OAuthListRow): string {
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
  const instanceName = useConnectorsStore((s) => s.instanceName);
  const selectedId = useConnectorsStore(
    (s) => s.formData.auth.oauthConfigId as string | undefined
  );
  const oauthInstanceName = useConnectorsStore(
    (s) => s.formData.auth.oauthInstanceName as string | undefined
  );
  const oauthInstanceNameError = useConnectorsStore((s) => s.formErrors.oauthInstanceName);
  const setAuthFormValue = useConnectorsStore((s) => s.setAuthFormValue);
  const oauthConfigError = useConnectorsStore((s) => s.formErrors.oauthConfigId);

  const [oauthApps, setOauthApps] = useState<OAuthListRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

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
      setOauthApps([]);
      setFetchError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setFetchError(null);

    ConnectorsApi.listOAuthConfigs(connectorType, 1, 100)
      .then((res) => {
        if (cancelled) return;
        const apps = (res.oauthConfigs ?? []) as OAuthListRow[];
        setOauthApps(apps);
      })
      .catch(() => {
        if (!cancelled) {
          setFetchError('Could not load OAuth apps for this connector.');
          setOauthApps([]);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedAuthType, connectorType]);

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

  /** Create new OAuth registration: no linked `oauthConfigId` (explicit "Create new" or no saved apps yet). */
  const showNewOAuthAppName =
    isAdmin === true && !isExistingConnector && !selectedIdTrimmed;

  /**
   * When there are no saved apps yet, mirror legacy behavior: default OAuth registration name
   * from the connector instance name until the user edits the field.
   */
  useEffect(() => {
    if (selectedAuthType !== 'OAUTH' || isExistingConnector || isAdmin !== true) return;
    if (selectedIdTrimmed) return;
    if (loading || fetchError) return;
    if (oauthApps.length !== 0) return;
    const inst = instanceName.trim();
    if (!inst) return;
    const cur = String(useConnectorsStore.getState().formData.auth.oauthInstanceName ?? '').trim();
    if (!cur) {
      setAuthFormValue('oauthInstanceName', inst);
    }
  }, [
    selectedAuthType,
    isExistingConnector,
    isAdmin,
    selectedIdTrimmed,
    loading,
    fetchError,
    oauthApps.length,
    instanceName,
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
    loading || oauthApps.length > 0 || showUnlistedRegistrationItem;

  const radixValue = useMemo(() => {
    if (selectedAuthType !== 'OAUTH') return undefined;
    if (selectedIdTrimmed) return selectedIdTrimmed;
    if (isAdmin === true && !isExistingConnector) return MANUAL_VALUE;
    return undefined;
  }, [selectedAuthType, selectedIdTrimmed, isAdmin, isExistingConnector]);

  const handleValueChange = (value: string) => {
    if (value === MANUAL_VALUE) {
      setAuthFormValue('oauthConfigId', undefined);
      setAuthFormValue('oauthInstanceName', '');
      clearOAuthCredentialFields();
      return;
    }
    setAuthFormValue('oauthInstanceName', '');
    setAuthFormValue('oauthConfigId', value);
    const app = oauthApps.find((a) => a._id === value);
    if (app?.config && typeof app.config === 'object') {
      populateFromConfig(app.config);
      return;
    }
    if (isAdmin === true && app && connectorType) {
      void ConnectorsApi.getOAuthConfig(connectorType, value)
        .then((full) => {
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
            if (isExistingConnector) {
              return 'No other saved OAuth apps are listed for this connector. You can keep using the linked registration if shown above, or ask an administrator to add more.';
            }
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
                  ? isExistingConnector
                    ? 'Select OAuth app…'
                    : 'Select OAuth app or create new…'
                  : 'Select an OAuth app (required)…'
              }
            />
            <Select.Content
              position="popper"
              style={{ zIndex: 10000 }}
              container={panelBodyPortal ?? undefined}
            >
              {isAdmin === true && !isExistingConnector ? (
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

      {showNewOAuthAppName ? (
        <Flex direction="column" gap="1" style={{ width: '100%' }} data-ph-oauth-app-name>
          <FormField
            label={t('workspace.connectors.authTab.oauthAppNameLabel')}
            required
            error={oauthInstanceNameError ?? undefined}
          >
            <input
              type="text"
              value={typeof oauthInstanceName === 'string' ? oauthInstanceName : ''}
              onChange={(e) => setAuthFormValue('oauthInstanceName', e.target.value)}
              placeholder={t('workspace.connectors.authTab.oauthAppNamePlaceholder', {
                name: panelConnector.name,
              })}
              aria-invalid={oauthInstanceNameError ? true : undefined}
              style={{
                height: 32,
                width: '100%',
                padding: '6px 8px',
                backgroundColor: oauthInstanceNameError ? 'var(--red-a2)' : 'var(--color-surface)',
                border: oauthInstanceNameError
                  ? '1px solid var(--red-9)'
                  : '1px solid var(--gray-a5)',
                borderRadius: 'var(--radius-2)',
                fontSize: 14,
                fontFamily: 'var(--default-font-family)',
                color: 'var(--gray-12)',
                boxSizing: 'border-box',
                outline: 'none',
              }}
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
