'use client';

import React, { useEffect, useCallback } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { Flex, Text, Tabs, Box, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon } from '@/app/components/ui';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { WorkspaceRightPanel } from '@/app/(main)/workspace/components/workspace-right-panel';
import { FormField } from '@/app/(main)/workspace/components/form-field';
import { AuthenticateTab } from './authenticate-tab';
import { ConfigureTab } from './configure-tab';
import { SelectRecordsPage } from './select-records-page';
import { useConnectorsStore } from '../store';
import { ConnectorsApi } from '../api';
import { isNoneAuthType } from '../utils/auth-helpers';
import { trimConnectorConfig } from '../utils/trim-config';
import { resolveAuthFields } from './authenticate-tab/helpers';
import type { PanelTab } from '../types';

// ========================================
// Component
// ========================================

export function ConnectorPanel() {
  const router = useRouter();
  const pathname = usePathname();
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
  } = useConnectorsStore();

  const isCreateMode = !panelConnectorId;
  const isLoading = isLoadingSchema || isLoadingConfig;
  const connectorName = panelConnector?.name ?? '';
  const connectorType = panelConnector?.type ?? '';
  // Use registry connector's display name so the panel always shows the type name
  // (e.g. "Pipeshub docs") rather than an instance name when creating a new connector.
  const connectorTypeName = registryConnectors.find((c) => c.type === connectorType)?.name ?? connectorName;

  // ── Fetch schema + config on panel open ──────────────────────
  useEffect(() => {
    if (!isPanelOpen || !connectorType) return;

    const fetchData = async () => {
      try {
        setIsLoadingSchema(true);
        setSchemaError(null);

        if (isCreateMode) {
          // Create mode: fetch schema only
          const schemaRes = await ConnectorsApi.getConnectorSchema(connectorType);
          setSchemaAndConfig(schemaRes.schema);
        } else {
          // Edit mode: fetch both schema and config in parallel
          setIsLoadingConfig(true);
          const [schemaRes, configRes] = await Promise.all([
            ConnectorsApi.getConnectorSchema(connectorType),
            ConnectorsApi.getConnectorConfig(panelConnectorId!),
          ]);
          setSchemaAndConfig(schemaRes.schema, configRes);
          setIsLoadingConfig(false);
        }
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : 'Failed to load connector configuration';
        setSchemaError(message);
      } finally {
        setIsLoadingSchema(false);
      }
    };

    fetchData();
  }, [isPanelOpen, connectorType, isCreateMode, panelConnectorId]);

  // ── Save handlers ────────────────────────────────────────────

  const handleSaveAuth = useCallback(async () => {
    if (isCreateMode) {
      // Create mode: POST /connectors
      if (!instanceName.trim()) {
        setInstanceNameError('Instance name is required');
        return;
      }

      try {
        setIsSavingAuth(true);
        setSaveError(null);

        const result = await ConnectorsApi.createConnectorInstance({
          connectorType,
          instanceName: instanceName.trim(),
          scope: useConnectorsStore.getState().selectedScope,
          authType: selectedAuthType,
          config: { auth: trimConnectorConfig(formData.auth) },
          baseUrl: window.location.origin,
        });

        // After creation, we have a connectorId — transition to edit mode
        // Update the store with the new connector ID
        useConnectorsStore.setState({
          panelConnectorId: result._key || result.connectorId,
          isAuthTypeImmutable: true,
        });

        // If NONE auth type, mark as authenticated
        if (isNoneAuthType(selectedAuthType)) {
          setAuthState('success');
        }

        // Move to configure tab
        setPanelActiveTab('configure');
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

        await ConnectorsApi.saveAuthConfig(panelConnectorId!, {
          auth: trimConnectorConfig(formData.auth),
          baseUrl: window.location.origin,
        });

        // Move to configure tab
        setPanelActiveTab('configure');
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
      const scope = pathname.includes('/personal/') ? 'personal' : 'team';

      // Close the configuration panel
      closePanel();

      // Navigate to connector type page with connectorType query param
      // and trigger the success dialog
      if (savedConnectorType) {
        setNewlyConfiguredConnectorId(currentConnectorId);
        setShowConfigSuccessDialog(true);
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
  }, [panelConnectorId, formData, closePanel, connectorType, pathname, router, setShowConfigSuccessDialog, setNewlyConfiguredConnectorId]);

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

  const footerConfig = getFooterConfig({
    panelView,
    panelActiveTab,
    isAuthReady,
    areRequiredAuthFieldsFilled,
    hasConnectorId: !!panelConnectorId,
    isSavingAuth,
    isSavingConfig,
    isLoadingSchema,
    isLoadingConfig,
    onNext: handleSaveAuth,
    onBack: () => setPanelActiveTab('authenticate'),
    onSave: handleSaveConfig,
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
          <LottieLoader variant="loader" size={48} />
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
              // Only allow switching to configure tab if connector has server-side config
              if (tab === 'configure' && !connectorConfig) return;
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
              <Tabs.Trigger
                value="configure"
                disabled={!connectorConfig}
                style={!connectorConfig ? { opacity: 0.5, cursor: 'not-allowed' } : undefined}
              >
                Configure Records
              </Tabs.Trigger>
            </Tabs.List>

            <Box style={{ paddingTop: 16 }}>
              <Tabs.Content value="authenticate">
                <AuthenticateTab />
              </Tabs.Content>
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
  hasConnectorId,
  isSavingAuth,
  isSavingConfig,
  isLoadingSchema,
  isLoadingConfig,
  onNext,
  onBack,
  onSave,
}: {
  panelView: string;
  panelActiveTab: PanelTab;
  isAuthReady: boolean;
  areRequiredAuthFieldsFilled: boolean;
  hasConnectorId: boolean;
  isSavingAuth: boolean;
  isSavingConfig: boolean;
  isLoadingSchema: boolean;
  isLoadingConfig: boolean;
  onNext: () => void;
  onBack: () => void;
  onSave: () => void;
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
      primaryLabel: 'Next →',
      primaryDisabled: !areRequiredAuthFieldsFilled || isSavingAuth,
      primaryLoading: isSavingAuth,
      primaryTooltip: !areRequiredAuthFieldsFilled
        ? 'Fill in all required fields to continue'
        : undefined,
      onPrimary: onNext,
      secondaryLabel: 'Cancel',
    };
  }

  // configure tab
  const configTooltip = !hasConnectorId
    ? 'Complete authentication first to save configuration'
    : isLoadingSchema || isLoadingConfig
    ? 'Loading configuration…'
    : undefined;

  return {
    primaryLabel: 'Save Configuration',
    primaryDisabled: !hasConnectorId || isSavingConfig || isLoadingSchema || isLoadingConfig,
    primaryLoading: isSavingConfig,
    primaryTooltip: configTooltip,
    onPrimary: onSave,
    secondaryLabel: '← Back',
    onSecondary: onBack,
  };
}
