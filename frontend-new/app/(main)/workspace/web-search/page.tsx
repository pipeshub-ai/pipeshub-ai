'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import { Box, Flex, Text, Heading, Button, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { SettingsSaveBar } from '../components/settings-save-bar';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { useToastStore } from '@/lib/store/toast-store';
import { WebSearchApi } from './api';
import { WebSearchProviderRow, ConfigurePanel, SendImagesRow } from './components';
import {
  WEB_SEARCH_PROVIDER_META,
  ALL_WEB_SEARCH_PROVIDER_TYPES,
  type WebSearchProviderState,
  type WebSearchConfigStatus,
  type ConfigurableProvider,
  type ConfiguredWebSearchProvider,
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

  if (!isProfileInitialized || isAdmin === false) {
    return null;
  }

  // ── State ─────────────────────────────────────────────────
  const [providers, setProviders] = useState<WebSearchProviderState[]>(
    ALL_WEB_SEARCH_PROVIDER_TYPES.map((type) => ({
      type,
      enabled: false,
      isDefault: false,
      providerKey: null,
    })),
  );
  const [savedProviders, setSavedProviders] = useState<WebSearchProviderState[]>(
    ALL_WEB_SEARCH_PROVIDER_TYPES.map((type) => ({
      type,
      enabled: false,
      isDefault: false,
      providerKey: null,
    })),
  );
  const [configStatus, setConfigStatus] = useState<WebSearchConfigStatus>({
    duckduckgo: true,
    serper: false,
    tavily: false,
  });
  const [configuredProviders, setConfiguredProviders] = useState<ConfiguredWebSearchProvider[]>([]);
  const [settings, setSettings] = useState<WebSearchSettings>({
    includeImages: false,
    maxImages: 3,
  });
  const [isEditing, setIsEditing] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  // Configure panel
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelProvider, setPanelProvider] = useState<ConfigurableProvider | null>(null);

  // ── Derived ───────────────────────────────────────────────
  const isDirty = isEditing && JSON.stringify(providers) !== JSON.stringify(savedProviders);

  // ── Data loading ──────────────────────────────────────────
  const loadData = useCallback(async () => {
    setIsLoading(true);
    try {
      const config = await WebSearchApi.getConfig();

      setConfiguredProviders(config.providers);
      setSettings(config.settings);

      const providerMap = new Map(
        config.providers.map((p) => [p.provider, p]),
      );

      const loaded = ALL_WEB_SEARCH_PROVIDER_TYPES.map((type) => {
        const configured = providerMap.get(type);
        return {
          type,
          enabled: !!configured,
          isDefault: configured?.isDefault ?? false,
          providerKey: configured?.providerKey ?? null,
        };
      });

      // DuckDuckGo is always "configured" (no key needed)
      const status: WebSearchConfigStatus = {
        duckduckgo: true,
        serper: !!providerMap.get('serper'),
        tavily: !!providerMap.get('tavily'),
      };

      setProviders(loaded);
      setSavedProviders(loaded);
      setConfigStatus(status);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // ── Handlers ──────────────────────────────────────────────
  const handleToggle = useCallback((type: string) => {
    setProviders((prev) =>
      prev.map((p) =>
        p.type === type
          ? { ...p, enabled: !p.enabled, isDefault: !p.enabled }
          : { ...p, enabled: false, isDefault: false },
      ),
    );
  }, []);

  const handleConfigure = useCallback((provider: ConfigurableProvider) => {
    setPanelProvider(provider);
    setPanelOpen(true);
  }, []);

  const handlePanelClose = useCallback(() => {
    setPanelOpen(false);
    setPanelProvider(null);
  }, []);

  const handleConfigureSaveSuccess = useCallback(
    (provider: ConfigurableProvider) => {
      const meta = WEB_SEARCH_PROVIDER_META.find((m) => m.type === provider);
      const label = meta?.label ?? provider;

      setConfigStatus((prev) => ({ ...prev, [provider]: true }));

      addToast({
        variant: 'success',
        title: t('workspace.webSearch.toasts.providerAdded', { label }),
        description: t('workspace.webSearch.toasts.providerAddedDescription', { label: label.toLowerCase() }),
        duration: 5000,
      });

      loadData();
    },
    [addToast, loadData],
  );

  const handleSave = useCallback(async () => {
    setIsSaving(true);
    try {
      const enabledProviders = providers.filter((p) => p.enabled);

      if (enabledProviders.length > 1) {
        addToast({
          variant: 'error',
          title: t('workspace.webSearch.toasts.oneProviderOnly'),
          duration: 5000,
        });
        setIsSaving(false);
        return;
      }

      const enabledProvider = enabledProviders[0];

      if (enabledProvider) {
        if (enabledProvider.providerKey) {
          await WebSearchApi.setDefaultProvider(enabledProvider.providerKey);
        } else if (enabledProvider.type === 'duckduckgo') {
          await WebSearchApi.addProvider({
            provider: 'duckduckgo',
            configuration: {},
            isDefault: true,
          });
        }
      } else {
        const prevEnabled = savedProviders.find((p) => p.enabled);
        if (prevEnabled?.providerKey) {
          await WebSearchApi.deleteProvider(prevEnabled.providerKey);
        }
      }

      await loadData();
      setIsEditing(false);

      addToast({
        variant: 'success',
        title: t('workspace.webSearch.toasts.saved'),
        description: t('workspace.webSearch.toasts.savedDescription'),
        duration: 4000,
      });
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.webSearch.toasts.saveError'),
        description: t('workspace.webSearch.toasts.saveErrorDescription'),
        duration: 5000,
      });
    } finally {
      setIsSaving(false);
    }
  }, [providers, savedProviders, addToast, loadData]);

  const handleDiscard = useCallback(() => {
    setProviders(savedProviders);
    setIsEditing(false);
  }, [savedProviders]);

  const handleSendImagesToggle = useCallback(
    async (enabled: boolean) => {
      const newSettings = { ...settings, includeImages: enabled };
      setSettings(newSettings);
      try {
        await WebSearchApi.updateSettings(newSettings);
        addToast({
          variant: 'success',
          title: enabled ? t('workspace.webSearch.toasts.imagesEnabled') : t('workspace.webSearch.toasts.imagesDisabled'),
          duration: 3000,
        });
      } catch {
        setSettings(settings);
        addToast({
          variant: 'error',
          title: t('workspace.webSearch.toasts.settingError'),
          duration: 4000,
        });
      }
    },
    [settings, addToast],
  );

  // ── Get existing provider for panel ───────────────────────
  const panelMeta = panelProvider
    ? WEB_SEARCH_PROVIDER_META.find((m) => m.type === panelProvider) ?? null
    : null;
  const existingProvider = panelProvider
    ? configuredProviders.find((p) => p.provider === panelProvider) ?? null
    : null;

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

        {/* ── Web Search Methods section ── */}
        <Flex
          direction="column"
          style={{
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-1)',
            background: 'var(--olive-2)',
            marginBottom: 'var(--space-5)',
          }}
        >
          {/* Section header */}
          <Flex
            align="center"
            justify="between"
            style={{ padding: '14px 16px' }}
          >
            <Box>
              <Text size="3" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
                {t('workspace.webSearch.methodsSection')}
              </Text>
              <Text
                size="1"
                style={{ color: 'var(--slate-10)', display: 'block', marginTop: 2, fontWeight: 300 }}
              >
                {t('workspace.webSearch.methodsSectionDescription')}
              </Text>
            </Box>

            {!isEditing && (
              <Button
                variant="outline"
                color="gray"
                size="2"
                onClick={() => setIsEditing(true)}
                disabled={isLoading}
                style={{ cursor: 'pointer', gap: 6 }}
              >
                <span className="material-icons-outlined" style={{ fontSize: 15 }}>
                  edit
                </span>
                {t('action.edit')}
              </Button>
            )}
          </Flex>
           <Box px="4">
                      <Box style={{ height: 1, background: 'var(--olive-3)' }} />
                    </Box>

          {/* Provider rows */}
          <Flex direction="column" gap="2" style={{ padding: '12px 14px' }}>
              {WEB_SEARCH_PROVIDER_META.map((meta) => {
                const state = providers.find((p) => p.type === meta.type) ?? {
                  type: meta.type,
                  enabled: false,
                  isDefault: false,
                  providerKey: null,
                };
                const anotherProviderEnabled =
                  !state.enabled && providers.some((p) => p.type !== meta.type && p.enabled);

                return (
                  <WebSearchProviderRow
                    key={meta.type}
                    meta={meta}
                    state={state}
                    isEditing={isEditing}
                    configStatus={configStatus}
                    anotherProviderEnabled={anotherProviderEnabled}
                    onToggle={handleToggle}
                    onConfigure={handleConfigure}
                  />
                );
              })}

              <SendImagesRow
                enabled={settings.includeImages}
                onToggle={handleSendImagesToggle}
              />
            </Flex>
        </Flex>

        {/* ── Web Search Provider Policy info box ── */}
        <Flex
          align="center"
          gap="3"
          style={{
            backgroundColor: 'var(--accent-a2)',
            padding: '12px 16px',
          }}
        >
          <IconButton variant="soft" size="2" style={{ flexShrink: 0, cursor: 'default', background: 'var(--slate-a2)' }} tabIndex={-1}>
            <MaterialIcon name="info" size={16} color="var(--accent-11)" />
          </IconButton>
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('workspace.webSearch.policyTitle')}
            </Text>
            <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: '16px', fontWeight: 300 }}>
              {t('workspace.webSearch.policyDescription')}
            </Text>
          </Flex>
        </Flex>
      </Box>

      {/* ── Floating save bar ── */}
      <SettingsSaveBar
        visible={isDirty}
        isSaving={isSaving}
        onDiscard={handleDiscard}
        onSave={handleSave}
        saveLabel={t('action.save')}
      />

      {/* ── Configure side panel ── */}
      <ConfigurePanel
        open={panelOpen}
        provider={panelProvider}
        providerMeta={panelMeta}
        existingProvider={existingProvider}
        onClose={handlePanelClose}
        onSaveSuccess={handleConfigureSaveSuccess}
      />
    </Box>
    </ServiceGate>
  );
}
