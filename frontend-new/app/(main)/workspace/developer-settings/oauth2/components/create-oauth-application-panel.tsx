'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Box,
  Button,
  Checkbox,
  Flex,
  IconButton,
  Tabs,
  Text,
  TextArea,
  TextField,
} from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { CheckedState } from '@radix-ui/react-checkbox';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FormField, WorkspaceRightPanel } from '../../../components';
import { useToastStore } from '@/lib/store/toast-store';
import { Oauth2Api } from '../api';
import type {
  OAuthGrantTypeValue,
  OAuthScopeItem,
} from '../types';
import {
  isValidHttpUrl,
  isValidOAuthRedirectUri,
} from '../redirect-uri-validation';

// ========================================
// Constants
// ========================================

const GRANT_OPTIONS: { value: OAuthGrantTypeValue; labelKey: string }[] = [
  {
    value: 'authorization_code',
    labelKey: 'workspace.oauth2.create.grantAuthorizationCode',
  },
  {
    value: 'refresh_token',
    labelKey: 'workspace.oauth2.create.grantRefreshToken',
  },
  {
    value: 'client_credentials',
    labelKey: 'workspace.oauth2.create.grantClientCredentials',
  },
];

const INPUT_STYLE: React.CSSProperties = {
  width: '100%',
  minHeight: 32,
  padding: '6px 8px',
  backgroundColor: 'var(--color-surface)',
  border: '1px solid var(--slate-a5)',
  borderRadius: 'var(--radius-2)',
  fontSize: 14,
  lineHeight: '20px',
  fontFamily: 'var(--default-font-family)',
  color: 'var(--slate-12)',
  outline: 'none',
  boxSizing: 'border-box',
};

const CARD_STYLE: React.CSSProperties = {
  backgroundColor: 'var(--olive-2)',
  border: '1px solid var(--olive-3)',
  borderRadius: 'var(--radius-2)',
  padding: 'var(--space-4)',
};

const MAX_REDIRECT_URIS = 10;

const CREDENTIAL_MONO: React.CSSProperties = {
  fontFamily:
    'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
};

// ========================================
// Helpers
// ========================================

function optionalUrlOrEmpty(s: string): string | undefined {
  const t = s.trim();
  if (!t) return undefined;
  return t;
}

// ========================================
// Props
// ========================================

export interface CreateOAuthApplicationPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: () => void;
  /** After create success, user can open Manage for the new app (Mongo id). */
  onOpenManage?: (applicationId: string) => void;
}

// ========================================
// Component
// ========================================

export function CreateOAuthApplicationPanel({
  open,
  onOpenChange,
  onCreated,
  onOpenManage,
}: CreateOAuthApplicationPanelProps) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);

  const [activeTab, setActiveTab] = useState<'general' | 'scopes'>('general');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [grantTypes, setGrantTypes] = useState<Set<OAuthGrantTypeValue>>(
    () =>
      new Set<OAuthGrantTypeValue>(['authorization_code', 'refresh_token'])
  );
  const grantTypesRef = useRef(grantTypes);
  grantTypesRef.current = grantTypes;

  const [redirectUris, setRedirectUris] = useState<string[]>(['']);
  const [homepageUrl, setHomepageUrl] = useState('');
  const [privacyPolicyUrl, setPrivacyPolicyUrl] = useState('');
  const [termsUrl, setTermsUrl] = useState('');

  const [generalErrors, setGeneralErrors] = useState<{
    name?: string;
    redirectUris?: string;
    grants?: string;
    optionalUrls?: string;
  }>({});

  const [scopesByCategory, setScopesByCategory] = useState<Record<
    string,
    OAuthScopeItem[]
  > | null>(null);
  const [scopesLoading, setScopesLoading] = useState(false);
  const [scopesError, setScopesError] = useState<string | null>(null);
  const [selectedScopes, setSelectedScopes] = useState<Set<string>>(
    () => new Set()
  );

  const [isCreating, setIsCreating] = useState(false);

  const [createdCredentials, setCreatedCredentials] = useState<{
    applicationId: string;
    clientId: string;
    clientSecret: string | null;
  } | null>(null);
  const [showClientSecret, setShowClientSecret] = useState(false);

  const usesAuthorizationCode = grantTypes.has('authorization_code');

  const sortedCategories = useMemo(() => {
    if (!scopesByCategory) return [] as [string, OAuthScopeItem[]][];
    return Object.entries(scopesByCategory).sort(([a], [b]) =>
      a.localeCompare(b)
    );
  }, [scopesByCategory]);

  const allScopeNames = useMemo(() => {
    return sortedCategories.flatMap(([, items]) => items.map((i) => i.name));
  }, [sortedCategories]);

  const totalScopeCount = allScopeNames.length;
  const selectedScopeCount = useMemo(() => {
    return allScopeNames.filter((n) => selectedScopes.has(n)).length;
  }, [allScopeNames, selectedScopes]);

  const allScopesSelected =
    totalScopeCount > 0 && selectedScopeCount === totalScopeCount;

  useEffect(() => {
    if (!open) {
      setActiveTab('general');
      setName('');
      setDescription('');
      setGrantTypes(
        new Set<OAuthGrantTypeValue>(['authorization_code', 'refresh_token'])
      );
      setRedirectUris(['']);
      setHomepageUrl('');
      setPrivacyPolicyUrl('');
      setTermsUrl('');
      setGeneralErrors({});
      setScopesByCategory(null);
      setScopesError(null);
      setSelectedScopes(new Set());
      setIsCreating(false);
      setCreatedCredentials(null);
      setShowClientSecret(false);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setScopesLoading(true);
    setScopesError(null);

    const loadScopes = async () => {
      try {
        const data = await Oauth2Api.getScopes();
        if (!cancelled) setScopesByCategory(data.scopes ?? {});
      } catch {
        if (!cancelled) {
          setScopesError(t('workspace.oauth2.create.scopesLoadError'));
        }
      } finally {
        if (!cancelled) setScopesLoading(false);
      }
    };

    void loadScopes();

    return () => {
      cancelled = true;
    };
  }, [open, t]);

  useEffect(() => {
    if (usesAuthorizationCode) return;
    setGeneralErrors((prev) => {
      if (!prev.redirectUris) return prev;
      const next = { ...prev };
      delete next.redirectUris;
      return next;
    });
  }, [usesAuthorizationCode]);

  const handleClose = useCallback(() => {
    onOpenChange(false);
  }, [onOpenChange]);

  const copyToClipboard = useCallback(
    async (text: string) => {
      try {
        await navigator.clipboard.writeText(text);
        addToast({
          variant: 'success',
          title: t('workspace.oauth2.manageApplication.copySuccessTitle'),
          description: t(
            'workspace.oauth2.manageApplication.copySuccessDescription'
          ),
          duration: 2500,
        });
      } catch {
        addToast({
          variant: 'error',
          title: t('workspace.oauth2.manageApplication.copyErrorTitle'),
          duration: 4000,
        });
      }
    },
    [addToast, t]
  );

  const toggleGrant = useCallback(
    (value: OAuthGrantTypeValue, checked: boolean) => {
      if (checked) {
        setGrantTypes((prev) => new Set(prev).add(value));
        return;
      }
      // Read latest grants in the event handler ΓÇö never call addToast inside setState updaters.
      if (grantTypesRef.current.size <= 1) {
        addToast({
          variant: 'error',
          title: t('workspace.oauth2.create.grantRequiredTitle'),
          description: t(
            'workspace.oauth2.create.grantRequiredDescription'
          ),
          duration: 4000,
        });
        return;
      }
      setGrantTypes((prev) => {
        const next = new Set(prev);
        next.delete(value);
        return next;
      });
    },
    [addToast, t]
  );

  const validateGeneral = useCallback((): boolean => {
    const errors: typeof generalErrors = {};
    if (!name.trim()) {
      errors.name = t('workspace.oauth2.create.errorNameRequired');
    }

    if (usesAuthorizationCode) {
      const uris = redirectUris.map((u) => u.trim()).filter(Boolean);
      if (uris.length === 0) {
        errors.redirectUris = t('workspace.oauth2.create.errorRedirectRequired');
      } else {
        const bad = uris.some((u) => !isValidOAuthRedirectUri(u));
        if (bad) {
          errors.redirectUris = t('workspace.oauth2.create.errorRedirectInvalid');
        }
      }
    }

    if (grantTypes.size === 0) {
      errors.grants = t('workspace.oauth2.create.errorGrantRequired');
    }

    const optFields = [
      { v: homepageUrl, key: 'home' as const },
      { v: privacyPolicyUrl, key: 'privacy' as const },
      { v: termsUrl, key: 'terms' as const },
    ];
    for (const { v } of optFields) {
      const o = optionalUrlOrEmpty(v);
      if (o && !isValidHttpUrl(o)) {
        errors.optionalUrls = t('workspace.oauth2.create.errorOptionalUrlInvalid');
        break;
      }
    }

    setGeneralErrors(errors);
    return Object.keys(errors).length === 0;
  }, [
    name,
    redirectUris,
    grantTypes,
    usesAuthorizationCode,
    homepageUrl,
    privacyPolicyUrl,
    termsUrl,
    t,
  ]);

  const buildPayload = useCallback(() => {
    const redirectUrisClean = usesAuthorizationCode
      ? redirectUris.map((u) => u.trim()).filter(Boolean)
      : [];
    return {
      name: name.trim(),
      description: description.trim() || undefined,
      redirectUris: redirectUrisClean,
      allowedGrantTypes: Array.from(grantTypes) as OAuthGrantTypeValue[],
      allowedScopes: Array.from(selectedScopes),
      homepageUrl: optionalUrlOrEmpty(homepageUrl),
      privacyPolicyUrl: optionalUrlOrEmpty(privacyPolicyUrl),
      termsOfServiceUrl: optionalUrlOrEmpty(termsUrl),
      isConfidential: true as const,
    };
  }, [
    name,
    description,
    redirectUris,
    grantTypes,
    selectedScopes,
    homepageUrl,
    privacyPolicyUrl,
    termsUrl,
    usesAuthorizationCode,
  ]);

  const handleNext = useCallback(() => {
    if (!validateGeneral()) {
      return;
    }
    setActiveTab('scopes');
  }, [validateGeneral]);

  const handleCreate = useCallback(async () => {
    if (!validateGeneral()) {
      setActiveTab('general');
      return;
    }
    if (totalScopeCount > 0 && selectedScopeCount === 0) {
      return;
    }
    setIsCreating(true);
    try {
      const res = await Oauth2Api.createOAuthClient(buildPayload());
      const app = res?.app;
      if (!app?.id || !app.clientId) {
        addToast({
          variant: 'error',
          title: t('workspace.oauth2.create.errorCreateTitle'),
          duration: 5000,
        });
        return;
      }
      setShowClientSecret(false);
      setCreatedCredentials({
        applicationId: app.id,
        clientId: app.clientId,
        clientSecret:
          typeof app.clientSecret === 'string' && app.clientSecret.length > 0
            ? app.clientSecret
            : null,
      });
      onCreated?.();
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.oauth2.create.errorCreateTitle'),
        duration: 5000,
      });
    } finally {
      setIsCreating(false);
    }
  }, [validateGeneral, buildPayload, addToast, t, onCreated, totalScopeCount, selectedScopeCount]);

  const onPrimaryClick = useCallback(() => {
    if (createdCredentials) {
      handleClose();
      return;
    }
    if (activeTab === 'general') {
      handleNext();
    } else {
      void handleCreate();
    }
  }, [createdCredentials, activeTab, handleNext, handleCreate, handleClose]);

  const onSecondaryPanelClick = useCallback(() => {
    if (createdCredentials) {
      onOpenManage?.(createdCredentials.applicationId);
      handleClose();
      return;
    }
    handleClose();
  }, [createdCredentials, onOpenManage, handleClose]);

  const trimmedRedirectUris = useMemo(
    () => redirectUris.map((u) => u.trim()).filter(Boolean),
    [redirectUris]
  );
  const hasValidRedirect = trimmedRedirectUris.some((u) =>
    isValidOAuthRedirectUri(u)
  );

  const primaryDisabledGeneral =
    !name.trim() ||
    grantTypes.size === 0 ||
    (usesAuthorizationCode && !hasValidRedirect);

  const primaryTooltipGeneral = primaryDisabledGeneral
    ? t('workspace.oauth2.create.nextDisabledTooltip')
    : undefined;

  const primaryDisabledScopes =
    scopesLoading ||
    (totalScopeCount > 0 && selectedScopeCount === 0);

  const primaryTooltipScopes = primaryDisabledScopes
    ? scopesLoading
      ? undefined
      : t('workspace.oauth2.create.createDisabledScopesTooltip')
    : undefined;

  const documentationAction = (
    <Button
      variant="outline"
      color="gray"
      size="1"
      style={{ cursor: 'pointer', gap: 4 }}
      onClick={() =>
        window.open('https://docs.pipeshub.com/developer/oauth2', '_blank')
      }
    >
      <MaterialIcon name="open_in_new" size={14} color="var(--slate-11)" />
      {t('workspace.oauth2.create.documentation')}
    </Button>
  );

  const toggleScope = useCallback((scopeName: string, checked: boolean) => {
    setSelectedScopes((prev) => {
      const next = new Set(prev);
      if (checked) next.add(scopeName);
      else next.delete(scopeName);
      return next;
    });
  }, []);

  const toggleCategory = useCallback(
    (items: OAuthScopeItem[], checked: CheckedState) => {
      setSelectedScopes((prev) => {
        const next = new Set(prev);
        if (checked === true) {
          items.forEach((i) => next.add(i.name));
        } else {
          items.forEach((i) => next.delete(i.name));
        }
        return next;
      });
    },
    []
  );

  const toggleSelectAllScopes = useCallback(() => {
    if (allScopesSelected) {
      setSelectedScopes(new Set());
    } else {
      setSelectedScopes(new Set(allScopeNames));
    }
  }, [allScopesSelected, allScopeNames]);

  return (
    <WorkspaceRightPanel
      open={open}
      onOpenChange={onOpenChange}
      title={
        createdCredentials
          ? t('workspace.oauth2.create.successPanelTitle')
          : t('workspace.oauth2.create.panelTitle')
      }
      icon={<MaterialIcon name="vpn_key" size={20} color="var(--slate-12)" />}
      headerActions={createdCredentials ? undefined : documentationAction}
      primaryLabel={
        createdCredentials
          ? t('workspace.oauth2.create.returnToAppsButton')
          : activeTab === 'general'
            ? t('workspace.oauth2.create.next')
            : t('workspace.oauth2.create.createApp')
      }
      secondaryLabel={
        createdCredentials
          ? t('workspace.oauth2.manage')
          : t('workspace.oauth2.create.cancel')
      }
      primaryDisabled={
        createdCredentials
          ? false
          : activeTab === 'general'
            ? primaryDisabledGeneral
            : primaryDisabledScopes
      }
      primaryLoading={createdCredentials ? false : isCreating}
      primaryTooltip={
        createdCredentials
          ? undefined
          : activeTab === 'general'
            ? primaryTooltipGeneral
            : primaryTooltipScopes
      }
      onPrimaryClick={onPrimaryClick}
      onSecondaryClick={onSecondaryPanelClick}
    >
      {createdCredentials ? (
        <Box style={CARD_STYLE}>
          <Flex direction="column" gap="4">
            <Flex align="start" gap="3">
              <Box
                style={{
                  width: 40,
                  height: 40,
                  borderRadius: 'var(--radius-2)',
                  backgroundColor: 'var(--accent-9)',
                  flexShrink: 0,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <MaterialIcon name="check" size={22} color="white" />
              </Box>
              <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
                <Text
                  size="4"
                  weight="bold"
                  style={{ color: 'var(--slate-12)' }}
                >
                  {t('workspace.oauth2.create.successScreenTitle')}
                </Text>
                <Text size="2" style={{ color: 'var(--slate-11)' }}>
                  {t('workspace.oauth2.create.successScreenSubtitle')}
                </Text>
              </Flex>
            </Flex>

            <Flex direction="column" gap="2">
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {t('workspace.oauth2.create.clientIdLabel')}
              </Text>
              <Flex align="center" gap="2" style={{ width: '100%' }}>
                <TextField.Root
                  size="2"
                  readOnly
                  value={createdCredentials.clientId}
                  style={{ flex: 1, minWidth: 0, ...CREDENTIAL_MONO }}
                />
                <IconButton
                  type="button"
                  variant="soft"
                  color="gray"
                  size="2"
                  aria-label={t('workspace.oauth2.manageApplication.copyClientIdAria')}
                  onClick={() =>
                    void copyToClipboard(createdCredentials.clientId)
                  }
                  style={{ flexShrink: 0, cursor: 'pointer' }}
                >
                  <MaterialIcon
                    name="content_copy"
                    size={18}
                    color="var(--slate-11)"
                  />
                </IconButton>
              </Flex>
            </Flex>

            {createdCredentials.clientSecret ? (
              <Flex direction="column" gap="2">
                <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                  {t('workspace.oauth2.create.clientSecretLabel')}
                </Text>
                <Flex align="center" gap="2" style={{ width: '100%' }}>
                  <TextField.Root
                    size="2"
                    readOnly
                    type={showClientSecret ? 'text' : 'password'}
                    value={createdCredentials.clientSecret}
                    style={{ flex: 1, minWidth: 0, ...CREDENTIAL_MONO }}
                  />
                  <IconButton
                    type="button"
                    variant="soft"
                    color="gray"
                    size="2"
                    aria-label={
                      showClientSecret
                        ? t('workspace.oauth2.create.hideSecretAria')
                        : t('workspace.oauth2.create.showSecretAria')
                    }
                    onClick={() => setShowClientSecret((v) => !v)}
                    style={{ flexShrink: 0, cursor: 'pointer' }}
                  >
                    <MaterialIcon
                      name={showClientSecret ? 'visibility_off' : 'visibility'}
                      size={18}
                      color="var(--slate-11)"
                    />
                  </IconButton>
                  <IconButton
                    type="button"
                    variant="soft"
                    color="gray"
                    size="2"
                    aria-label={t('workspace.oauth2.manageApplication.copySecretAria')}
                    onClick={() =>
                      void copyToClipboard(createdCredentials.clientSecret!)
                    }
                    style={{ flexShrink: 0, cursor: 'pointer' }}
                  >
                    <MaterialIcon
                      name="content_copy"
                      size={18}
                      color="var(--slate-11)"
                    />
                  </IconButton>
                </Flex>
              </Flex>
            ) : null}
          </Flex>
        </Box>
      ) : (
        <Tabs.Root
          value={activeTab}
          onValueChange={(v) => setActiveTab(v as 'general' | 'scopes')}
        >
          <Tabs.List
            style={{
              borderBottom: '1px solid var(--olive-3)',
              marginBottom: 'var(--space-3)',
            }}
          >
            <Tabs.Trigger value="general">
              {t('workspace.oauth2.create.tabGeneral')}
            </Tabs.Trigger>
            <Tabs.Trigger value="scopes">
              {t('workspace.oauth2.create.tabPermissions')}
            </Tabs.Trigger>
          </Tabs.List>

          <Tabs.Content value="general">
            <Flex direction="column" gap="4">
              <Box style={CARD_STYLE}>
                <FormField
                  label={t('workspace.oauth2.create.appNameLabel')}
                  required
                  error={generalErrors.name}
                >
                  <TextField.Root
                    size="2"
                    placeholder={t('workspace.oauth2.create.appNamePlaceholder')}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    style={{ width: '100%' }}
                  />
                </FormField>
                <Box style={{ marginTop: 'var(--space-3)' }}>
                  <FormField
                    label={t('workspace.oauth2.create.descriptionLabel')}
                    optional
                  >
                    <TextArea
                      size="2"
                      rows={2}
                      placeholder={t(
                        'workspace.oauth2.create.descriptionPlaceholder'
                      )}
                      value={description}
                      onChange={(e) => setDescription(e.target.value)}
                      style={{ width: '100%', minHeight: 64 }}
                    />
                  </FormField>
                </Box>
              </Box>

              <Box style={CARD_STYLE}>
                <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                  {t('workspace.oauth2.create.grantTypesHeading')}
                  <Text
                    as="span"
                    weight="medium"
                    style={{ color: 'var(--red-a11)', marginLeft: 2 }}
                    aria-hidden
                  >
                    *
                  </Text>
                </Text>
                {generalErrors.grants && (
                  <Text
                    size="1"
                    style={{ color: 'var(--red-a11)', marginTop: 4 }}
                  >
                    {generalErrors.grants}
                  </Text>
                )}
                <Flex wrap="wrap" gap="3" style={{ marginTop: 'var(--space-2)' }}>
                  {GRANT_OPTIONS.map((opt) => (
                    <Flex key={opt.value} align="center" gap="2">
                      <Checkbox
                        checked={grantTypes.has(opt.value)}
                        onCheckedChange={(c) =>
                          toggleGrant(opt.value, Boolean(c))
                        }
                      />
                      <Text size="2" style={{ color: 'var(--slate-12)' }}>
                        {t(opt.labelKey)}
                      </Text>
                    </Flex>
                  ))}
                </Flex>
              </Box>

              {usesAuthorizationCode && (
                <Box style={CARD_STYLE}>
                  <Flex direction="column" gap="1">
                    <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                      {t('workspace.oauth2.create.redirectHeading')}
                      <Text
                        as="span"
                        weight="medium"
                        style={{ color: 'var(--red-a11)', marginLeft: 2 }}
                        aria-hidden
                      >
                        *
                      </Text>
                    </Text>
                    <Text size="1" style={{ color: 'var(--slate-11)' }}>
                      {t('workspace.oauth2.create.redirectHelper')}
                    </Text>
                  </Flex>
                  {generalErrors.redirectUris && (
                    <Text
                      size="1"
                      style={{ color: 'var(--red-a11)', marginTop: 4 }}
                    >
                      {generalErrors.redirectUris}
                    </Text>
                  )}
                  <Flex direction="column" gap="2" style={{ marginTop: 'var(--space-3)' }}>
                    {redirectUris.map((uri, index) => (
                      <Flex key={index} align="center" gap="2">
                        <TextField.Root
                          size="2"
                          placeholder={t(
                            'workspace.oauth2.create.redirectPlaceholder'
                          )}
                          value={uri}
                          onChange={(e) => {
                            const next = [...redirectUris];
                            next[index] = e.target.value;
                            setRedirectUris(next);
                          }}
                          style={{ flex: 1, minWidth: 0 }}
                        />
                        <IconButton
                          type="button"
                          variant="ghost"
                          color="gray"
                          size="2"
                          disabled={redirectUris.length <= 1}
                          aria-label={t('workspace.oauth2.create.removeRedirectAria')}
                          onClick={() => {
                            if (redirectUris.length <= 1) return;
                            setRedirectUris(redirectUris.filter((_, i) => i !== index));
                          }}
                          style={{ flexShrink: 0 }}
                        >
                          <MaterialIcon name="close" size={18} color="var(--slate-11)" />
                        </IconButton>
                      </Flex>
                    ))}
                  </Flex>
                  {redirectUris.length >= MAX_REDIRECT_URIS ? (
                    <Text
                      size="1"
                      style={{ color: 'var(--amber-11)', marginTop: 'var(--space-2)' }}
                    >
                      {t('workspace.oauth2.create.redirectUriLimitNote')}
                    </Text>
                  ) : (
                    <Button
                      type="button"
                      variant="ghost"
                      size="2"
                      style={{ marginTop: 'var(--space-2)', cursor: 'pointer' }}
                      onClick={() => setRedirectUris([...redirectUris, ''])}
                    >
                      <MaterialIcon name="add" size={16} color="var(--accent-11)" />
                      {t('workspace.oauth2.create.addRedirectUri')}
                    </Button>
                  )}
                </Box>
              )}

              <Box style={CARD_STYLE}>
                <Flex direction="column" gap="3">
                  {generalErrors.optionalUrls && (
                    <Text size="1" style={{ color: 'var(--red-a11)' }}>
                      {generalErrors.optionalUrls}
                    </Text>
                  )}
                  <FormField
                    label={t('workspace.oauth2.create.homepageLabel')}
                    optional
                  >
                    <TextField.Root
                      type="url"
                      placeholder={t('workspace.oauth2.create.urlPlaceholder')}
                      value={homepageUrl}
                      onChange={(e) => setHomepageUrl(e.target.value)}
                      style={INPUT_STYLE}
                    />
                  </FormField>
                  <FormField
                    label={t('workspace.oauth2.create.privacyLabel')}
                    optional
                  >
                    <TextField.Root
                      type="url"
                      value={privacyPolicyUrl}
                      onChange={(e) => setPrivacyPolicyUrl(e.target.value)}
                      placeholder={t('workspace.oauth2.create.urlPlaceholder')}
                      style={INPUT_STYLE}
                    />
                  </FormField>
                  <FormField
                    label={t('workspace.oauth2.create.termsLabel')}
                    optional
                  >
                    <TextField.Root
                      type="url"
                      value={termsUrl}
                      onChange={(e) => setTermsUrl(e.target.value)}
                      placeholder={t('workspace.oauth2.create.urlPlaceholder')}
                      style={INPUT_STYLE}
                    />
                  </FormField>
                </Flex>
              </Box>
            </Flex>
          </Tabs.Content>

          <Tabs.Content value="scopes">
            <Flex direction="column" gap="3">
              <Flex align="start" justify="between" gap="3" wrap="wrap">
                <Flex direction="column" gap="1" style={{ minWidth: 0, flex: 1 }}>
                  <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
                    {t('workspace.oauth2.create.permissionsHeading')}
                  </Text>
                  <Text size="2" style={{ color: 'var(--slate-11)' }}>
                    {t('workspace.oauth2.create.permissionsSummary', {
                      selected: selectedScopeCount,
                      total: totalScopeCount,
                    })}
                  </Text>
                  {totalScopeCount > 0 && selectedScopeCount === 0 && (
                    <Text size="2" style={{ color: 'var(--amber-11)', marginTop: 4 }}>
                      {t('workspace.oauth2.create.errorScopeRequired')}
                    </Text>
                  )}
                </Flex>
                <Button
                  type="button"
                  variant="outline"
                  color="gray"
                  size="2"
                  disabled={totalScopeCount === 0 || scopesLoading}
                  onClick={toggleSelectAllScopes}
                  style={{ flexShrink: 0, cursor: 'pointer' }}
                >
                  {allScopesSelected
                    ? t('workspace.oauth2.create.clearAllScopes')
                    : t('workspace.oauth2.create.selectAllScopes')}
                </Button>
              </Flex>

              {scopesLoading && (
                <Text size="2" style={{ color: 'var(--slate-11)' }}>
                  {t('workspace.oauth2.create.scopesLoading')}
                </Text>
              )}

              {scopesError && !scopesLoading && (
                <Flex direction="column" gap="2" align="start">
                  <Text size="2" style={{ color: 'var(--red-11)' }}>
                    {scopesError}
                  </Text>
                  <Button
                    size="2"
                    variant="soft"
                    onClick={() => {
                      setScopesError(null);
                      setScopesLoading(true);
                      void Oauth2Api.getScopes()
                        .then((data) => setScopesByCategory(data.scopes ?? {}))
                        .catch(() =>
                          setScopesError(t('workspace.oauth2.create.scopesLoadError'))
                        )
                        .finally(() => setScopesLoading(false));
                    }}
                  >
                    {t('workspace.oauth2.create.scopesRetry')}
                  </Button>
                </Flex>
              )}

              {!scopesLoading &&
                !scopesError &&
                sortedCategories.map(([category, items]) => {
                  const selectedInCat = items.filter((i) =>
                    selectedScopes.has(i.name)
                  ).length;
                  const totalInCat = items.length;
                  const groupChecked: CheckedState =
                    selectedInCat === totalInCat
                      ? true
                      : selectedInCat === 0
                        ? false
                        : 'indeterminate';

                  return (
                    <Box
                      key={category}
                      style={{
                        ...CARD_STYLE,
                        padding: 'var(--space-3)',
                      }}
                    >
                      <Flex align="center" justify="between" gap="3" wrap="wrap">
                        <Flex align="center" gap="2" style={{ minWidth: 0 }}>
                          <Checkbox
                            checked={groupChecked}
                            onCheckedChange={(c) =>
                              toggleCategory(items, c as CheckedState)
                            }
                          />
                          <Text
                            size="2"
                            weight="medium"
                            style={{ color: 'var(--slate-12)' }}
                          >
                            {category}
                          </Text>
                        </Flex>
                        <Text size="2" style={{ color: 'var(--slate-11)' }}>
                          {selectedInCat}/{totalInCat}
                        </Text>
                      </Flex>
                      <Flex direction="column" gap="2" style={{ marginTop: 'var(--space-3)' }}>
                        {items.map((scope) => (
                          <Flex
                            key={scope.name}
                            align="start"
                            gap="2"
                            style={{
                              padding: 'var(--space-2)',
                              borderRadius: 'var(--radius-2)',
                              border: '1px solid var(--olive-4)',
                            }}
                          >
                            <Checkbox
                              checked={selectedScopes.has(scope.name)}
                              onCheckedChange={(c) =>
                                toggleScope(scope.name, Boolean(c))
                              }
                              style={{ marginTop: 2 }}
                            />
                            <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
                              <Text
                                size="2"
                                weight="medium"
                                style={{
                                  fontFamily:
                                    'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                                  color: 'var(--slate-12)',
                                }}
                              >
                                {scope.name}
                              </Text>
                              <Text size="2" style={{ color: 'var(--slate-11)' }}>
                                {scope.description}
                              </Text>
                              {scope.requiresUserConsent && (
                                <Text size="1" style={{ color: 'var(--slate-10)' }}>
                                  {t('workspace.oauth2.create.requiresConsent')}
                                </Text>
                              )}
                            </Flex>
                          </Flex>
                        ))}
                      </Flex>
                    </Box>
                  );
                })}
            </Flex>
          </Tabs.Content>
        </Tabs.Root>
      )}
    </WorkspaceRightPanel>
  );
}
