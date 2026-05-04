'use client';

import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Flex, Tabs, Box, Button, Text } from '@radix-ui/themes';
import React, { useEffect, useCallback, useRef, useState } from 'react';
import { ConnectorIcon, MaterialIcon } from '@/app/components/ui';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import {
  WorkspaceRightPanel,
  useWorkspaceRightPanelBodyRefresh,
  useWorkspaceDrawerNestedModalHost,
} from '@/app/(main)/workspace/components/workspace-right-panel';
import { ConfirmationDialog } from '@/app/(main)/workspace/components/confirmation-dialog';
import { FormField } from '@/app/(main)/workspace/components/form-field';
import { AuthenticateTab } from './authenticate-tab';
import { AuthorizeTab } from './authorize-tab';
import { ConfigureTab } from './configure-tab';
import { SelectRecordsPage } from './select-records-page';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { useConnectorsStore } from '../store';
import { ConnectorsApi } from '../api';
import {
  isNoneAuthType,
  isOAuthType,
  isConnectorConfigAuthenticated,
  isConnectorInstanceAuthenticatedForUi,
  resolveOAuthFieldVisibility,
} from '../utils/auth-helpers';
import { trimConnectorConfig } from '../utils/trim-config';
import { collectSyncCustomFieldErrors } from '../utils/sync-custom-fields-validation';
import {
  visibleAuthSchemaFields,
  collectAuthFieldErrors,
} from './authenticate-tab/auth-step-validation';
import { useConnectorOAuthPopup } from './authenticate-tab/use-connector-oauth-popup';
import {
  hasAnySyncFiltersSelected,
  isManualIndexingEnabled,
} from '../utils/sync-filter-save-guards';
import type { PanelTab } from '../types';
import { getConnectorDocumentationUrl } from '../utils/connector-metadata';

/** Non-admin OAuth instances must pick an OAuth app before save. */
function oauthAppSelectionError(
  selectedAuthType: string,
  oauthConfigId: unknown,
  isProfileInitialized: boolean,
  isAdmin: boolean
): string | null {
  if (selectedAuthType !== 'OAUTH' || !isProfileInitialized || isAdmin !== false) {
    return null;
  }
  if (
    oauthConfigId === undefined ||
    oauthConfigId === null ||
    (typeof oauthConfigId === 'string' && oauthConfigId.trim() === '')
  ) {
    return 'Please select an OAuth app.';
  }
  return null;
}

/** Scroll the connector panel body to the first invalid sync custom field (matches auth step UX). */
function scrollToFirstSyncCustomFieldError(
  syncCustomFields: { name: string }[],
  syncFieldErrors: Record<string, string>
) {
  const name = syncCustomFields.find((f) => syncFieldErrors[f.name])?.name;
  if (!name) return;
  requestAnimationFrame(() => {
    document
      .querySelector(`[data-ph-field="${name}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
}

// ========================================
// Component
// ========================================

export function ConnectorPanel() {
  const router = useRouter();
  const { t } = useTranslation();
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
    conditionalDisplay,
    registryConnectors,
    closePanel,
    setPanelActiveTab,
    setSchemaAndConfig,
    setIsLoadingSchema,
    setIsLoadingConfig,
    setSchemaError,
    setInstanceName,
    setInstanceNameError,
    mergeFormErrors,
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

  const [syncSaveConfirmOpen, setSyncSaveConfirmOpen] = useState(false);
  const [syncSaveConfirmKind, setSyncSaveConfirmKind] = useState<'manual' | 'wide_sync'>('wide_sync');
  const authSaveInFlightRef = useRef(false);
  const configSaveInFlightRef = useRef(false);

  const connectorPanelNestedModalHost = useWorkspaceDrawerNestedModalHost(isPanelOpen);

  const isCreateMode = !panelConnectorId;
  const isLoading = isLoadingSchema || isLoadingConfig;
  const connectorName = panelConnector?.name ?? '';
  const connectorType = panelConnector?.type ?? '';
  const storedListAuthType = panelConnectorId ? (panelConnector?.authType ?? '') : '';
  const schemaTypes = connectorSchema?.auth?.supportedAuthTypes ?? [];
  // Once the schema is loaded, only treat the stored authType as authoritative if the
  // schema still supports it. This prevents the Authorize tab from persisting on
  // connectors whose auth type was migrated (e.g. OAUTH → CUSTOM).
  const resolvedListAuthForOAuth =
    storedListAuthType &&
    (!connectorSchema || schemaTypes.length === 0 || schemaTypes.includes(storedListAuthType))
      ? storedListAuthType
      : '';
  /** Prefer list row `authType` when editing an instance so tabs stay correct before config fetch. */
  const authTypeForOAuthUi =
    resolvedListAuthForOAuth ||
    selectedAuthType ||
    connectorConfig?.authType ||
    panelConnector?.authType ||
    '';
  const showAuthorizeTab = Boolean(panelConnectorId && isOAuthType(authTypeForOAuthUi));
  // Prefer schema-resolved selectedAuthType over the stored connectorConfig?.authType so that
  // connectors migrated from OAUTH→CUSTOM (old DB rows carry "OAUTH") still unlock the
  // Configure tab once CUSTOM credentials have been saved.
  const authTypeForConfigureGate =
    selectedAuthType || connectorConfig?.authType || panelConnector?.authType || '';
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
  /** Bumped when the panel open-fetch effect re-runs or the drawer closes so stale requests cannot flip loaders. */
  const panelOpenFetchGen = useRef(0);

  // ── Fetch schema + config on panel open ──────────────────────
  useEffect(() => {
    if (!isPanelOpen || !connectorType) {
      panelOpenFetchGen.current += 1;
      return;
    }

    const gen = ++panelOpenFetchGen.current;
    const instanceKey = panelConnectorId ?? '';

    const fetchData = async () => {
      setIsLoadingSchema(true);
      setSchemaError(null);
      if (!isCreateMode) {
        setIsLoadingConfig(true);
      }
      try {
        if (isCreateMode) {
          const schemaRes = await ConnectorsApi.getConnectorSchema(connectorType);
          if (gen !== panelOpenFetchGen.current) return;
          const s = useConnectorsStore.getState();
          if (s.panelConnector?.type !== connectorType || (s.panelConnectorId ?? '') !== instanceKey) {
            return;
          }
          setSchemaAndConfig(schemaRes.schema);
        } else {
          const [schemaRes, configRes] = await Promise.all([
            ConnectorsApi.getConnectorSchema(connectorType),
            ConnectorsApi.getConnectorConfig(panelConnectorId!),
          ]);
          if (gen !== panelOpenFetchGen.current) return;
          const s = useConnectorsStore.getState();
          if (
            s.panelConnector?.type !== connectorType ||
            s.panelConnectorId !== panelConnectorId
          ) {
            return;
          }
          setSchemaAndConfig(schemaRes.schema, configRes);
        }
      } catch (err: unknown) {
        if (gen !== panelOpenFetchGen.current) return;
        const s = useConnectorsStore.getState();
        if (s.panelConnector?.type !== connectorType || (s.panelConnectorId ?? '') !== instanceKey) {
          return;
        }
        const message = err instanceof Error ? err.message : 'Failed to load connector configuration';
        setSchemaError(message);
      } finally {
        if (gen === panelOpenFetchGen.current) {
          setIsLoadingSchema(false);
          setIsLoadingConfig(false);
        }
      }
    };

    void fetchData();
  }, [isPanelOpen, connectorType, isCreateMode, panelConnectorId, setSchemaAndConfig, setSchemaError, setIsLoadingSchema, setIsLoadingConfig]);

  // If auth type changes away from OAuth, leave the Authorize tab value so Radix Tabs does not break.
  useEffect(() => {
    if (panelActiveTab === 'authorize' && !showAuthorizeTab) {
      setPanelActiveTab('authenticate');
    }
  }, [panelActiveTab, showAuthorizeTab, setPanelActiveTab]);

  /**
   * Do not stay on Configure when the instance is not authenticated (e.g. stale tab or API catch-up).
   * `instanceAuthenticated` uses {@link isConnectorInstanceAuthenticatedForUi}: config-based inference and
   * explicit `isAuthenticated:false` win over the catalog row so we do not bounce incorrectly during GET /config lag;
   * see ordering documented on that helper before changing deps or tab logic here.
   */
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
      const s = useConnectorsStore.getState();
      if (s.panelConnectorId !== id || s.panelConnector?.type !== type) return;
      setSchemaAndConfig(schemaRes.schema, configRes);
    } catch {
      // leave existing form; user can retry
    } finally {
      const s = useConnectorsStore.getState();
      if (s.panelConnectorId === id && s.panelConnector?.type === type) {
        setIsLoadingConfig(false);
      }
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
      const s = useConnectorsStore.getState();
      if (s.panelConnectorId !== id || s.panelConnector?.type !== type) return;
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

  const resolveAuthenticateOrReturn = useCallback((): boolean => {
    if (!connectorSchema) {
      setSaveError(t('workspace.connectors.loadingConfig'));
      return false;
    }
    const { linkedOAuthAppId: oauthConfigIdStr, oauthFieldVisibility } = resolveOAuthFieldVisibility(
      formData.auth,
      connectorConfig,
      isCreateMode,
      isAdmin
    );

    const vFields = visibleAuthSchemaFields(
      connectorSchema.auth,
      selectedAuthType,
      conditionalDisplay,
      selectedAuthType === 'OAUTH' ? oauthFieldVisibility : null
    );
    const clearPatch: Record<string, null> = { oauthConfigId: null };
    for (const f of vFields) {
      clearPatch[f.name] = null;
    }
    mergeFormErrors(clearPatch);
    setInstanceNameError(null);
    setSaveError(null);

    const oauthErrEarly = oauthAppSelectionError(
      selectedAuthType,
      oauthConfigIdStr,
      isProfileInitialized,
      isAdmin
    );
    if (oauthErrEarly) {
      mergeFormErrors({ oauthConfigId: oauthErrEarly });
      requestAnimationFrame(() => {
        document
          .querySelector('[data-ph-oauth-app-select]')
          ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
      return false;
    }

    if (isCreateMode && !instanceName.trim()) {
      setInstanceNameError(t('workspace.actions.errors.instanceNameRequired'));
      requestAnimationFrame(() => {
        document
          .querySelector('[data-ph-connector-instance-name]')
          ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
      return false;
    }

    const fieldErrs = collectAuthFieldErrors(
      vFields,
      formData.auth,
      (f) => t('workspace.actions.validation.fieldRequired', { field: f.displayName }),
      (f) => t('workspace.actions.validation.fieldMustBeTrue', { field: f.displayName })
    );
    if (Object.keys(fieldErrs).length > 0) {
      mergeFormErrors(fieldErrs);
      const first = Object.keys(fieldErrs)[0];
      requestAnimationFrame(() => {
        document
          .querySelector(`[data-ph-field="${first}"]`)
          ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
      return false;
    }

    return true;
  }, [
    connectorSchema,
    selectedAuthType,
    conditionalDisplay,
    formData.auth,
    connectorConfig,
    isProfileInitialized,
    isAdmin,
    isCreateMode,
    instanceName,
    mergeFormErrors,
    setInstanceNameError,
    setSaveError,
    t,
  ]);

  const handleSaveAuth = useCallback(async () => {
    if (authSaveInFlightRef.current) {
      return;
    }
    if (!resolveAuthenticateOrReturn()) {
      return;
    }
    authSaveInFlightRef.current = true;

    if (isCreateMode) {
      // Create mode: POST /connectors
      try {
        setIsSavingAuth(true);
        setSaveError(null);

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
        const message = err instanceof Error ? err.message : t('workspace.connectors.toasts.createError');
        setSaveError(message);
      } finally {
        setIsSavingAuth(false);
        authSaveInFlightRef.current = false;
      }
    } else {
      // Edit mode: PUT /config/auth
      try {
        setIsSavingAuth(true);
        setSaveError(null);

        await ConnectorsApi.saveAuthConfig(panelConnectorId!, {
          auth: {
            ...trimConnectorConfig(formData.auth),
            connectorScope: selectedScope,
          },
          baseUrl: window.location.origin,
        });

        const editId = panelConnectorId!;
        const editType = connectorType;
        let configRes: Awaited<ReturnType<typeof ConnectorsApi.getConnectorConfig>> | null = null;
        try {
          setIsLoadingConfig(true);
          const [schemaRes, fetched] = await Promise.all([
            ConnectorsApi.getConnectorSchema(connectorType),
            ConnectorsApi.getConnectorConfig(panelConnectorId!),
          ]);
          configRes = fetched;
          const s = useConnectorsStore.getState();
          if (s.panelConnectorId === editId && s.panelConnector?.type === editType) {
            setSchemaAndConfig(schemaRes.schema, configRes);
          }
        } catch {
          // Non-fatal — user can reopen panel
        } finally {
          const s = useConnectorsStore.getState();
          if (s.panelConnectorId === editId && s.panelConnector?.type === editType) {
            setIsLoadingConfig(false);
          }
        }

        if (isOAuthType(selectedAuthType)) {
          setPanelActiveTab(
            isConnectorConfigAuthenticated(configRes) ? 'configure' : 'authorize'
          );
        } else {
          setPanelActiveTab('configure');
        }
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : t('workspace.connectors.toasts.authSaveError');
        setSaveError(message);
      } finally {
        setIsSavingAuth(false);
        authSaveInFlightRef.current = false;
      }
    }
  }, [
    isCreateMode,
    resolveAuthenticateOrReturn,
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
    setIsSavingAuth,
    setSaveError,
    selectedScope,
  ]);

  const performSaveConfig = useCallback(async () => {
    if (configSaveInFlightRef.current) {
      return;
    }
    const currentConnectorId =
      panelConnectorId || useConnectorsStore.getState().panelConnectorId;

    if (!currentConnectorId) {
      setSaveError('No connector ID found. Please complete authentication first.');
      return;
    }

    setSaveError(null);

    const syncCustomFields = connectorSchema?.sync?.customFields ?? [];
    const trimmedCustomValues = trimConnectorConfig(
      formData.sync.customValues
    ) as Record<string, unknown>;
    const syncFieldErrors = collectSyncCustomFieldErrors(syncCustomFields, trimmedCustomValues);

    const syncErrorPatch: Record<string, string | null | undefined> = {};
    for (const f of syncCustomFields) {
      syncErrorPatch[f.name] = syncFieldErrors[f.name] ?? '';
    }
    mergeFormErrors(syncErrorPatch);

    if (Object.keys(syncFieldErrors).length > 0) {
      scrollToFirstSyncCustomFieldError(syncCustomFields, syncFieldErrors);
      return;
    }

    configSaveInFlightRef.current = true;
    try {
      setIsSavingConfig(true);
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
      const message = err instanceof Error ? err.message : t('workspace.connectors.toasts.configSaveError');
      setSaveError(message);
    } finally {
      setIsSavingConfig(false);
      configSaveInFlightRef.current = false;
    }
  }, [
    panelConnectorId,
    formData,
    connectorSchema,
    mergeFormErrors,
    closePanel,
    connectorType,
    router,
    setShowConfigSuccessDialog,
    setNewlyConfiguredConnectorId,
    bumpCatalogRefresh,
    setSaveError,
    setIsSavingConfig,
    t,
  ]);

  const handleSaveConfig = useCallback(() => {
    if (configSaveInFlightRef.current) {
      return;
    }
    const currentConnectorId =
      panelConnectorId || useConnectorsStore.getState().panelConnectorId;

    if (!currentConnectorId) {
      setSaveError('No connector ID found. Please complete authentication first.');
      return;
    }

    setSaveError(null);

    const syncCustomFields = connectorSchema?.sync?.customFields ?? [];
    const trimmedCustomValues = trimConnectorConfig(
      formData.sync.customValues
    ) as Record<string, unknown>;
    const syncFieldErrors = collectSyncCustomFieldErrors(syncCustomFields, trimmedCustomValues);

    const syncErrorPatch: Record<string, string | null | undefined> = {};
    for (const f of syncCustomFields) {
      syncErrorPatch[f.name] = syncFieldErrors[f.name] ?? '';
    }
    mergeFormErrors(syncErrorPatch);

    if (Object.keys(syncFieldErrors).length > 0) {
      scrollToFirstSyncCustomFieldError(syncCustomFields, syncFieldErrors);
      return;
    }

    const syncFields = connectorSchema?.filters?.sync?.schema?.fields;
    const manualOn = isManualIndexingEnabled(formData.filters.indexing);
    const hasSync = hasAnySyncFiltersSelected(syncFields, formData.filters.sync);

    if (manualOn || !hasSync) {
      setSyncSaveConfirmKind(manualOn ? 'manual' : 'wide_sync');
      setSyncSaveConfirmOpen(true);
      return;
    }

    void performSaveConfig();
  }, [
    panelConnectorId,
    connectorSchema,
    formData.sync.customValues,
    formData.filters.sync,
    formData.filters.indexing,
    mergeFormErrors,
    performSaveConfig,
    setSaveError,
  ]);

  const handleConfirmSyncSave = useCallback(() => {
    setSyncSaveConfirmOpen(false);
    void performSaveConfig();
  }, [performSaveConfig]);

  const isAuthReady =
    authState === 'success' || isNoneAuthType(selectedAuthType);

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
    hasConnectorId: !!panelConnectorId,
    authTypeForConfigureGate,
    instanceAuthenticated,
    isSavingAuth,
    isSavingConfig,
    isLoadingSchema,
    isLoadingConfig,
    onNext: handleSaveAuth,
    onSave: handleSaveConfig,
    labels: {
      next: t('common.next'),
      saving: t('action.saving'),
      cancel: t('action.cancel'),
      loadingConfig: t('workspace.connectors.loadingConfig'),
      saveConfig: t('workspace.connectors.saveConfig'),
      completeAuthForSave: t('workspace.connectors.tooltips.authFirst'),
      continueToConfigure: t('workspace.connectors.continueToConfiguration'),
      oauthInProgress: t('workspace.connectors.oauthSigningIn'),
      authBeforeConfigure: t('workspace.connectors.authRequiredBeforeConfig'),
      backToAuth: t('workspace.connectors.backToCredentials'),
      backFromConfigure: t('workspace.connectors.backFromConfigure'),
    },
    onContinueFromAuthorize: async () => {
      await refreshPanelFromServer();
      setPanelActiveTab('configure');
    },
    onBackFromConfigure: handleBackFromConfigure,
    onBackFromAuthorize: handleBackFromAuthorize,
    isOAuthPopupBusy,
  });

  // ── Header ───────────────────────────────────────────────────

  const documentationUrl = getConnectorDocumentationUrl(
    panelConnector,
    connectorSchema != null ? (connectorSchema.documentationLinks ?? []) : undefined
  );

  const headerActions = documentationUrl ? (
    <Button
      variant="outline"
      color="gray"
      size="1"
      aria-label={t('workspace.actions.documentation')}
      onClick={() => {
        window.open(documentationUrl, '_blank', 'noopener,noreferrer');
      }}
      style={{ cursor: 'pointer', gap: 'var(--space-1)' }}
    >
      <MaterialIcon name="open_in_new" size={14} color="var(--gray-11)" />
      <Text size="1">{t('workspace.actions.documentation')}</Text>
    </Button>
  ) : null;

  // ── Render panel icon as img (connector icon) ────────────────

  const panelIcon = panelConnector ? (
    <ConnectorIcon type={panelConnector.type} size={16} />
  ) : undefined;

  return (
    <>
    <WorkspaceRightPanel
      open={isPanelOpen}
      onOpenChange={(open) => {
        if (!open) closePanel();
      }}
      title={t('workspace.connectors.configPanelTitle', { name: connectorTypeName })}
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
          <LottieLoader variant="loader" size={48} showLabel label={t('workspace.connectors.loadingConfig')} />
        </Flex>
      ) : panelView === 'select-records' ? (
        <SelectRecordsPage />
      ) : (
        <Flex direction="column" style={{ height: '100%' }}>
          {/* ── Create mode: Instance name input ── */}
          {isCreateMode && connectorSchema && (
            <Box style={{ marginBottom: 16 }} data-ph-connector-instance-name>
              <FormField
                label={t('workspace.actions.instanceName')}
                required
                error={instanceNameError ?? undefined}
              >
                <input
                  type="text"
                  data-ph-connector-instance-name
                  value={instanceName}
                  onChange={(e) => setInstanceName(e.target.value)}
                  placeholder={t('workspace.actions.instanceNamePlaceholder', { name: connectorTypeName })}
                  aria-invalid={instanceNameError ? true : undefined}
                  style={{
                    height: 32,
                    width: '100%',
                    padding: '6px 8px',
                    backgroundColor: instanceNameError ? 'var(--red-a2)' : 'var(--color-surface)',
                    border: instanceNameError
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
                {t('workspace.connectors.tabs.authenticate')}
              </Tabs.Trigger>
              {showAuthorizeTab ? (
                <Tabs.Trigger value="authorize">Authorize</Tabs.Trigger>
              ) : null}
              <Tabs.Trigger
                value="configure"
                disabled={!configureTabEnabled}
                style={!configureTabEnabled ? { opacity: 0.5, cursor: 'not-allowed' } : undefined}
              >
                {t('workspace.connectors.tabs.configureRecords')}
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
    <ConfirmationDialog
      open={syncSaveConfirmOpen}
      onOpenChange={setSyncSaveConfirmOpen}
      container={connectorPanelNestedModalHost}
      title={t('workspace.connectors.syncSaveConfirm.title')}
      message={
        syncSaveConfirmKind === 'manual'
          ? t('workspace.connectors.syncSaveConfirm.manualMessage')
          : t('workspace.connectors.syncSaveConfirm.wideSyncMessage')
      }
      confirmLabel={t('workspace.connectors.syncSaveConfirm.confirm')}
      cancelLabel={t('workspace.connectors.syncSaveConfirm.cancel')}
      confirmVariant="primary"
      onConfirm={handleConfirmSyncSave}
    />
    </>
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
  hasConnectorId,
  authTypeForConfigureGate,
  instanceAuthenticated,
  isSavingAuth,
  isSavingConfig,
  isLoadingSchema,
  isLoadingConfig,
  onNext,
  onSave,
  labels,
  onContinueFromAuthorize,
  onBackFromConfigure,
  onBackFromAuthorize,
  isOAuthPopupBusy,
}: {
  panelView: string;
  panelActiveTab: PanelTab;
  isAuthReady: boolean;
  hasConnectorId: boolean;
  authTypeForConfigureGate: string;
  instanceAuthenticated: boolean;
  isSavingAuth: boolean;
  isSavingConfig: boolean;
  isLoadingSchema: boolean;
  isLoadingConfig: boolean;
  onNext: () => void;
  onSave: () => void;
  labels: {
    next: string;
    saving: string;
    cancel: string;
    loadingConfig: string;
    saveConfig: string;
    completeAuthForSave: string;
    continueToConfigure: string;
    oauthInProgress: string;
    authBeforeConfigure: string;
    backToAuth: string;
    backFromConfigure: string;
  };
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
    return {
      primaryLabel: `${labels.next} →`,
      /** Validation runs on click; only disable while the save request is in flight. */
      primaryDisabled: isSavingAuth,
      primaryLoading: isSavingAuth,
      primaryTooltip: isSavingAuth ? labels.saving : undefined,
      onPrimary: onNext,
      secondaryLabel: labels.cancel,
    };
  }

  if (panelActiveTab === 'authorize') {
    return {
      primaryLabel: labels.continueToConfigure,
      primaryDisabled: !instanceAuthenticated || isOAuthPopupBusy,
      primaryLoading: isOAuthPopupBusy,
      primaryTooltip: isOAuthPopupBusy
        ? labels.oauthInProgress
        : !instanceAuthenticated
          ? labels.authBeforeConfigure
          : undefined,
      onPrimary: onContinueFromAuthorize,
      secondaryLabel: labels.backToAuth,
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
    ? labels.completeAuthForSave
    : !configureSaveAllowed
    ? labels.authBeforeConfigure
    : isLoadingSchema || isLoadingConfig
    ? labels.loadingConfig
    : undefined;

  return {
    primaryLabel: labels.saveConfig,
    primaryDisabled: !configureSaveAllowed || isSavingConfig || isLoadingSchema || isLoadingConfig,
    primaryLoading: isSavingConfig,
    primaryTooltip: configTooltip,
    onPrimary: onSave,
    secondaryLabel: labels.backFromConfigure,
    onSecondary: () => {
      void onBackFromConfigure();
    },
  };
}
