'use client';

import React, { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Flex, Text, Select, Spinner, Box } from '@radix-ui/themes';
import { WorkspaceRightPanelBodyPortalContext } from '@/app/(main)/workspace/components/workspace-right-panel';
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

function rowLabel(row: OAuthListRow): string {
  return row.oauthInstanceName || row.oauth_instance_name || 'Unnamed OAuth app';
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
  const panelBodyPortal = useContext(WorkspaceRightPanelBodyPortalContext);
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  const connectorSchema = useConnectorsStore((s) => s.connectorSchema);
  const panelConnector = useConnectorsStore((s) => s.panelConnector);
  const selectedAuthType = useConnectorsStore((s) => s.selectedAuthType);
  const selectedId = useConnectorsStore((s) => s.formData.auth.oauthConfigId as string | undefined);
  const setAuthFormValue = useConnectorsStore((s) => s.setAuthFormValue);

  const [oauthApps, setOauthApps] = useState<OAuthListRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const connectorType = panelConnector?.type ?? '';

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

  /** Saved app id is invalid when the server returns no registrations for this type. */
  useEffect(() => {
    if (selectedAuthType !== 'OAUTH' || loading || oauthApps.length > 0) return;
    const id = useConnectorsStore.getState().formData.auth.oauthConfigId as string | undefined;
    if (id?.trim()) {
      setAuthFormValue('oauthConfigId', undefined);
    }
  }, [selectedAuthType, loading, oauthApps.length, setAuthFormValue]);

  const radixValue = useMemo(() => {
    if (selectedAuthType !== 'OAUTH') return undefined;
    if (selectedId) return selectedId;
    if (isAdmin === true) return MANUAL_VALUE;
    return undefined;
  }, [selectedAuthType, selectedId, isAdmin]);

  const handleValueChange = (value: string) => {
    if (value === MANUAL_VALUE) {
      setAuthFormValue('oauthConfigId', undefined);
      clearOAuthCredentialFields();
      return;
    }
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

  const oauthAppDescription = (() => {
    if (loading) {
      return 'Loading saved OAuth apps for this connector…';
    }
    if (oauthApps.length === 0) {
      if (isProfileInitialized && isAdmin === false) {
        return 'No OAuth apps are registered yet. Ask an administrator to add one in workspace connector settings.';
      }
      if (isAdmin === true) {
        return 'No saved OAuth apps for this connector yet. Enter client credentials in the fields below to use a new OAuth app.';
      }
      return 'Enter OAuth client credentials below, or pick a saved app once one is available.';
    }
    if (isAdmin === true) {
      return 'Pick an existing OAuth app to reuse its credentials, or choose Create new and enter credentials below.';
    }
    return 'Select which OAuth app this connector instance should use.';
  })();

  return (
    <Flex direction="column" gap="3" style={{ width: '100%' }}>
      <Flex direction="column" gap="1">
        <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          OAuth app
        </Text>
        <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55, maxWidth: '100%' }}>
          {oauthAppDescription}
        </Text>
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
      ) : oauthApps.length > 0 ? (
        <Select.Root value={radixValue} onValueChange={handleValueChange}>
          <Select.Trigger
            style={{
              width: '100%',
              minHeight: 40,
              height: 'auto',
              minWidth: 0,
              alignItems: 'center',
            }}
            placeholder={isAdmin === true ? 'Select OAuth app or create new…' : 'Select an OAuth app (required)…'}
          />
          <Select.Content
            position="popper"
            style={{ zIndex: 10000 }}
            container={panelBodyPortal ?? undefined}
          >
            {isAdmin === true && (
              <Select.Item value={MANUAL_VALUE}>Create new OAuth app</Select.Item>
            )}
            {oauthApps.map((app) => (
              <Select.Item key={app._id} value={app._id}>
                {rowLabel(app)}
              </Select.Item>
            ))}
          </Select.Content>
        </Select.Root>
      ) : null}
    </Flex>
  );
}

// ========================================
// OAuth app in use (edit mode — no changing registration)
// ========================================

type LinkedOAuthDetails = {
  appTitle: string;
  manual?: boolean;
};

/**
 * After an instance exists, the OAuth app binding is fixed. Resolve registration id from saved
 * config (including flat `config.auth` from API), fetch app metadata + client credentials when exposed.
 */
export function OAuthAppInUseReadonly() {
  const panelConnector = useConnectorsStore((s) => s.panelConnector);
  const panelConnectorId = useConnectorsStore((s) => s.panelConnectorId);
  const selectedAuthType = useConnectorsStore((s) => s.selectedAuthType);
  const formOAuthConfigId = useConnectorsStore((s) => s.formData.auth.oauthConfigId as string | undefined);
  const connectorConfig = useConnectorsStore((s) => s.connectorConfig);

  const oauthConfigId = useMemo(() => {
    const fromForm = formOAuthConfigId?.trim();
    if (fromForm) return fromForm;
    const auth = connectorConfig?.config?.auth as { oauthConfigId?: string } | undefined;
    return typeof auth?.oauthConfigId === 'string' ? auth.oauthConfigId.trim() : '';
  }, [formOAuthConfigId, connectorConfig?.config?.auth]);

  const savedInstanceName = useMemo(() => {
    const auth = connectorConfig?.config?.auth as { oauthInstanceName?: string } | undefined;
    return typeof auth?.oauthInstanceName === 'string' ? auth.oauthInstanceName.trim() : '';
  }, [connectorConfig?.config?.auth]);

  const connectorType = panelConnector?.type ?? '';
  const [loading, setLoading] = useState(false);
  const [details, setDetails] = useState<LinkedOAuthDetails | null>(null);

  useEffect(() => {
    if (!panelConnectorId || selectedAuthType !== 'OAUTH' || !connectorType) {
      setLoading(false);
      setDetails(null);
      return;
    }

    if (!oauthConfigId) {
      setLoading(false);
      setDetails({
        appTitle: 'Manual OAuth credentials',
        manual: true,
      });
      return;
    }

    let cancelled = false;
    setLoading(true);

    const applyFromFullDoc = (full: Record<string, unknown>): LinkedOAuthDetails => {
      const nameFromApi =
        (typeof full.oauthInstanceName === 'string' && full.oauthInstanceName.trim()) ||
        (typeof full.oauth_instance_name === 'string' && full.oauth_instance_name.trim()) ||
        '';
      const appTitle = nameFromApi || savedInstanceName || oauthConfigId;
      return { appTitle };
    };

    const run = async () => {
      try {
        const full = await ConnectorsApi.getOAuthConfig(connectorType, oauthConfigId);
        if (cancelled) return;
        if (full && typeof full === 'object' && Object.keys(full).length > 0) {
          setDetails(applyFromFullDoc(full));
          return;
        }
      } catch {
        /* fall through to list */
      }

      try {
        const res = await ConnectorsApi.listOAuthConfigs(connectorType, 1, 200);
        if (cancelled) return;
        const apps = (res.oauthConfigs ?? []) as OAuthListRow[];
        const app = apps.find((a) => a._id === oauthConfigId);
        if (app) {
          setDetails({
            appTitle: rowLabel(app),
          });
          return;
        }
      } catch {
        /* handled below */
      }

      if (!cancelled) {
        setDetails({
          appTitle: savedInstanceName || 'OAuth registration',
        });
      }
    };

    void run().finally(() => {
      if (!cancelled) setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [panelConnectorId, selectedAuthType, connectorType, oauthConfigId, savedInstanceName]);

  if (!panelConnectorId || selectedAuthType !== 'OAUTH') return null;

  const credentialSurface = {
    padding: '10px 12px',
    borderRadius: 'var(--radius-2)',
    border: '1px solid var(--olive-4)',
    background: 'var(--color-surface)',
    width: '100%',
    boxSizing: 'border-box' as const,
  };

  return (
    <Flex direction="column" gap="3" style={{ width: '100%' }}>
      <Flex direction="column" gap="1">
        <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          OAuth app in use
        </Text>
        <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
          This instance is tied to one OAuth registration. It cannot be switched from this screen.
        </Text>
      </Flex>

      {loading ? (
        <Flex align="center" gap="2" style={credentialSurface}>
          <Spinner />
          <Text size="2" color="gray">
            Loading OAuth app…
          </Text>
        </Flex>
      ) : details?.manual ? (
        <Box style={{ ...credentialSurface, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {details.appTitle}
          </Text>
          <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
            Not linked to a saved OAuth app registration. Use the credential fields below (from the
            connector schema).
          </Text>
        </Box>
      ) : (
        <Box style={{ ...credentialSurface, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)', wordBreak: 'break-word' }}>
            {details?.appTitle ?? '—'}
          </Text>
        </Box>
      )}
    </Flex>
  );
}
