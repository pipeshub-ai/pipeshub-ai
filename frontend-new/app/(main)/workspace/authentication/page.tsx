'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Flex,
  Text,
  Heading,
  Button,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { SettingsSaveBar } from '../components/settings-save-bar';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { AuthMethodRow } from './components/auth-method-row';
import { ConfigurePanel } from './components/configure-panel';
import { useToastStore } from '@/lib/store/toast-store';
import {
  AuthMethodsApi,
  AuthConfigApi,
  SmtpApi,
} from './api';
import {
  AUTH_METHOD_META,
  ALL_AUTH_METHOD_TYPES,
  type AuthMethodState,
  type ConfigurableMethod,
  type ConfigStatus,
} from './types';

// ============================================================
// Page
// ============================================================

export default function AuthenticationPage() {
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
  const [methods, setMethods] = useState<AuthMethodState[]>(
    ALL_AUTH_METHOD_TYPES.map((type) => ({ type, enabled: false })),
  );
  /** Snapshot used to revert on Discard */
  const [savedMethods, setSavedMethods] = useState<AuthMethodState[]>(
    ALL_AUTH_METHOD_TYPES.map((type) => ({ type, enabled: false })),
  );
  const [configStatus, setConfigStatus] = useState<ConfigStatus>({
    google: false,
    microsoft: false,
    samlSso: false,
    oauth: false,
  });
  const [smtpConfigured, setSmtpConfigured] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  // Configure panel
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelMethod, setPanelMethod] = useState<ConfigurableMethod | null>(null);

  // ── Derived ───────────────────────────────────────────────
  const isDirty = isEditing && JSON.stringify(methods) !== JSON.stringify(savedMethods);

  // ── Data loading ──────────────────────────────────────────
  const loadData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [authResp, smtpOk, googleCfg, microsoftCfg, samlCfg, oauthCfg] =
        await Promise.allSettled([
          AuthMethodsApi.getAuthMethods(),
          SmtpApi.checkSmtpConfigured(),
          AuthConfigApi.getGoogleConfig(),
          AuthConfigApi.getMicrosoftConfig(),
          AuthConfigApi.getSamlConfig(),
          AuthConfigApi.getOAuthConfig(),
        ]);

      // Parse enabled methods
      if (authResp.status === 'fulfilled') {
        const enabledTypes = new Set<string>();
        authResp.value.authMethods.forEach((step) =>
          step.allowedMethods.forEach((m) => enabledTypes.add(m.type)),
        );
        const loaded = ALL_AUTH_METHOD_TYPES.map((type) => ({
          type,
          enabled: enabledTypes.has(type),
        }));
        setMethods(loaded);
        setSavedMethods(loaded);
      }

      // SMTP
      if (smtpOk.status === 'fulfilled') {
        setSmtpConfigured(smtpOk.value);
      }

      // Config statuses
      const google =
        googleCfg.status === 'fulfilled' && !!(googleCfg.value?.clientId);
      const microsoft =
        microsoftCfg.status === 'fulfilled' &&
        !!(microsoftCfg.value?.clientId) &&
        !!(microsoftCfg.value?.tenantId);
      const samlSso =
        samlCfg.status === 'fulfilled' &&
        !!(samlCfg.value?.entryPoint) &&
        !!(samlCfg.value?.certificate) &&
        !!(samlCfg.value?.emailKey);
      const oauth =
        oauthCfg.status === 'fulfilled' &&
        !!(oauthCfg.value?.clientId) &&
        !!(oauthCfg.value?.providerName);

      setConfigStatus({ google, microsoft, samlSso, oauth });
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isProfileInitialized || isAdmin === false) return;
    void loadData();
  }, [isProfileInitialized, isAdmin, loadData]);

  // ── Handlers ──────────────────────────────────────────────
  const handleToggle = useCallback((type: string) => {
    setMethods((prev) =>
      prev.map((m) => (m.type === type ? { ...m, enabled: !m.enabled } : m)),
    );
  }, []);

  const handleConfigure = useCallback((method: ConfigurableMethod) => {
    setPanelMethod(method);
    setPanelOpen(true);
  }, []);

  const handlePanelClose = useCallback(() => {
    setPanelOpen(false);
    setPanelMethod(null);
  }, []);

  const handleConfigureSaveSuccess = useCallback(
    (method: ConfigurableMethod) => {
      const methodMeta = AUTH_METHOD_META.find((m) => m.type === method);
      const label = methodMeta?.label ?? method;

      // Mark as configured in UI
      setConfigStatus((prev) => ({ ...prev, [method]: true }));

      addToast({
        variant: 'success',
        title: t('workspace.authentication.toasts.configureSuccess', { label }),
        description: t('workspace.authentication.toasts.configureSuccessDescription', { label }),
        duration: 5000,
      });
    },
    [addToast],
  );

  const handleSave = useCallback(async () => {
    setIsSaving(true);
    try {
      const enabledMethods = methods.filter((m) => m.enabled);

      // Validate: only one method at a time
      if (enabledMethods.length > 1) {
        addToast({
          variant: 'error',
          title: t('workspace.authentication.toasts.onlyOneActive'),
          duration: 5000,
        });
        setIsSaving(false);
        return;
      }

      await AuthMethodsApi.updateAuthMethods({
        authMethod: [
          {
            order: 1,
            allowedMethods: enabledMethods.map(({ type }) => ({ type })),
          },
        ],
      });

      setSavedMethods(methods);
      setIsEditing(false);

      addToast({
        variant: 'success',
        title: t('workspace.authentication.toasts.saveSuccess'),
        description: t('workspace.authentication.toasts.saveSuccessDescription'),
        duration: 4000,
      });
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.authentication.toasts.saveError'),
        description: t('message.tryAgain'),
        duration: 5000,
      });
    } finally {
      setIsSaving(false);
    }
  }, [methods, addToast]);

  const handleDiscard = useCallback(() => {
    setMethods(savedMethods);
    setIsEditing(false);
  }, [savedMethods]);

  // Prevent rendering while profile is unresolved or for non-admin users
  // (redirect for non-admins is handled in the effect above).
  if (!isProfileInitialized || isAdmin === false) {
    return null;
  }

  // ── Loading state ─────────────────────────────────────────
  if (isLoading) {
    return (
      <Flex align="center" justify="center" style={{ height: '100%', width: '100%' }}>
        <LottieLoader variant="loader" size={48} showLabel label={t('workspace.authentication.loading')} />
      </Flex>
    );
  }

  // ── Render ────────────────────────────────────────────────
  return (
    <Box style={{ height: '100%', overflowY: 'auto', position: 'relative' }}>
      <Box style={{ padding: '64px 100px 80px' }}>
        {/* ── Page header ── */}
        <Flex align="start" justify="between" style={{ marginBottom: 24 }}>
          <Box>
            <Heading size="6" style={{ color: 'var(--slate-12)' }}>
              {t('workspace.authentication.title')}
            </Heading>
            <Text size="2" style={{ color: 'var(--slate-10)', marginTop: 4, display: 'block' }}>
              {t('workspace.authentication.subtitle')}
            </Text>
          </Box>

          <Button
            variant="outline"
            color="gray"
            size="2"
            onClick={() =>
              window.open(
                'https://docs.pipeshub.com/workspace/authentication',
                '_blank',
              )
            }
            style={{ cursor: 'pointer', flexShrink: 0, gap: 6 }}
          >
            <span className="material-icons-outlined" style={{ fontSize: 15 }}>
              open_in_new
            </span>
            {t('workspaceMenu.documentation')}
          </Button>
        </Flex>

        {/* ── Authentication Methods section ── */}
        <Flex
          direction="column"
          style={{
            border: '1px solid var(--slate-5)',
            borderRadius: 'var(--radius-2)',
            backgroundColor: 'var(--slate-2)',
            marginBottom: 20,
          }}
        >
          {/* Section header */}
          <Flex
            align="center"
            justify="between"
            style={{ padding: '14px 16px', borderBottom: '1px solid var(--slate-5)' }}
          >
            <Box>
              <Text size="3" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
                {t('workspace.authentication.methodsHeading')}
              </Text>
              <Text
                size="1"
                style={{ color: 'var(--slate-10)', display: 'block', marginTop: 2, fontWeight: 300 }}
              >
                {t('workspace.authentication.methodsSubtitle')}
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
                {t('workspace.aiModels.actionEdit')}
              </Button>
            )}
          </Flex>

          {/* Method rows */}
          <Flex direction="column" gap="2" style={{ padding: '12px 14px' }}>
              {AUTH_METHOD_META.map((meta) => {
                const state = methods.find((m) => m.type === meta.type) ?? {
                  type: meta.type,
                  enabled: false,
                };
                const anotherMethodEnabled =
                  !state.enabled && methods.some((m) => m.type !== meta.type && m.enabled);

                return (
                  <AuthMethodRow
                    key={meta.type}
                    meta={meta}
                    state={state}
                    isEditing={isEditing}
                    configStatus={configStatus}
                    smtpConfigured={smtpConfigured}
                    anotherMethodEnabled={anotherMethodEnabled}
                    onToggle={handleToggle}
                    onConfigure={handleConfigure}
                  />
                );
              })}
            </Flex>
        </Flex>

        {/* ── Authentication Method Policy info box ── */}
        <Flex
          align="start"
          gap="3"
          style={{
            backgroundColor: 'var(--accent-2)',
            border: '1px solid var(--accent-6)',
            borderRadius: 'var(--radius-1)',
            padding: '12px 16px',
          }}
        >
          <Box style={{ flexShrink: 0, marginTop: 2 }}>
            <MaterialIcon name="info" size={16} color="var(--accent-9)" />
          </Box>
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('workspace.authentication.policyHeading')}
            </Text>
            <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: '16px', fontWeight: 300 }}>
              {t('workspace.authentication.policyDescription')}
            </Text>
            {!smtpConfigured && (
              <Text
                size="1"
                style={{ color: 'var(--amber-11)', display: 'block', marginTop: 4 }}
              >
                {t('workspace.authentication.smtpWarning')}
              </Text>
            )}
          </Flex>
        </Flex>
      </Box>

      {/* ── Floating save bar ── */}
      <SettingsSaveBar
        visible={isDirty}
        isSaving={isSaving}
        onDiscard={handleDiscard}
        onSave={handleSave}
        saveLabel="Save"
      />

      {/* ── Configure side panel ── */}
      <ConfigurePanel
        open={panelOpen}
        method={panelMethod}
        onClose={handlePanelClose}
        onSaveSuccess={handleConfigureSaveSuccess}
      />
    </Box>
  );
}
