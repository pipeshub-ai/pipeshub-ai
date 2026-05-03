'use client';

import { useTranslation } from 'react-i18next';
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { Box, Flex, Text, Heading, Button, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { SettingsSaveBar } from '../components/settings-save-bar';
import { ConfirmationDialog } from '../components/confirmation-dialog';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { useToastStore } from '@/lib/store/toast-store';
import { WebSearchApi } from './api';
import { WebSearchProviderRow, ConfigurePanel, SendImagesRow } from './components';
import {
  DUCKDUCKGO_PROVIDER_ID,
  WEB_SEARCH_PROVIDER_META,
  type ConfigurableProvider,
  type ConfiguredWebSearchProvider,
  type WebSearchProviderAgentUsage,
  type WebSearchProviderType,
  type WebSearchSettings,
} from './types';

// ============================================================
// Page
// ============================================================

export default function WebSearchPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const addToast = useToastStore((s) => s.addToast);
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) {
      router.replace('/workspace/general');
    }
  }, [isProfileInitialized, isAdmin, router]);

  // ── State ─────────────────────────────────────────────────
  const [configuredProviders, setConfiguredProviders] = useState<ConfiguredWebSearchProvider[]>([]);
  const [settings, setSettings] = useState<WebSearchSettings>({
    includeImages: false,
    maxImages: 3,
  });
  const [isLoading, setIsLoading] = useState(true);
  const [settingDefaultType, setSettingDefaultType] = useState<WebSearchProviderType | null>(null);
  const [isSettingsSaving, setIsSettingsSaving] = useState(false);

  // Configure panel
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelProvider, setPanelProvider] = useState<ConfigurableProvider | null>(null);

  // Delete (from row) dialog
  const [deleteTarget, setDeleteTarget] = useState<ConfiguredWebSearchProvider | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteUsageAgents, setDeleteUsageAgents] = useState<WebSearchProviderAgentUsage[]>([]);
  const [isCheckingUsage, setIsCheckingUsage] = useState(false);

  const anyActionInProgress = isLoading || settingDefaultType !== null || isDeleting;

  // ── Derived ───────────────────────────────────────────────
  const providerMap = useMemo(() => {
    const map = new Map<string, ConfiguredWebSearchProvider>();
    for (const p of configuredProviders) map.set(p.provider, p);
    return map;
  }, [configuredProviders]);

  const defaultProviderType: WebSearchProviderType = useMemo(() => {
    const explicit = configuredProviders.find((p) => p.isDefault);
    if (explicit) return explicit.provider as WebSearchProviderType;
    return DUCKDUCKGO_PROVIDER_ID;
  }, [configuredProviders]);

  // ── Data loading ──────────────────────────────────────────
  const loadData = useCallback(
    async (showLoading = true) => {
      if (showLoading) setIsLoading(true);
      try {
        const config = await WebSearchApi.getConfig();
        setConfiguredProviders(config.providers);
        setSettings(config.settings);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Please try again.';
        addToast({
          variant: 'error',
          title: 'Failed to load web search settings',
          description: message,
          duration: 5000,
        });
      } finally {
        if (showLoading) setIsLoading(false);
      }
    },
    [addToast],
  );

  useEffect(() => {
    loadData();
  }, [loadData]);

  // ── Handlers: provider actions ────────────────────────────
  const handleConfigure = useCallback((provider: ConfigurableProvider) => {
    setPanelProvider(provider);
    setPanelOpen(true);
  }, []);

  const handlePanelClose = useCallback(() => {
    setPanelOpen(false);
    setPanelProvider(null);
  }, []);

  const handleConfigureSaveSuccess = useCallback(
    ({ provider, isEdit }: { provider: ConfigurableProvider; isEdit: boolean }) => {
      const meta = WEB_SEARCH_PROVIDER_META.find((m) => m.type === provider);
      const label = meta?.label ?? provider;
      addToast({
        variant: 'success',
        title: isEdit ? `${label} updated` : `${label} added`,
        description: isEdit
          ? 'Configuration saved successfully.'
          : `${label} is now configured. Click "Set as default" to start using it.`,
        duration: 5000,
      });
      loadData(false);
    },
    [addToast, loadData],
  );

  const handleConfigureDeleteSuccess = useCallback(
    async ({ provider, wasDefault }: { provider: ConfigurableProvider; wasDefault: boolean }) => {
      const meta = WEB_SEARCH_PROVIDER_META.find((m) => m.type === provider);
      const label = meta?.label ?? provider;
      try {
        if (wasDefault) {
          await WebSearchApi.setDefaultProvider(DUCKDUCKGO_PROVIDER_ID);
        }
        addToast({
          variant: 'success',
          title: `${label} removed`,
          description: wasDefault
            ? 'DuckDuckGo is now the default web search provider.'
            : 'Configuration deleted successfully.',
          duration: 4000,
        });
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Please try again.';
        addToast({
          variant: 'error',
          title: 'Failed to reset default provider',
          description: message,
          duration: 5000,
        });
      } finally {
        loadData(false);
      }
    },
    [addToast, loadData],
  );

  const handleSetDefault = useCallback(
    async (type: WebSearchProviderType) => {
      setSettingDefaultType(type);
      try {
        const meta = WEB_SEARCH_PROVIDER_META.find((m) => m.type === type);
        const label = meta?.label ?? type;

        const configured = providerMap.get(type);
        if (configured) {
          await WebSearchApi.setDefaultProvider(configured.providerKey);
        } else if (type === DUCKDUCKGO_PROVIDER_ID) {
          await WebSearchApi.setDefaultProvider(DUCKDUCKGO_PROVIDER_ID);
        } else {
          addToast({
            variant: 'error',
            title: `Configure ${label} first`,
            duration: 4000,
          });
          return;
        }

        addToast({
          variant: 'success',
          title: `${label} is now the default`,
          duration: 3000,
        });
        await loadData(false);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Please try again.';
        addToast({
          variant: 'error',
          title: 'Failed to set default provider',
          description: message,
          duration: 5000,
        });
      } finally {
        setSettingDefaultType(null);
      }
    },
    [providerMap, addToast, loadData],
  );

  const handleRowDelete = useCallback(async (configured: ConfiguredWebSearchProvider) => {
    setDeleteTarget(configured);
    setDeleteUsageAgents([]);
    setIsCheckingUsage(true);
    try {
      const agents = await WebSearchApi.getProviderUsage(configured.provider);
      setDeleteUsageAgents(agents);
    } finally {
      setIsCheckingUsage(false);
    }
  }, []);

  const handleDeleteConfirm = useCallback(async () => {
    if (!deleteTarget) return;
    const type = deleteTarget.provider as WebSearchProviderType;
    const meta = WEB_SEARCH_PROVIDER_META.find((m) => m.type === type);
    const label = meta?.label ?? type;
    const wasDefault = deleteTarget.isDefault;

    setIsDeleting(true);
    try {
      await WebSearchApi.deleteProvider(deleteTarget.providerKey);
      if (wasDefault) {
        await WebSearchApi.setDefaultProvider(DUCKDUCKGO_PROVIDER_ID);
      }
      addToast({
        variant: 'success',
        title: `${label} removed`,
        description: wasDefault
          ? 'DuckDuckGo is now the default web search provider.'
          : 'Configuration deleted successfully.',
        duration: 4000,
      });
      setDeleteTarget(null);
      setDeleteUsageAgents([]);
      await loadData(false);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Please try again.';
      addToast({
        variant: 'error',
        title: 'Failed to delete provider',
        description: message,
        duration: 5000,
      });
    } finally {
      setIsDeleting(false);
    }
  }, [deleteTarget, addToast, loadData]);

  // ── Handlers: settings (send images) ──────────────────────
  const saveSettings = useCallback(
    async (next: WebSearchSettings, successMessage: string) => {
      setIsSettingsSaving(true);
      const previous = settings;
      setSettings(next);
      try {
        const updated = await WebSearchApi.updateSettings(next);
        setSettings(updated);
        addToast({
          variant: 'success',
          title: successMessage,
          duration: 3000,
        });
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Please try again.';
        setSettings(previous);
        addToast({
          variant: 'error',
          title: 'Failed to update image settings',
          description: message,
          duration: 5000,
        });
      } finally {
        setIsSettingsSaving(false);
      }
    },
    [settings, addToast],
  );

  const handleSendImagesToggle = useCallback(
    (enabled: boolean) => {
      const next = { ...settings, includeImages: enabled };
      const message = enabled ? 'Images will be sent to LLM' : 'Images will not be sent to LLM';
      saveSettings(next, message);
    },
    [settings, saveSettings],
  );

  const handleMaxImagesCommit = useCallback(
    (maxImages: number) => {
      const next = { ...settings, maxImages };
      saveSettings(next, `Max images updated to ${maxImages}`);
    },
    [settings, saveSettings],
  );

  // ── Panel data ────────────────────────────────────────────
  const panelMeta = panelProvider
    ? WEB_SEARCH_PROVIDER_META.find((m) => m.type === panelProvider) ?? null
    : null;
  const existingPanelProvider = panelProvider ? providerMap.get(panelProvider) ?? null : null;

  // ── Guard ─────────────────────────────────────────────────
  if (!isProfileInitialized || isAdmin === false) {
    return null;
  }

  // ── Loading state ─────────────────────────────────────────
  if (isLoading) {
    return (
      <Flex align="center" justify="center" style={{ height: '100%', width: '100%' }}>
        <LottieLoader variant="loader" size={48} showLabel label={t('workspace.webSearch.loading')} />
      </Flex>
    );
  }

  // ── Render ────────────────────────────────────────────────
  return (
    <ServiceGate services={['query']}>
    <Box style={{ height: '100%', overflowY: 'auto', position: 'relative' }}>
      <Box style={{ padding: '64px 100px 80px' }}>
        {/* ── Page header ── */}
        <Flex align="start" justify="between" style={{ marginBottom: 'var(--space-6)' }}>
          <Box>
            <Heading size="6" style={{ color: 'var(--slate-12)' }}>
              {t('workspace.webSearch.heading')}
            </Heading>
            <Text size="2" style={{ color: 'var(--slate-10)', marginTop: 'var(--space-1)', display: 'block' }}>
             {t('workspace.webSearch.subtitle')}
            </Text>
          </Box>

          <Button
            variant="outline"
            color="gray"
            size="2"
            onClick={() =>
              window.open('https://docs.pipeshub.com/workspace/web-search', '_blank')
            }
            style={{ cursor: 'pointer', flexShrink: 0, gap: 6 }}
          >
            <span className="material-icons-outlined" style={{ fontSize: 15 }}>
              open_in_new
            </span>
            {t('workspace.bots.documentation')}
          </Button>
        </Flex>

        {/* ── Providers section ── */}
        <Flex
          direction="column"
          style={{
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-1)',
            background: 'var(--olive-2)',
            marginBottom: 'var(--space-5)',
          }}
        >
          <Flex
            align="center"
            justify="between"
            style={{ padding: '14px 16px' }}
          >
            <Box>
              <Text size="3" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
                Web Search Providers
              </Text>
              <Text
                size="1"
                style={{ color: 'var(--slate-10)', display: 'block', marginTop: 2, fontWeight: 300 }}
              >
                Configure any providers you want, then pick one as the default
              </Text>
            </Box>
          </Flex>
           <Box px="4">
                      <Box style={{ height: 1, background: 'var(--olive-3)' }} />
                    </Box>

          {isLoading ? (
            <Flex align="center" justify="center" style={{ padding: '40px 0' }}>
              <Text size="2" style={{ color: 'var(--slate-10)' }}>
                Loading web search settings…
              </Text>
            </Flex>
          ) : (
            <Flex direction="column" gap="2" style={{ padding: '12px 14px' }}>
              {WEB_SEARCH_PROVIDER_META.map((meta) => {
                const configured = providerMap.get(meta.type) ?? null;
                const isDefault = defaultProviderType === meta.type;
                return (
                  <WebSearchProviderRow
                    key={meta.type}
                    meta={meta}
                    configured={configured}
                    isDefault={isDefault}
                    isSettingDefault={settingDefaultType === meta.type}
                    anyActionInProgress={anyActionInProgress}
                    onSetDefault={() => handleSetDefault(meta.type)}
                    onConfigure={handleConfigure}
                    onDelete={() => configured && handleRowDelete(configured)}
                  />
                );
              })}
            </Flex>
          )}
        </Flex>

        {/* ── Image Settings section ── */}
        <Flex
          direction="column"
          style={{
            border: '1px solid var(--slate-5)',
            borderRadius: 'var(--radius-2)',
            backgroundColor: 'var(--slate-2)',
            marginBottom: 20,
          }}
        >
          <Flex
            align="center"
            justify="between"
            style={{ padding: '14px 16px', borderBottom: '1px solid var(--slate-5)' }}
          >
            <Box>
              <Text size="3" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
                Image Settings
              </Text>
              <Text
                size="1"
                style={{ color: 'var(--slate-10)', display: 'block', marginTop: 2, fontWeight: 300 }}
              >
                Control whether images are sent to the LLM during web search
              </Text>
            </Box>
          </Flex>

          <Flex direction="column" gap="2" style={{ padding: '12px 14px' }}>
            <SendImagesRow
              enabled={settings.includeImages}
              maxImages={settings.maxImages}
              onToggle={handleSendImagesToggle}
              onMaxImagesCommit={handleMaxImagesCommit}
              disabled={isLoading || isSettingsSaving}
            />
          </Flex>
        </Flex>
      </Box>

      {/* ── Configure side panel ── */}
      <ConfigurePanel
        open={panelOpen}
        provider={panelProvider}
        providerMeta={panelMeta}
        existingProvider={existingPanelProvider}
        onClose={handlePanelClose}
        onSaveSuccess={handleConfigureSaveSuccess}
        onDeleteSuccess={handleConfigureDeleteSuccess}
      />

      {/* ── Row-level delete confirmation ── */}
      <ConfirmationDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteTarget(null);
            setDeleteUsageAgents([]);
          }
        }}
        title={
          deleteTarget
            ? !isCheckingUsage && deleteUsageAgents.length > 0
              ? `Cannot delete ${
                  WEB_SEARCH_PROVIDER_META.find((m) => m.type === deleteTarget.provider)?.label ??
                  deleteTarget.provider
                }`
              : `Delete ${
                  WEB_SEARCH_PROVIDER_META.find((m) => m.type === deleteTarget.provider)?.label ??
                  deleteTarget.provider
                } configuration?`
            : 'Delete provider?'
        }
        message={
          <Flex direction="column" gap="2">
            {isCheckingUsage && (
              <Text size="2" style={{ color: 'var(--slate-10)', fontStyle: 'italic' }}>
                Checking agent usage…
              </Text>
            )}
            {!isCheckingUsage && deleteUsageAgents.length > 0 && (
              <Flex
                direction="column"
                gap="1"
                style={{
                  padding: '10px 12px',
                  borderRadius: 'var(--radius-2)',
                  border: '1px solid var(--red-5)',
                  backgroundColor: 'var(--red-2)',
                }}
              >
                <Text size="1" weight="medium" style={{ color: 'var(--red-11)' }}>
                  This provider is currently used by {deleteUsageAgents.length}{' '}
                  {deleteUsageAgents.length === 1 ? 'agent' : 'agents'}:
                </Text>
                <Flex direction="column" gap="1" style={{ paddingLeft: 4 }}>
                  {deleteUsageAgents.map((agent) => (
                    <Text key={agent._key} size="1" style={{ color: 'var(--slate-12)' }}>
                      • <Text weight="medium" size="1">{agent.name}</Text>
                      {agent.creatorName && (
                        <Text size="1" style={{ color: 'var(--slate-10)' }}>
                          {' '}(created by {agent.creatorName})
                        </Text>
                      )}
                    </Text>
                  ))}
                </Flex>
                <Text size="1" style={{ color: 'var(--red-11)', marginTop: 2 }}>
                  Please remove the web search configuration from these agents before deleting this provider.
                </Text>
              </Flex>
            )}
            {!isCheckingUsage && deleteUsageAgents.length === 0 && (
              <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
                {deleteTarget?.isDefault
                  ? 'This provider is currently the default. Deleting it will reset the default to DuckDuckGo.'
                  : 'This will permanently remove the provider configuration. You can reconfigure it later.'}
              </Text>
            )}
          </Flex>
        }
        hideConfirm={isCheckingUsage || deleteUsageAgents.length > 0}
        confirmLabel="Delete"
        confirmLoadingLabel="Deleting..."
        confirmVariant="danger"
        isLoading={isDeleting}
        onConfirm={handleDeleteConfirm}
      />
    </Box>
    </ServiceGate>
  );
}
