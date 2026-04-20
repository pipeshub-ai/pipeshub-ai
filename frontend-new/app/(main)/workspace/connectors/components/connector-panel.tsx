'use client';

import React, { useEffect, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { Flex, Tabs, Box, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon } from '@/app/components/ui';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import {
  WorkspaceRightPanel,
  useWorkspaceRightPanelBodyRefresh,
} from '@/app/(main)/workspace/components/workspace-right-panel';
import { FormField } from '@/app/(main)/workspace/components/form-field';
import { AuthenticateTab } from './authenticate-tab';
import { AuthorizeTab } from './authorize-tab';
import { ConfigureTab } from './configure-tab';
import { SelectRecordsPage } from './select-records-page';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { useToastStore } from '@/lib/store/toast-store';
import { useConnectorsStore } from '../store';
import { ConnectorsApi } from '../api';
import {
  isNoneAuthType,
  isOAuthType,
  isConnectorConfigAuthenticated,
  isConnectorInstanceAuthenticatedForUi,
} from '../utils/auth-helpers';
import { trimConnectorConfig } from '../utils/trim-config';
import { resolveAuthFields } from './authenticate-tab/helpers';
import { useConnectorOAuthPopup } from './authenticate-tab/use-connector-oauth-popup';
import type { PanelTab } from '../types';

// ========================================
// Component
// ========================================

export function ConnectorPanel() {
  const router = useRouter();
  const addToast = useToastStore((s) => s.addToast);
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);
  const {
    isPanelOpen,
    panelConnector,
    panelConnectorId,
    panelActiveTab,
    panelView,
    connectorSchema,
    connectorConfig,
    isLoadingSchema,
    isLoadingConfig,
    isSavingAuth,
    isSavingConfig,
    authState,
    selectedAuthType,
    instanceName,
    instanceNameError,
    formData,
    registryConnectors,
    closePanel,
    setPanelActiveTab,
    setSchemaAndConfig,
    setIsLoadingSchema,
    setIsLoadingConfig,
    setSchemaError,
    setInstanceName,
    setInstanceNameError,
    setIsSavingAuth,
    setIsSavingConfig,
    setSaveError,
    setAuthState,
    setShowConfigSuccessDialog,
    setNewlyConfiguredConnectorId,
    bumpCatalogRefresh,
    oauthAuthorizeUiEpoch,
    selectedScope,
  } = useConnectorsStore();

  const isCreateMode = !panelConnectorId;
  const isLoading = isLoadingSchema || isLoadingConfig;
  const connectorName = panelConnector?.name ?? '';
  const connectorType = panelConnector?.type ?? '';
  /** Prefer list row `authType` when editing an instance so tabs stay correct before config fetch. */
  const authTypeForOAuthUi =
    (panelConnectorId ? panelConnector?.authType : undefined) ||
    selectedAuthType ||
    connectorConfig?.authType ||
    panelConnector?.authType ||
    '';
  const showAuthorizeTab = Boolean(panelConnectorId && isOAuthType(authTypeForOAuthUi));
  const authTypeForConfigureGate =
    connectorConfig?.authType || selectedAuthType || panelConnector?.authType || '';
  /**
   * OAuth gate: inferred auth from GET `/config` (incl. nested tokens), explicit `false` on
   * config over stale list rows, else list-row while config omits a top-level flag.
   */
  const instanceAuthenticated = isConnectorInstanceAuthenticatedForUi(
    panelConnectorId,
    panelConnector,
    connectorConfig
  );
  const configureTabEnabled =
    Boolean(connectorConfig) &&
    (isNoneAuthType(authTypeForConfigureGate) ||
      !isOAuthType(authTypeForConfigureGate) ||
      instanceAuthenticated);
  // Use registry connector's display name so the panel always shows the type name
  // (e.g. "Pipeshub docs") rather than an instance name when creating a new connector.
  const connectorTypeName = registryConnectors.find((c) => c.type === connectorType)?.name ?? connectorName;

  const prevPanelTabRef = useRef<PanelTab | null>(null);

  // ── Fetch schema + config on panel open ──────────────────────
  useEffect(() => {
    if (!isPanelOpen || !connectorType) return;

    const fetchData = async () => {
      setIsLoadingSchema(true);
      setSchemaError(null);
      if (!isCreateMode) {
        setIsLoadingConfig(true);
      }
      try {
        if (isCreateMode) {
          const schemaRes = await ConnectorsApi.getConnectorSchema(connectorType);
          setSchemaAndConfig(schemaRes.schema);
        } else {
          const [schemaRes, configRes] = await Promise.all([
            ConnectorsApi.getConnectorSchema(connectorType),
            ConnectorsApi.getConnectorConfig(panelConnectorId!),
          ]);
          setSchemaAndConfig(schemaRes.schema, configRes);
        }
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : 'Failed to load connector configuration';
        setSchemaError(message);
      } finally {
        setIsLoadingSchema(false);
        setIsLoadingConfig(false);
      }
    };

    fetchData();
  }, [isPanelOpen, connectorType, isCreateMode, panelConnectorId]);

  // If auth type changes away from OAuth, leave the Authorize tab value so Radix Tabs does not break.
  useEffect(() => {
    if (panelActiveTab === 'authorize' && !showAuthorizeTab) {
      setPanelActiveTab('authenticate');
    }
  }, [panelActiveTab, showAuthorizeTab, setPanelActiveTab]);

  // Do not stay on Configure when the instance is not authenticated (e.g. stale tab or API catch-up).
  useEffect(() => {
    if (!isPanelOpen || isLoading) return;
    if (panelActiveTab !== 'configure' || configureTabEnabled) return;
    // After `openPanel`, config is cleared until GET /config completes — do not use stale/absent config to switch tabs.
    if (panelConnectorId && !connectorConfig) return;
    if (showAuthorizeTab && !instanceAuthenticated) {
      setPanelActiveTab('authorize');
    } else {
      setPanelActiveTab('authenticate');
    }
  }, [
    isPanelOpen,
    isLoading,
    panelActiveTab,
    configureTabEnabled,
    showAuthorizeTab,
    instanceAuthenticated,
    panelConnectorId,
    connectorConfig,
    setPanelActiveTab,
  ]);

  /** Reload schema + config so the Authenticate tab shows saved credentials after navigating back. */
  const refreshPanelFromServer = useCallback(async () => {
    const id = useConnectorsStore.getState().panelConnectorId;
    const type = useConnectorsStore.getState().panelConnector?.type;
    if (!id || !type) return;
    try {
      setIsLoadingConfig(true);
      const [schemaRes, configRes] = await Promise.all([
        ConnectorsApi.getConnectorSchema(type),
        ConnectorsApi.getConnectorConfig(id),
      ]);
      setSchemaAndConfig(schemaRes.schema, configRes);
    } catch {
      // leave existing form; user can retry
    } finally {
      setIsLoadingConfig(false);
    }
  }, [setSchemaAndConfig, setIsLoadingConfig]);

  /**
   * Belt-and-suspenders re-fetch used after OAuth completion. Skips `setIsLoadingConfig` so
   * the panel body doesn't flash the full-panel loader — `checkAuthStatus` already committed the
   * authenticated config; this just ensures the Authenticate tab form data is also up-to-date.
   */
  const refreshPanelSilent = useCallback(async () => {
    const id = useConnectorsStore.getState().panelConnectorId;
    const type = useConnectorsStore.getState().panelConnector?.type;
    if (!id || !type) return;
    try {
      const [schemaRes, configRes] = await Promise.all([
        ConnectorsApi.getConnectorSchema(type),
        ConnectorsApi.getConnectorConfig(id),
      ]);
      setSchemaAndConfig(schemaRes.schema, configRes);
    } catch {
      // non-fatal — checkAuthStatus already committed the panel state
    }
  }, [setSchemaAndConfig]);

  const { requestRefresh: requestDrawerBodyRefresh, refreshNonce: drawerBodyRefreshNonce } =
    useWorkspaceRightPanelBodyRefresh();
  const { startOAuthPopup, isAuthenticating: isOAuthPopupBusy } = useConnectorOAuthPopup({
    onDrawerBodyRefresh: requestDrawerBodyRefresh,
    onAfterConnectorOAuthHydrate: refreshPanelSilent,
  });

  /**
   * Refetch config whenever the active tab changes (user click or programmatic), not only
   * via Radix `onValueChange` (programmatic `setPanelActiveTab` often skips that callback).
   */
  useEffect(() => {
    if (!isPanelOpen || !panelConnectorId) {
      prevPanelTabRef.current = null;
      return;
    }
    const prev = prevPanelTabRef.current;
    prevPanelTabRef.current = panelActiveTab;
    if (
      prev !== null &&
      prev !== panelActiveTab &&
      (panelActiveTab === 'authenticate' ||
        panelActiveTab === 'authorize' ||
        panelActiveTab === 'configure')
    ) {
      void refreshPanelFromServer();
    }
  }, [isPanelOpen, panelConnectorId, panelActiveTab, refreshPanelFromServer]);

  // ── Save handlers ────────────────────────────────────────────

  const handleSaveAuth = useCallback(async () => {
    if (isCreateMode) {
      // Create mode: POST /connectors
      if (!instanceName.trim()) {
        setInstanceNameError('Instance name is required');
        addToast({
          variant: 'warning',
          title: 'Instance name required',
          description: 'Enter a name for this connector instance before continuing.',
          duration: 4000,
        });
        return;
      }

      try {
        setIsSavingAuth(true);
        setSaveError(null);

        if (
          selectedAuthType === 'OAUTH' &&
          isProfileInitialized &&
          isAdmin === false
        ) {
          const oauthId = formData.auth.oauthConfigId;
          if (
            oauthId === undefined ||
            oauthId === null ||
            (typeof oauthId === 'string' && oauthId.trim() === '')
          ) {
            setSaveError('Please select an OAuth app.');
            return;
          }
        }

        const result = (await ConnectorsApi.createConnectorInstance({
          connectorType,
          instanceName: instanceName.trim(),
          scope: selectedScope,
          authType: selectedAuthType,
          config: {
            auth: {
              ...trimConnectorConfig(formData.auth),
              connectorScope: selectedScope,
            },
          },
          baseUrl: window.location.origin,
        })) as {
          connector?: { connectorId?: string };
          _key?: string;
          connectorId?: string;
        };

        const newConnectorId =
          result?.connector?.connectorId ?? result?._key ?? result?.connectorId;
        if (!newConnectorId) {
          setSaveError('Create succeeded but no connector id was returned');
          return;
        }

        useConnectorsStore.setState({
          panelConnectorId: newConnectorId,
          isAuthTypeImmutable: true,
        });

        // Load merged schema + saved config so the Configure tab enables and filters/sync hydrate.
        try {
          setIsLoadingConfig(true);
          const [schemaRes, configRes] = await Promise.all([
            ConnectorsApi.getConnectorSchema(connectorType),
            ConnectorsApi.getConnectorConfig(newConnectorId),
          ]);
          setSchemaAndConfig(schemaRes.schema, configRes);
        } catch {
          setSaveError('Connector was created but configuration could not be loaded. Try reopening the panel.');
        } finally {
          setIsLoadingConfig(false);
        }

        if (isNoneAuthType(selectedAuthType)) {
          setAuthState('success');
        }

        bumpCatalogRefresh();
        if (isOAuthType(selectedAuthType) && !isNoneAuthType(selectedAuthType)) {
          setPanelActiveTab('authorize');
        } else {
          setPanelActiveTab('configure');
        }
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : 'Failed to create connector';
        setSaveError(message);
      } finally {
        setIsSavingAuth(false);
      }
    } else {
      // Edit mode: PUT /config/auth
      try {
        setIsSavingAuth(true);
        setSaveError(null);

        if (
          selectedAuthType === 'OAUTH' &&
          isProfileInitialized &&
          isAdmin === false
        ) {
          const oauthId = formData.auth.oauthConfigId;
          if (
            oauthId === undefined ||
            oauthId === null ||
            (typeof oauthId === 'string' && oauthId.trim() === '')
          ) {
            setSaveError('Please select an OAuth app.');
            return;
          }
        }

        await ConnectorsApi.saveAuthConfig(panelConnectorId!, {
          auth: {
            ...trimConnectorConfig(formData.auth),
            connectorScope: selectedScope,
          },
          baseUrl: window.location.origin,
        });

        let configRes: Awaited<ReturnType<typeof ConnectorsApi.getConnectorConfig>> | null = null;
        try {
          setIsLoadingConfig(true);
          const [schemaRes, fetched] = await Promise.all([
            ConnectorsApi.getConnectorSchema(connectorType),
            ConnectorsApi.getConnectorConfig(panelConnectorId!),
          ]);
          configRes = fetched;
          setSchemaAndConfig(schemaRes.schema, configRes);
        } catch {
          // Non-fatal — user can reopen panel
        } finally {
          setIsLoadingConfig(false);
        }

        if (isOAuthType(selectedAuthType)) {
          setPanelActiveTab(
            isConnectorConfigAuthenticated(configRes) ? 'configure' : 'authorize'
          );
        } else {
          setPanelActiveTab('configure');
        }
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : 'Failed to save auth configuration';
        setSaveError(message);
      } finally {
        setIsSavingAuth(false);
      }
    }
  }, [
    isCreateMode,
    instanceName,
    connectorType,
    selectedAuthType,
    formData.auth,
    panelConnectorId,
    bumpCatalogRefresh,
    setSchemaAndConfig,
    setIsLoadingConfig,
    setAuthState,
    setPanelActiveTab,
    setInstanceNameError,
    setIsSavingAuth,
    setSaveError,
    isAdmin,
    isProfileInitialized,
    selectedScope,
    addToast,
  ]);

  const handleSaveConfig = useCallback(async () => {
    const currentConnectorId =
      panelConnectorId || useConnectorsStore.getState().panelConnectorId;

    if (!currentConnectorId) {
      setSaveError('No connector ID found. Please complete authentication first.');
      return;
    }

    try {
      setIsSavingConfig(true);
      setSaveError(null);

      const trimmedCustomValues = trimConnectorConfig(formData.sync.customValues);
      const syncPayload: {
        selectedStrategy: string;
        customValues: Record<string, unknown>;
        scheduledConfig?: Record<string, unknown>;
        [key: string]: unknown;
      } = {
        selectedStrategy: formData.sync.selectedStrategy,
        customValues: trimmedCustomValues,
        // Spread custom values at the top level (required by backend for validation)
        ...trimmedCustomValues,
      };

      if (formData.sync.selectedStrategy === 'SCHEDULED') {
        syncPayload.scheduledConfig = {
          intervalMinutes: formData.sync.scheduledConfig.intervalMinutes ?? 60,
          ...(formData.sync.scheduledConfig.timezone
            ? { timezone: formData.sync.scheduledConfig.timezone }
            : {}),
          ...(formData.sync.scheduledConfig.startDateTime
            ? { startDateTime: formData.sync.scheduledConfig.startDateTime }
            : {}),
        };
      }

      await ConnectorsApi.saveFiltersSyncConfig(currentConnectorId, {
        sync: syncPayload,
        filters: {
          sync: { values: trimConnectorConfig(formData.filters.sync) },
          indexing: { values: trimConnectorConfig(formData.filters.indexing) },
        },
        baseUrl: window.location.origin,
      });

      // After successful save, navigate to the connector type page
      // and show the success dialog
      const savedConnectorType = connectorType;
      const scope = useConnectorsStore.getState().selectedScope;

      // Close the configuration panel
      closePanel();

      // Navigate to connector type page with connectorType query param
      // and trigger the success dialog
      if (savedConnectorType) {
        setNewlyConfiguredConnectorId(currentConnectorId);
        setShowConfigSuccessDialog(true);
        bumpCatalogRefresh();
        router.push(
          `/workspace/connectors/${scope}/?connectorType=${encodeURIComponent(savedConnectorType)}`
        );
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to save configuration';
      setSaveError(message);
    } finally {
      setIsSavingConfig(false);
    }
  }, [
    panelConnectorId,
    formData,
    closePanel,
    connectorType,
    router,
    setShowConfigSuccessDialog,
    setNewlyConfiguredConnectorId,
    bumpCatalogRefresh,
  ]);

  // ── Footer logic ─────────────────────────────────────────────

  const isAuthReady =
    authState === 'success' || isNoneAuthType(selectedAuthType);

  // Check if all required auth fields are filled
  const areRequiredAuthFieldsFilled = (() => {
    if (!connectorSchema) return false;
    const authFields = resolveAuthFields(connectorSchema.auth, selectedAuthType);
    const requiredFields = authFields.filter((f) => f.required);
    if (requiredFields.length === 0) return true;
    return requiredFields.every((f) => {
      const val = formData.auth[f.name];
      if (val === undefined || val === null || val === '') return false;
      if (typeof val === 'string' && val.trim() === '') return false;
      return true;
    });
  })();

  const handleBackFromConfigure = useCallback(async () => {
    await refreshPanelFromServer();
    if (showAuthorizeTab) {
      setPanelActiveTab('authorize');
    } else {
      setPanelActiveTab('authenticate');
    }
  }, [refreshPanelFromServer, showAuthorizeTab, setPanelActiveTab]);

  const handleBackFromAuthorize = useCallback(async () => {
    await refreshPanelFromServer();
    setPanelActiveTab('authenticate');
  }, [refreshPanelFromServer, setPanelActiveTab]);

  const footerConfig = getFooterConfig({
    panelView,
    panelActiveTab,
    isAuthReady,
    areRequiredAuthFieldsFilled,
    instanceName,
    hasConnectorId: !!panelConnectorId,
    authTypeForConfigureGate,
    instanceAuthenticated,
    isSavingAuth,
    isSavingConfig,
    isLoadingSchema,
    isLoadingConfig,
    onNext: handleSaveAuth,
    onSave: handleSaveConfig,
    onContinueFromAuthorize: async () => {
      await refreshPanelFromServer();
      setPanelActiveTab('configure');
    },
    onBackFromConfigure: handleBackFromConfigure,
    onBackFromAuthorize: handleBackFromAuthorize,
    isOAuthPopupBusy,
  });

  // ── Header ───────────────────────────────────────────────────

  const headerActions = (
    <Flex align="center" gap="1">
      {connectorSchema?.documentationLinks?.[0]?.url && (
        <IconButton
          variant="ghost"
          color="gray"
          size="1"
          onClick={() => {
            const url = connectorSchema?.documentationLinks?.[0]?.url;
            if (url) window.open(url, '_blank', 'noopener,noreferrer');
          }}
          style={{ cursor: 'pointer' }}
        >
          <MaterialIcon
            name="open_in_new"
            size={16}
            color="var(--gray-11)"
          />
        </IconButton>
      )}
    </Flex>
  );

  // ── Render panel icon as img (connector icon) ────────────────

  const panelIcon = panelConnector ? (
    <ConnectorIcon type={panelConnector.type} size={16} />
  ) : undefined;

  return (
    <WorkspaceRightPanel
      open={isPanelOpen}
      onOpenChange={(open) => {
        if (!open) closePanel();
      }}
      title={`${connectorTypeName} Configuration`}
      icon={panelIcon}
      headerActions={headerActions}
      hideFooter={panelView === 'select-records'}
      primaryLabel={footerConfig.primaryLabel}
      primaryDisabled={footerConfig.primaryDisabled}
      primaryLoading={footerConfig.primaryLoading}
      primaryTooltip={footerConfig.primaryTooltip}
      onPrimaryClick={footerConfig.onPrimary}
      secondaryLabel={footerConfig.secondaryLabel}
      onSecondaryClick={footerConfig.onSecondary}
    >
      {isLoading ? (
        <Flex
          align="center"
          justify="center"
          style={{ height: 200 }}
        >
          <LottieLoader variant="loader" size={48} showLabel label="Loading configuration…" />
        </Flex>
      ) : panelView === 'select-records' ? (
        <SelectRecordsPage />
      ) : (
        <Flex direction="column" style={{ height: '100%' }}>
          {/* ── Create mode: Instance name input ── */}
          {isCreateMode && connectorSchema && (
            <Box style={{ marginBottom: 16 }}>
              <FormField
                label="Instance Name"
                error={instanceNameError ?? undefined}
              >
                <input
                  type="text"
                  value={instanceName}
                  onChange={(e) => setInstanceName(e.target.value)}
                  placeholder={`e.g. My ${connectorTypeName}`}
                  style={{
                    height: 32,
                    width: '100%',
                    padding: '6px 8px',
                    backgroundColor: 'var(--color-surface)',
                    border: '1px solid var(--gray-a5)',
                    borderRadius: 'var(--radius-2)',
                    fontSize: 14,
                    fontFamily: 'var(--default-font-family)',
                    color: 'var(--gray-12)',
                    boxSizing: 'border-box',
                    outline: 'none',
                  }}
                />
              </FormField>
            </Box>
          )}

          {/* ── Tab bar ── */}
          <Tabs.Root
            value={panelActiveTab}
            onValueChange={(v) => {
              const tab = v as PanelTab;
              if (tab === 'configure' && !configureTabEnabled) return;
              setPanelActiveTab(tab);
            }}
          >
            <Tabs.List
              style={{
                borderBottom: '1px solid var(--gray-a6)',
              }}
            >
              <Tabs.Trigger value="authenticate">
                Authenticate Instance
              </Tabs.Trigger>
              {showAuthorizeTab ? (
                <Tabs.Trigger value="authorize">Authorize</Tabs.Trigger>
              ) : null}
              <Tabs.Trigger
                value="configure"
                disabled={!configureTabEnabled}
                style={!configureTabEnabled ? { opacity: 0.5, cursor: 'not-allowed' } : undefined}
              >
                Configure Records
              </Tabs.Trigger>
            </Tabs.List>

            <Box style={{ paddingTop: 16 }}>
              <Tabs.Content value="authenticate">
                <AuthenticateTab />
              </Tabs.Content>
              {showAuthorizeTab ? (
                <Tabs.Content value="authorize">
                  <AuthorizeTab
                    key={`authorize-${panelConnectorId ?? 'new'}-${oauthAuthorizeUiEpoch}-${instanceAuthenticated ? '1' : '0'}-${drawerBodyRefreshNonce}`}
                    startOAuthPopup={startOAuthPopup}
                    isAuthenticating={isOAuthPopupBusy}
                  />
                </Tabs.Content>
              ) : null}
              <Tabs.Content value="configure">
                <ConfigureTab />
              </Tabs.Content>
            </Box>
          </Tabs.Root>
        </Flex>
      )}
    </WorkspaceRightPanel>
  );
}

// ========================================
// Sub-components
// ========================================


// ========================================
// Footer config helper
// ========================================

interface FooterConfig {
  primaryLabel: string;
  primaryDisabled: boolean;
  primaryLoading: boolean;
  primaryTooltip?: string;
  onPrimary?: () => void;
  secondaryLabel: string;
  onSecondary?: () => void;
}

function getFooterConfig({
  panelView,
  panelActiveTab,
  isAuthReady: _isAuthReady,
  areRequiredAuthFieldsFilled,
  instanceName,
  hasConnectorId,
  authTypeForConfigureGate,
  instanceAuthenticated,
  isSavingAuth,
  isSavingConfig,
  isLoadingSchema,
  isLoadingConfig,
  onNext,
  onSave,
  onContinueFromAuthorize,
  onBackFromConfigure,
  onBackFromAuthorize,
  isOAuthPopupBusy,
}: {
  panelView: string;
  panelActiveTab: PanelTab;
  isAuthReady: boolean;
  areRequiredAuthFieldsFilled: boolean;
  instanceName: string;
  hasConnectorId: boolean;
  authTypeForConfigureGate: string;
  instanceAuthenticated: boolean;
  isSavingAuth: boolean;
  isSavingConfig: boolean;
  isLoadingSchema: boolean;
  isLoadingConfig: boolean;
  onNext: () => void;
  onSave: () => void;
  onContinueFromAuthorize: () => void | Promise<void>;
  onBackFromConfigure: () => void | Promise<void>;
  onBackFromAuthorize: () => void | Promise<void>;
  isOAuthPopupBusy: boolean;
}): FooterConfig {
  if (panelView === 'select-records') {
    // Footer is hidden for select-records (handled inside that component)
    return {
      primaryLabel: '',
      primaryDisabled: true,
      primaryLoading: false,
      secondaryLabel: '',
    };
  }

  if (panelActiveTab === 'authenticate') {
    const instanceNameReady = hasConnectorId || instanceName.trim().length > 0;
    return {
      primaryLabel: 'Next →',
      primaryDisabled:
        !areRequiredAuthFieldsFilled || isSavingAuth || !instanceNameReady,
      primaryLoading: isSavingAuth,
      primaryTooltip: !instanceNameReady
        ? 'Enter an instance name to continue'
        : !areRequiredAuthFieldsFilled
          ? 'Fill in all required fields to continue'
          : undefined,
      onPrimary: onNext,
      secondaryLabel: 'Cancel',
    };
  }

  if (panelActiveTab === 'authorize') {
    return {
      primaryLabel: 'Continue to configuration →',
      primaryDisabled: !instanceAuthenticated || isOAuthPopupBusy,
      primaryLoading: isOAuthPopupBusy,
      primaryTooltip: isOAuthPopupBusy
        ? 'Finish signing in with your provider…'
        : !instanceAuthenticated
          ? 'Complete OAuth authorization before configuring sync and filters'
          : undefined,
      onPrimary: onContinueFromAuthorize,
      secondaryLabel: '← Back to credentials',
      onSecondary: () => {
        void onBackFromAuthorize();
      },
    };
  }

  // configure tab
  const configureSaveAllowed =
    hasConnectorId &&
    (instanceAuthenticated ||
      isNoneAuthType(authTypeForConfigureGate) ||
      !isOAuthType(authTypeForConfigureGate));

  const configTooltip = !hasConnectorId
    ? 'Complete authentication first to save configuration'
    : !configureSaveAllowed
    ? 'Complete OAuth authorization before configuring sync and filters.'
    : isLoadingSchema || isLoadingConfig
    ? 'Loading configuration…'
    : undefined;

  return {
    primaryLabel: 'Save Configuration',
    primaryDisabled: !configureSaveAllowed || isSavingConfig || isLoadingSchema || isLoadingConfig,
    primaryLoading: isSavingConfig,
    primaryTooltip: configTooltip,
    onPrimary: onSave,
    secondaryLabel: '← Back',
    onSecondary: () => {
      void onBackFromConfigure();
    },
  };
}
