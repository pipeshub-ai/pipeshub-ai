'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Badge,
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
import { LoadingButton } from '@/app/components/ui/loading-button';
import {
  DestructiveTypedConfirmationDialog,
  FormField,
  WorkspaceRightPanel,
} from '../../../components';
import { useToastStore } from '@/lib/store/toast-store';
import { Oauth2Api } from '../api';
import type { OAuthClient, OAuthGrantTypeValue, OAuthScopeItem } from '../types';
import {
  isValidHttpUrl,
  isValidOAuthRedirectUri,
} from '../redirect-uri-validation';

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

const VALID_GRANTS = new Set<string>([
  'authorization_code',
  'refresh_token',
  'client_credentials',
]);

const CARD_STYLE: React.CSSProperties = {
  backgroundColor: 'var(--olive-2)',
  border: '1px solid var(--olive-3)',
  borderRadius: 'var(--radius-2)',
  padding: 'var(--space-4)',
};

const MAX_REDIRECT_URIS = 10;


function optionalUrlOrEmpty(s: string): string | undefined {
  const t = s.trim();
  if (!t) return undefined;
  return t;
}

function oauthUrlFromDetail(raw: string | null | undefined): string {
  return raw?.trim() ?? '';
}

function tokenLifetimesFromClient(d: OAuthClient): {
  accessTokenLifetime: number;
  refreshTokenLifetime: number;
} {
  return {
    accessTokenLifetime:
      typeof d.accessTokenLifetime === 'number' &&
        Number.isFinite(d.accessTokenLifetime)
        ? d.accessTokenLifetime
        : 3600,
    refreshTokenLifetime:
      typeof d.refreshTokenLifetime === 'number' &&
        Number.isFinite(d.refreshTokenLifetime)
        ? d.refreshTokenLifetime
        : 2_592_000,
  };
}

function parseGrantTypesFromDetail(
  raw: string[] | undefined
): Set<OAuthGrantTypeValue> {
  const picked = (raw ?? []).filter((g): g is OAuthGrantTypeValue =>
    VALID_GRANTS.has(g)
  );
  if (picked.length > 0) {
    return new Set(picked);
  }
  return new Set<OAuthGrantTypeValue>(['authorization_code', 'refresh_token']);
}

interface FormSnapshot {
  name: string;
  description: string;
  grantsKey: string;
  redirectKey: string;
  scopesKey: string;
  homepageUrl: string;
  privacyPolicyUrl: string;
  termsOfServiceUrl: string;
}

function buildSnapshot(params: {
  name: string;
  description: string;
  grantTypes: Set<OAuthGrantTypeValue>;
  redirectUris: string[];
  selectedScopes: Set<string>;
  homepageUrl: string;
  privacyPolicyUrl: string;
  termsOfServiceUrl: string;
}): FormSnapshot {
  const useAuthCode = params.grantTypes.has('authorization_code');
  const redirectForKey = useAuthCode
    ? params.redirectUris.map((u) => u.trim()).filter(Boolean)
    : [];
  return {
    name: params.name.trim(),
    description: params.description.trim(),
    grantsKey: Array.from(params.grantTypes).sort().join(','),
    redirectKey: JSON.stringify(redirectForKey),
    scopesKey: Array.from(params.selectedScopes).sort().join(','),
    homepageUrl: params.homepageUrl.trim(),
    privacyPolicyUrl: params.privacyPolicyUrl.trim(),
    termsOfServiceUrl: params.termsOfServiceUrl.trim(),
  };
}

function snapshotsEqual(a: FormSnapshot, b: FormSnapshot): boolean {
  return (
    a.name === b.name &&
    a.description === b.description &&
    a.grantsKey === b.grantsKey &&
    a.redirectKey === b.redirectKey &&
    a.scopesKey === b.scopesKey &&
    a.homepageUrl === b.homepageUrl &&
    a.privacyPolicyUrl === b.privacyPolicyUrl &&
    a.termsOfServiceUrl === b.termsOfServiceUrl
  );
}

export interface ManageOAuthApplicationPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Mongo-style application id for API routes */
  clientId: string | null;
  onSaved?: () => void;
}

function ReadonlyCopyField({
  value,
  copyLabel,
  onCopy,
}: {
  value: string;
  copyLabel: string;
  onCopy: () => void;
}) {
  return (
    <Flex align="center" gap="2" style={{ width: '100%' }}>
      <TextField.Root
        size="2"
        readOnly
        value={value}
        style={{ flex: 1, minWidth: 0 }}
      />
      <IconButton
        type="button"
        variant="soft"
        color="gray"
        size="2"
        aria-label={copyLabel}
        onClick={onCopy}
        style={{ flexShrink: 0, cursor: 'pointer' }}
      >
        <MaterialIcon name="content_copy" size={18} color="var(--slate-11)" />
      </IconButton>
    </Flex>
  );
}

export function ManageOAuthApplicationPanel({
  open,
  onOpenChange,
  clientId,
  onSaved,
}: ManageOAuthApplicationPanelProps) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);

  const [activeTab, setActiveTab] = useState<'general' | 'scopes' | 'advanced'>(
    'general'
  );
  const [detail, setDetail] = useState<OAuthClient | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [baseline, setBaseline] = useState<FormSnapshot | null>(null);

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [grantTypes, setGrantTypes] = useState<Set<OAuthGrantTypeValue>>(
    () => new Set()
  );
  const grantTypesRef = useRef(grantTypes);
  grantTypesRef.current = grantTypes;

  const usesAuthorizationCode = grantTypes.has('authorization_code');

  const [redirectUris, setRedirectUris] = useState<string[]>(['']);
  const [homepageUrl, setHomepageUrl] = useState('');
  const [privacyPolicyUrl, setPrivacyPolicyUrl] = useState('');
  const [termsOfServiceUrl, setTermsOfServiceUrl] = useState('');

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

  const [newClientSecret, setNewClientSecret] = useState<string | null>(null);
  const [regeneratingSecret, setRegeneratingSecret] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  const [revokeDialogOpen, setRevokeDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [suspendDialogOpen, setSuspendDialogOpen] = useState(false);
  const [isRevokingTokens, setIsRevokingTokens] = useState(false);
  const [isDeletingApp, setIsDeletingApp] = useState(false);
  const [statusActionLoading, setStatusActionLoading] = useState(false);

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

  const oauthAppDisplayName = useMemo(() => {
    const n = detail?.name?.trim();
    return n && n.length > 0
      ? n
      : t('workspace.oauth2.manageApplication.typedConfirmFallbackName');
  }, [detail?.name, t]);

  const typedConfirmKeywordFromAppName = useMemo(() => {
    const n = detail?.name?.trim();
    return n && n.length > 0 ? n : null;
  }, [detail?.name]);

  const revokeConfirmationKeyword = typedConfirmKeywordFromAppName ?? 'REVOKE';
  const deleteConfirmationKeyword = typedConfirmKeywordFromAppName ?? 'DELETE';
  const OAUTH_SUSPEND_CONFIRMATION_KEYWORD = typedConfirmKeywordFromAppName ?? 'SUSPEND';


  const applyDetailToForm = useCallback((d: OAuthClient) => {
    setName(d.name ?? '');
    setDescription(String(d.description ?? ''));
    setGrantTypes(parseGrantTypesFromDetail(d.allowedGrantTypes));
    const uris = d.redirectUris?.length ? [...d.redirectUris] : [''];
    setRedirectUris(uris);
    setSelectedScopes(new Set(d.allowedScopes ?? []));
    setHomepageUrl(oauthUrlFromDetail(d.homepageUrl));
    setPrivacyPolicyUrl(oauthUrlFromDetail(d.privacyPolicyUrl));
    setTermsOfServiceUrl(oauthUrlFromDetail(d.termsOfServiceUrl));
  }, []);

  useEffect(() => {
    if (!open) {
      setActiveTab('general');
      setDetail(null);
      setBaseline(null);
      setDetailError(null);
      setDetailLoading(false);
      setName('');
      setDescription('');
      setGrantTypes(new Set());
      setRedirectUris(['']);
      setHomepageUrl('');
      setPrivacyPolicyUrl('');
      setTermsOfServiceUrl('');
      setGeneralErrors({});
      setScopesByCategory(null);
      setScopesError(null);
      setSelectedScopes(new Set());
      setNewClientSecret(null);
      setRegeneratingSecret(false);
      setIsSaving(false);
      setRevokeDialogOpen(false);
      setDeleteDialogOpen(false);
      setSuspendDialogOpen(false);
      setIsRevokingTokens(false);
      setIsDeletingApp(false);
      setStatusActionLoading(false);
      return;
    }

    if (!clientId) {
      return;
    }

    let cancelled = false;
    setDetailLoading(true);
    setDetailError(null);
    setNewClientSecret(null);

    void (async () => {
      try {
        const [d, scopesData] = await Promise.all([
          Oauth2Api.getOAuthClient(clientId),
          Oauth2Api.getScopes(),
        ]);
        if (cancelled) return;
        setDetail(d);
        applyDetailToForm(d);
        setBaseline(
          buildSnapshot({
            name: d.name ?? '',
            description:
              typeof d.description === 'string'
                ? d.description
                : d.description ?? '',
            grantTypes: parseGrantTypesFromDetail(d.allowedGrantTypes),
            redirectUris: d.redirectUris?.length ? [...d.redirectUris] : [''],
            selectedScopes: new Set(d.allowedScopes ?? []),
            homepageUrl: oauthUrlFromDetail(d.homepageUrl),
            privacyPolicyUrl: oauthUrlFromDetail(d.privacyPolicyUrl),
            termsOfServiceUrl: oauthUrlFromDetail(d.termsOfServiceUrl),
          })
        );
        setScopesByCategory(scopesData.scopes ?? {});
        setScopesError(null);
      } catch {
        if (!cancelled) {
          setDetailError(t('workspace.oauth2.manageApplication.loadError'));
          setDetail(null);
          setBaseline(null);
        }
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [open, clientId, applyDetailToForm, t]);

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
          description: t('workspace.oauth2.manageApplication.copySuccessDescription'),
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

  const currentSnapshot = useMemo(
    () =>
      buildSnapshot({
        name,
        description,
        grantTypes,
        redirectUris,
        selectedScopes,
        homepageUrl,
        privacyPolicyUrl,
        termsOfServiceUrl,
      }),
    [
      name,
      description,
      grantTypes,
      redirectUris,
      selectedScopes,
      homepageUrl,
      privacyPolicyUrl,
      termsOfServiceUrl,
    ]
  );

  const isDirty =
    baseline !== null && !snapshotsEqual(currentSnapshot, baseline);

  const toggleGrant = useCallback(
    (value: OAuthGrantTypeValue, checked: boolean) => {
      if (checked) {
        setGrantTypes((prev) => new Set(prev).add(value));
        return;
      }
      if (grantTypesRef.current.size <= 1) {
        addToast({
          variant: 'error',
          title: t('workspace.oauth2.create.grantRequiredTitle'),
          description: t('workspace.oauth2.create.grantRequiredDescription'),
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

  const validateForm = useCallback((): boolean => {
    const errors: typeof generalErrors = {};
    if (!name.trim()) {
      errors.name = t('workspace.oauth2.create.errorNameRequired');
    }

    if (usesAuthorizationCode) {
      const uris = redirectUris.map((u) => u.trim()).filter(Boolean);
      if (uris.length === 0) {
        errors.redirectUris = t('workspace.oauth2.create.errorRedirectRequired');
      } else if (uris.some((u) => !isValidOAuthRedirectUri(u))) {
        errors.redirectUris = t('workspace.oauth2.create.errorRedirectInvalid');
      }
    }

    if (grantTypes.size === 0) {
      errors.grants = t('workspace.oauth2.create.errorGrantRequired');
    }

    const optFields = [homepageUrl, privacyPolicyUrl, termsOfServiceUrl];
    for (const v of optFields) {
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
    termsOfServiceUrl,
    t,
  ]);

  const trimmedRedirectUris = useMemo(
    () => redirectUris.map((u) => u.trim()).filter(Boolean),
    [redirectUris]
  );
  const hasValidRedirect = trimmedRedirectUris.some((u) =>
    isValidOAuthRedirectUri(u)
  );

  const baseGeneralValid =
    Boolean(name.trim()) &&
    grantTypes.size > 0 &&
    (!usesAuthorizationCode || hasValidRedirect);

  const scopesRequirementMet =
    totalScopeCount === 0 || selectedScopes.size > 0;

  const formStructurallyValid = baseGeneralValid && scopesRequirementMet;

  const primaryDisabled =
    detailLoading ||
    !detail ||
    !clientId ||
    !formStructurallyValid ||
    !isDirty ||
    isSaving;

  const primaryTooltip = useMemo(() => {
    if (detailLoading || !detail || !clientId || isSaving) return undefined;
    if (!formStructurallyValid) {
      if (baseGeneralValid && !scopesRequirementMet) {
        return t('workspace.oauth2.create.errorScopeRequired');
      }
      return t('workspace.oauth2.manageApplication.saveDisabledInvalid');
    }
    if (!isDirty) {
      return t('workspace.oauth2.manageApplication.saveDisabledNoChanges');
    }
    return undefined;
  }, [
    detailLoading,
    detail,
    clientId,
    isSaving,
    formStructurallyValid,
    baseGeneralValid,
    scopesRequirementMet,
    isDirty,
    t,
  ]);

  const handleSave = useCallback(async () => {
    if (!clientId || !detail) return;
    if (totalScopeCount > 0 && selectedScopes.size === 0) {
      setActiveTab('scopes');
      return;
    }
    if (!validateForm()) {
      setActiveTab('general');
      return;
    }
    setIsSaving(true);
    try {
      const redirectUrisClean = usesAuthorizationCode
        ? redirectUris.map((u) => u.trim()).filter(Boolean)
        : [];
      const payload = {
        name: name.trim(),
        description: description.trim() || undefined,
        redirectUris: redirectUrisClean,
        allowedGrantTypes: Array.from(grantTypes) as OAuthGrantTypeValue[],
        allowedScopes: Array.from(selectedScopes),
        ...tokenLifetimesFromClient(detail),
        homepageUrl: optionalUrlOrEmpty(homepageUrl),
        privacyPolicyUrl: optionalUrlOrEmpty(privacyPolicyUrl),
        termsOfServiceUrl: optionalUrlOrEmpty(termsOfServiceUrl),
        isConfidential: detail.isConfidential ?? true,
      };

      await Oauth2Api.updateOAuthClient(clientId, payload);
      const next = await Oauth2Api.getOAuthClient(clientId);

      setDetail(next);
      applyDetailToForm(next);
      setBaseline(
        buildSnapshot({
          name: next.name ?? '',
          description:
            typeof next.description === 'string'
              ? next.description
              : next.description ?? '',
          grantTypes: parseGrantTypesFromDetail(next.allowedGrantTypes),
          redirectUris: next.redirectUris?.length ? [...next.redirectUris] : [''],
          selectedScopes: new Set(next.allowedScopes ?? []),
          homepageUrl: oauthUrlFromDetail(next.homepageUrl),
          privacyPolicyUrl: oauthUrlFromDetail(next.privacyPolicyUrl),
          termsOfServiceUrl: oauthUrlFromDetail(next.termsOfServiceUrl),
        })
      );

      addToast({
        variant: 'success',
        title: t('workspace.oauth2.manageApplication.saveSuccessTitle'),
        description: t('workspace.oauth2.manageApplication.saveSuccessDescription'),
        duration: 4000,
      });
      onSaved?.();
      onOpenChange(false);
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.oauth2.manageApplication.saveErrorTitle'),
        duration: 5000,
      });
    } finally {
      setIsSaving(false);
    }
  }, [
    clientId,
    detail,
    validateForm,
    redirectUris,
    name,
    description,
    grantTypes,
    selectedScopes,
    homepageUrl,
    privacyPolicyUrl,
    termsOfServiceUrl,
    applyDetailToForm,
    addToast,
    t,
    onSaved,
    onOpenChange,
    usesAuthorizationCode,
    totalScopeCount,
  ]);

  const handleRegenerateSecret = useCallback(async () => {
    if (!clientId) return;
    setRegeneratingSecret(true);
    try {
      const res = await Oauth2Api.regenerateOAuthClientSecret(clientId);
      setNewClientSecret(res.clientSecret);
      addToast({
        variant: 'success',
        title: t('workspace.oauth2.manageApplication.regenerateSuccessTitle'),
        description: res.message,
        duration: 6000,
      });
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.oauth2.manageApplication.regenerateErrorTitle'),
        duration: 5000,
      });
    } finally {
      setRegeneratingSecret(false);
    }
  }, [clientId, addToast, t]);

  const handleConfirmRevokeTokens = useCallback(async () => {
    if (!clientId) return;
    setIsRevokingTokens(true);
    try {
      await Oauth2Api.revokeOAuthClientTokens(clientId);
      setRevokeDialogOpen(false);
      addToast({
        variant: 'success',
        title: t('workspace.oauth2.manageApplication.revokeSuccessTitle'),
        description: t('workspace.oauth2.manageApplication.revokeSuccessDescription'),
        duration: 5000,
      });
      onSaved?.();
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.oauth2.manageApplication.revokeErrorTitle'),
        duration: 5000,
      });
    } finally {
      setIsRevokingTokens(false);
    }
  }, [clientId, addToast, t, onSaved]);

  const handleConfirmDeleteApp = useCallback(async () => {
    if (!clientId) return;
    setIsDeletingApp(true);
    try {
      await Oauth2Api.deleteOAuthClient(clientId);
      setDeleteDialogOpen(false);
      addToast({
        variant: 'success',
        title: t('workspace.oauth2.manageApplication.deleteSuccessTitle'),
        description: t('workspace.oauth2.manageApplication.deleteSuccessDescription'),
        duration: 5000,
      });
      onOpenChange(false);
      onSaved?.();
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.oauth2.manageApplication.deleteErrorTitle'),
        duration: 5000,
      });
    } finally {
      setIsDeletingApp(false);
    }
  }, [clientId, addToast, t, onOpenChange, onSaved]);

  const handleActivateApplication = useCallback(async () => {
    if (!clientId || !detail || detail.status !== 'suspended') return;
    setStatusActionLoading(true);
    try {
      await Oauth2Api.activateOAuthClient(clientId);
      const next = await Oauth2Api.getOAuthClient(clientId);
      setDetail(next);
      addToast({
        variant: 'success',
        title: t('workspace.oauth2.manageApplication.activateSuccessTitle'),
        duration: 5000,
      });
      onSaved?.();
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.oauth2.manageApplication.activateErrorTitle'),
        duration: 5000,
      });
    } finally {
      setStatusActionLoading(false);
    }
  }, [clientId, detail, addToast, t, onSaved]);

  const handleConfirmSuspend = useCallback(async () => {
    if (!clientId || !detail || detail.status === 'suspended') return;
    setStatusActionLoading(true);
    try {
      await Oauth2Api.suspendOAuthClient(clientId);
      setSuspendDialogOpen(false);
      const next = await Oauth2Api.getOAuthClient(clientId);
      setDetail(next);
      addToast({
        variant: 'success',
        title: t('workspace.oauth2.manageApplication.suspendSuccessTitle'),
        duration: 5000,
      });
      onSaved?.();
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.oauth2.manageApplication.suspendErrorTitle'),
        duration: 5000,
      });
    } finally {
      setStatusActionLoading(false);
    }
  }, [clientId, detail, addToast, t, onSaved]);

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

  const oauthClientIdDisplay = detail?.clientId ?? '';

  return (
    <>
      <WorkspaceRightPanel
        open={open && Boolean(clientId)}
        onOpenChange={onOpenChange}
        title={t('workspace.oauth2.manageApplication.panelTitle')}
        icon={<MaterialIcon name="vpn_key" size={20} color="var(--slate-12)" />}
        headerActions={documentationAction}
        hideFooter={activeTab === 'advanced'}
        primaryLabel={t('workspace.oauth2.manageApplication.saveChanges')}
        secondaryLabel={t('workspace.oauth2.create.cancel')}
        primaryDisabled={primaryDisabled}
        primaryLoading={isSaving}
        primaryTooltip={primaryTooltip}
        onPrimaryClick={() => void handleSave()}
        onSecondaryClick={handleClose}
      >
        {detailLoading && (
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {t('workspace.oauth2.manageApplication.loadingDetail')}
          </Text>
        )}

        {!detailLoading && detailError && (
          <Flex direction="column" gap="2" align="start">
            <Text size="2" style={{ color: 'var(--red-11)' }}>
              {detailError}
            </Text>
            <Button
              size="2"
              variant="soft"
              onClick={() => {
                if (!clientId) return;
                setDetailError(null);
                setDetailLoading(true);
                void Promise.all([
                  Oauth2Api.getOAuthClient(clientId),
                  Oauth2Api.getScopes(),
                ])
                  .then(([d, scopesData]) => {
                    setDetail(d);
                    applyDetailToForm(d);
                    setBaseline(
                      buildSnapshot({
                        name: d.name ?? '',
                        description:
                          typeof d.description === 'string'
                            ? d.description
                            : d.description ?? '',
                        grantTypes: parseGrantTypesFromDetail(d.allowedGrantTypes),
                        redirectUris: d.redirectUris?.length
                          ? [...d.redirectUris]
                          : [''],
                        selectedScopes: new Set(d.allowedScopes ?? []),
                        homepageUrl: oauthUrlFromDetail(d.homepageUrl),
                        privacyPolicyUrl: oauthUrlFromDetail(d.privacyPolicyUrl),
                        termsOfServiceUrl: oauthUrlFromDetail(d.termsOfServiceUrl),
                      })
                    );
                    setScopesByCategory(scopesData.scopes ?? {});
                  })
                  .catch(() => {
                    setDetailError(t('workspace.oauth2.manageApplication.loadError'));
                  })
                  .finally(() => setDetailLoading(false));
              }}
            >
              {t('workspace.oauth2.manageApplication.loadRetry')}
            </Button>
          </Flex>
        )}

        {!detailLoading && !detailError && detail && (
          <Tabs.Root
            value={activeTab}
            onValueChange={(v) =>
              setActiveTab(v as 'general' | 'scopes' | 'advanced')
            }
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
              <Tabs.Trigger value="advanced">
                {t('workspace.oauth2.manageApplication.tabAdvanced')}
              </Tabs.Trigger>
            </Tabs.List>

            <Tabs.Content value="general">
              <Flex direction="column" gap="4">
                <Box style={CARD_STYLE}>
                  <Flex direction="column" gap="2">
                    <Text
                      size="2"
                      weight="medium"
                      style={{ color: 'var(--slate-12)' }}
                    >
                      {t('workspace.oauth2.manageApplication.aboutHeading')}
                    </Text>
                    <Text size="1" style={{ color: 'var(--slate-11)' }}>
                      {t('workspace.oauth2.manageApplication.aboutHelper')}
                    </Text>
                    <ReadonlyCopyField
                      value={oauthClientIdDisplay}
                      copyLabel={t('workspace.oauth2.manageApplication.copyClientIdAria')}
                      onCopy={() => void copyToClipboard(oauthClientIdDisplay)}
                    />
                  </Flex>
                </Box>

                <Box style={CARD_STYLE}>
                  <Flex direction="column" gap="2">
                    <Text
                      size="2"
                      weight="medium"
                      style={{ color: 'var(--slate-12)' }}
                    >
                      {t('workspace.oauth2.manageApplication.clientSecretsHeading')}
                    </Text>
                    <Text size="1" style={{ color: 'var(--slate-11)' }}>
                      {t('workspace.oauth2.manageApplication.clientSecretsHelper')}
                    </Text>
                    <Button
                      type="button"
                      variant="outline"
                      color="gray"
                      size="2"
                      disabled={regeneratingSecret}
                      style={{ alignSelf: 'flex-start', cursor: 'pointer', gap: 8 }}
                      onClick={() => void handleRegenerateSecret()}
                    >
                      <MaterialIcon
                        name="refresh"
                        size={16}
                        color="var(--slate-11)"
                      />
                      {regeneratingSecret
                        ? t('workspace.oauth2.manageApplication.generatingSecret')
                        : t('workspace.oauth2.manageApplication.generateSecret')}
                    </Button>
                    {newClientSecret && (
                      <Flex direction="column" gap="1" style={{ marginTop: 'var(--space-2)' }}>
                        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                          {t('workspace.oauth2.manageApplication.newSecretLabel')}
                        </Text>
                        <ReadonlyCopyField
                          value={newClientSecret}
                          copyLabel={t('workspace.oauth2.manageApplication.copySecretAria')}
                          onCopy={() => void copyToClipboard(newClientSecret)}
                        />
                      </Flex>
                    )}
                  </Flex>
                </Box>

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
                    <Flex
                      direction="column"
                      gap="2"
                      style={{ marginTop: 'var(--space-3)' }}
                    >
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
                            <MaterialIcon
                              name="close"
                              size={18}
                              color="var(--slate-11)"
                            />
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
                        size="2"
                        type="url"
                        placeholder={t('workspace.oauth2.create.urlPlaceholder')}
                        value={homepageUrl}
                        onChange={(e) => setHomepageUrl(e.target.value)}
                        style={{ width: '100%' }}
                      />
                    </FormField>
                    <FormField
                      label={t('workspace.oauth2.create.privacyLabel')}
                      optional
                    >
                      <TextField.Root
                        size="2"
                        type="url"
                        placeholder={t('workspace.oauth2.create.urlPlaceholder')}
                        value={privacyPolicyUrl}
                        onChange={(e) => setPrivacyPolicyUrl(e.target.value)}
                        style={{ width: '100%' }}
                      />
                    </FormField>
                    <FormField
                      label={t('workspace.oauth2.create.termsLabel')}
                      optional
                    >
                      <TextField.Root
                        size="2"
                        type="url"
                        placeholder={t('workspace.oauth2.create.urlPlaceholder')}
                        value={termsOfServiceUrl}
                        onChange={(e) => setTermsOfServiceUrl(e.target.value)}
                        style={{ width: '100%' }}
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
                        <Flex
                          direction="column"
                          gap="2"
                          style={{ marginTop: 'var(--space-3)' }}
                        >
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

            <Tabs.Content value="advanced">
              <Flex direction="column" gap="4">
                <Flex
                  align="center"
                  justify="between"
                  gap="3"
                  wrap="wrap"
                  style={{ width: '100%' }}
                >
                  <Text size="4" weight="medium" style={{ color: 'var(--slate-12)' }}>
                    {t('workspace.oauth2.manageApplication.advancedSectionTitle')}
                  </Text>
                  <LoadingButton
                    type="button"
                    variant="outline"
                    color={detail.status === 'suspended' ? 'jade' : 'amber'}
                    size="2"
                    style={{ flexShrink: 0 }}
                    loading={statusActionLoading}
                    loadingLabel={t('workspace.oauth2.manageApplication.statusActionPending')}
                    onClick={() => {
                      if (detail.status === 'suspended') {
                        void handleActivateApplication();
                      } else {
                        setSuspendDialogOpen(true);
                      }
                    }}
                  >
                    {detail.status === 'suspended'
                      ? t('workspace.oauth2.manageApplication.activateApplicationButton')
                      : t('workspace.oauth2.manageApplication.suspendApplicationButton')}
                  </LoadingButton>
                </Flex>

                <Flex align="center" gap="3" style={{ width: '100%' }}>
                  <Text
                    size="1"
                    weight="bold"
                    style={{
                      color: 'var(--slate-11)',
                      letterSpacing: '0.08em',
                      flexShrink: 0,
                    }}
                  >
                    {t('workspace.oauth2.manageApplication.statusLabel')}
                  </Text>
                  <Badge
                    color={detail.status === 'suspended' ? 'orange' : 'green'}
                    variant="soft"
                    size="2"
                    style={{ flexShrink: 0 }}
                  >
                    {detail.status === 'suspended'
                      ? t('workspace.oauth2.manageApplication.statusSuspended')
                      : t('workspace.oauth2.manageApplication.statusActive')}
                  </Badge>
                </Flex>

                <Flex
                  align="center"
                  gap="3"
                  style={{ width: '100%', marginTop: 'var(--space-2)' }}
                >
                  <Box
                    style={{
                      flex: 1,
                      height: 1,
                      backgroundColor: 'var(--olive-5)',
                      minWidth: 0,
                    }}
                  />
                  <Text
                    size="1"
                    weight="bold"
                    style={{
                      color: 'var(--red-11)',
                      letterSpacing: '0.08em',
                      flexShrink: 0,
                    }}
                  >
                    {t('workspace.oauth2.manageApplication.dangerZoneLabel')}
                  </Text>
                  <Box
                    style={{
                      flex: 1,
                      height: 1,
                      backgroundColor: 'var(--olive-5)',
                      minWidth: 0,
                    }}
                  />
                </Flex>

                <Box
                  style={{
                    ...CARD_STYLE,
                    borderColor: 'var(--red-a6)',
                    backgroundColor: 'var(--olive-2)',
                  }}
                >
                  <Flex
                    align="start"
                    justify="between"
                    gap="4"
                    wrap="wrap"
                    style={{ width: '100%' }}
                  >
                    <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 200 }}>
                      <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                        {t('workspace.oauth2.manageApplication.revokeTokensTitle')}
                      </Text>
                      <Text size="2" style={{ color: 'var(--slate-11)' }}>
                        {t('workspace.oauth2.manageApplication.revokeTokensDescription')}
                      </Text>
                    </Flex>
                    <Button
                      type="button"
                      variant="outline"
                      color="red"
                      size="2"
                      style={{ flexShrink: 0, cursor: 'pointer' }}
                      onClick={() => setRevokeDialogOpen(true)}
                    >
                      {t('workspace.oauth2.manageApplication.revokeTokensButton')}
                    </Button>
                  </Flex>
                </Box>

                <Box
                  style={{
                    ...CARD_STYLE,
                    borderColor: 'var(--red-a6)',
                    backgroundColor: 'var(--olive-2)',
                  }}
                >
                  <Flex
                    align="start"
                    justify="between"
                    gap="4"
                    wrap="wrap"
                    style={{ width: '100%' }}
                  >
                    <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 200 }}>
                      <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                        {t('workspace.oauth2.manageApplication.deleteApplicationTitle')}
                      </Text>
                      <Text size="2" style={{ color: 'var(--slate-11)' }}>
                        {t('workspace.oauth2.manageApplication.deleteApplicationDescription')}
                      </Text>
                    </Flex>
                    <Button
                      type="button"
                      variant="solid"
                      color="red"
                      size="2"
                      style={{ flexShrink: 0, cursor: 'pointer' }}
                      onClick={() => setDeleteDialogOpen(true)}
                    >
                      {t('workspace.oauth2.manageApplication.deleteApplicationButton')}
                    </Button>
                  </Flex>
                </Box>
              </Flex>
            </Tabs.Content>
          </Tabs.Root>
        )}
      </WorkspaceRightPanel>

      <DestructiveTypedConfirmationDialog
        open={revokeDialogOpen}
        onOpenChange={setRevokeDialogOpen}
        heading={t('workspace.oauth2.manageApplication.revokeTypedConfirmTitle')}
        body={
          <>
            <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
              {t('workspace.oauth2.manageApplication.revokeTypedConfirmBodyLine1', {
                name: oauthAppDisplayName,
              })}
            </Text>
            <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
              {t('workspace.oauth2.manageApplication.revokeTypedConfirmBodyLine2')}
            </Text>
          </>
        }
        confirmationKeyword={revokeConfirmationKeyword}
        confirmInputLabel={t('workspace.oauth2.manageApplication.typeKeywordToConfirm', {
          keyword: revokeConfirmationKeyword,
        })}
        primaryButtonText={t('workspace.oauth2.manageApplication.revokeConfirmAction')}
        cancelLabel={t('workspace.oauth2.create.cancel')}
        isLoading={isRevokingTokens}
        confirmLoadingLabel={t('workspace.oauth2.manageApplication.revoking')}
        onConfirm={() => void handleConfirmRevokeTokens()}
      />

      <DestructiveTypedConfirmationDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        heading={t('workspace.oauth2.manageApplication.deleteTypedConfirmTitle')}
        body={
          <>
            <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
              {t('workspace.oauth2.manageApplication.deleteTypedConfirmBodyLine1', {
                name: oauthAppDisplayName,
              })}
            </Text>
            <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
              {t('workspace.oauth2.manageApplication.deleteTypedConfirmBodyLine2')}
            </Text>
            <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
              {t('workspace.oauth2.manageApplication.deleteTypedConfirmBodyLine3')}
            </Text>
          </>
        }
        confirmationKeyword={deleteConfirmationKeyword}
        confirmInputLabel={t('workspace.oauth2.manageApplication.typeKeywordToConfirm', {
          keyword: deleteConfirmationKeyword,
        })}
        primaryButtonText={t('workspace.oauth2.manageApplication.deleteConfirmAction')}
        cancelLabel={t('workspace.oauth2.create.cancel')}
        isLoading={isDeletingApp}
        confirmLoadingLabel={t('workspace.oauth2.manageApplication.deletingApp')}
        onConfirm={() => void handleConfirmDeleteApp()}
      />

      <DestructiveTypedConfirmationDialog
        open={suspendDialogOpen}
        onOpenChange={setSuspendDialogOpen}
        heading={t('workspace.oauth2.manageApplication.suspendTypedConfirmTitle')}
        body={
          <>
            <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
              {t('workspace.oauth2.manageApplication.suspendTypedConfirmBodyLine1', {
                name: oauthAppDisplayName,
              })}
            </Text>
            <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
              {t('workspace.oauth2.manageApplication.suspendTypedConfirmBodyLine2')}
            </Text>
          </>
        }
        confirmationKeyword={OAUTH_SUSPEND_CONFIRMATION_KEYWORD}
        confirmInputLabel={t('workspace.oauth2.manageApplication.typeKeywordToConfirm', {
          keyword: OAUTH_SUSPEND_CONFIRMATION_KEYWORD,
        })}
        primaryButtonText={t('workspace.oauth2.manageApplication.suspendTypedConfirmAction')}
        cancelLabel={t('workspace.oauth2.create.cancel')}
        isLoading={statusActionLoading}
        confirmLoadingLabel={t('workspace.oauth2.manageApplication.suspendTypedConfirmSuspending')}
        onConfirm={() => void handleConfirmSuspend()}
      />
    </>
  );
}
