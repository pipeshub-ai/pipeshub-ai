'use client';

import React, { useRef, useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Flex,
  Text,
  Heading,
  Switch,
  TextField,
  Avatar,
} from '@radix-ui/themes';
import {
  ConfirmationDialog,
  SettingsSaveBar,
  AvatarUploadWidget,
  SettingsSection,
  SettingsRow,
} from '../components';
import { useToastStore } from '@/lib/store/toast-store';
import { useGeneralStore } from './store';
import type { GeneralFormData } from './store';
import { OrgApi, MetricsApi, type MetricsConfig, type OrgResponse } from './api';
import { useOrg, useOrgLogo, useOrgMetrics } from './use-org';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { SettingsPageContainer } from '@/app/(main)/workspace/components/settings-page-container';

// ========================================
// Read-only view for non-admin users
// ========================================

interface ReadOnlyGeneralPageProps {
  form: GeneralFormData;
  logoUrl: string | null;
}

function ReadOnlyGeneralPage({ form, logoUrl }: ReadOnlyGeneralPageProps) {
  const { t } = useTranslation();
  const logoInitial = form.displayName
    ? form.displayName.charAt(0).toUpperCase()
    : form.registeredName
      ? form.registeredName.charAt(0).toUpperCase()
      : 'P';

  return (
    <Box style={{ height: '100%', overflowY: 'auto' }}>
      <SettingsPageContainer>
        <Box style={{ marginBottom: 'var(--space-6)' }}>
          <Heading size="5" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {t('workspace.sidebar.nav.general')}
          </Heading>
          <Text size="2" style={{ color: 'var(--gray-10)', marginTop: 'var(--space-1)', display: 'block' }}>
            {t('workspace.general.subtitle')}
          </Text>
        </Box>

        <SettingsSection title={t('workspace.general.companyProfile')}>
          <SettingsRow label={t('workspace.general.logoLabel')} description={t('workspace.general.logoDescription')}>
            <Avatar
              src={logoUrl ?? undefined}
              fallback={logoInitial}
              size="4"
              radius="medium"
              style={{ backgroundColor: 'var(--accent-9)', color: 'white' }}
            />
          </SettingsRow>

          <SettingsRow label={t('workspace.general.registeredName')} description={t('workspace.general.registeredNameDescription')}>
            <TextField.Root value={form.registeredName} readOnly />
          </SettingsRow>

          <SettingsRow
            label={t('workspace.general.displayName')}
            description={t('workspace.general.displayNameDescription')}
          >
            <TextField.Root value={form.displayName} readOnly />
          </SettingsRow>

          <SettingsRow
            label={t('workspace.general.contactEmail')}
            description={t('workspace.general.contactEmailDescription')}
          >
            <TextField.Root value={form.contactEmail} readOnly />
          </SettingsRow>
        </SettingsSection>
      </SettingsPageContainer>
    </Box>
  );
}

function toGeneralForm(org: OrgResponse, metrics: MetricsConfig | null): GeneralFormData {
  return {
    registeredName: org.registeredName || '',
    displayName: org.shortName || '',
    contactEmail: org.contactEmail || '',
    streetAddress: org.permanentAddress?.addressLine1 || '',
    country: org.permanentAddress?.country || '',
    state: org.permanentAddress?.state || '',
    city: org.permanentAddress?.city || '',
    zipCode: org.permanentAddress?.postCode || '',
    dataCollection: metrics?.enableMetricCollection === 'true',
    logoFileName: null,
  };
}

// ========================================
// Main Page
// ========================================

export default function GeneralPage() {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { data: org, error: orgError, isValidating: orgValidating, mutate: rereadOrg } = useOrg();
  const { data: savedLogoUrl, isLoading: logoLoading } = useOrgLogo();
  const {
    data: metrics,
    error: metricsError,
    isValidating: metricsValidating,
    mutate: rereadMetrics,
  } = useOrgMetrics(isProfileInitialized && isAdmin === true);
  // Local preview of a logo while it uploads; the saved logo comes from the cache.
  const [previewLogoUrl, setPreviewLogoUrl] = useState<string | null>(null);
  const [logoUploading, setLogoUploading] = useState(false);
  const [logoDeleting, setLogoDeleting] = useState(false);
  const logoUrl = previewLogoUrl ?? savedLogoUrl ?? null;
  const hasServerLogo = Boolean(savedLogoUrl);
  // Until the form holds the server's values, Save stays hidden: it would send blanks over them.
  const [formSeeded, setFormSeeded] = useState(false);
  const loadErrorShownRef = useRef(false);
  const cachedErrorRetriedRef = useRef(false);

  // ── Store selectors ────────────────────────────────────────
  const form = useGeneralStore((s) => s.form);
  const savedForm = useGeneralStore((s) => s.savedForm);
  const errors = useGeneralStore((s) => s.errors);
  const discardDialogOpen = useGeneralStore((s) => s.discardDialogOpen);
  const isLoading = useGeneralStore((s) => s.isLoading);

  const setField = useGeneralStore((s) => s.setField);
  const setForm = useGeneralStore((s) => s.setForm);
  const markSaved = useGeneralStore((s) => s.markSaved);
  const setErrors = useGeneralStore((s) => s.setErrors);
  const discardChanges = useGeneralStore((s) => s.discardChanges);
  const setDiscardDialogOpen = useGeneralStore((s) => s.setDiscardDialogOpen);
  const setLoading = useGeneralStore((s) => s.setLoading);
  const isDirty = useGeneralStore((s) => s.isDirty);

  // ── Log user store state on navigation to this page (dev only) ─────────
  useEffect(() => {
    if (process.env.NODE_ENV !== 'production') {
      console.debug('[workspace/general] UserStore state on mount:', useUserStore.getState());
    }
  }, []);

  // ── Fill the form from the cached org ──────────────────────

  // Seeded once per visit: later org/metrics refreshes (after Save) must not replace what is being edited.
  useEffect(() => {
    // isAdmin is null until the profile is initialized, which would pick the wrong branch.
    if (!isProfileInitialized || formSeeded) return;
    if (org && !logoLoading && (!isAdmin || metrics)) {
      setFormSeeded(true);
      setForm(toGeneralForm(org, isAdmin ? metrics : null));
      setLoading(false);
      return;
    }
    const orgFailed = orgError && !org && !orgValidating;
    const metricsFailed = isAdmin && metricsError && !metrics && !metricsValidating;
    if (!orgFailed && !metricsFailed) return;
    // An error left in the cache by an earlier read (the sidebar footer) is not this visit's answer:
    // ask once more before reporting it. So a real outage makes two reads before the toast.
    if (!cachedErrorRetriedRef.current) {
      cachedErrorRetriedRef.current = true;
      if (orgFailed) void rereadOrg();
      if (metricsFailed) void rereadMetrics();
      return;
    }
    if (!loadErrorShownRef.current) {
      loadErrorShownRef.current = true;
      addToast({
        variant: 'error',
        title: t('workspace.general.toasts.loadError'),
        description: t('workspace.general.toasts.loadErrorDescription'),
      });
    }
    setLoading(false);
  }, [isAdmin, isProfileInitialized, formSeeded, org, logoLoading, metrics, orgError, orgValidating,
    metricsError, metricsValidating, rereadOrg, rereadMetrics, addToast, setForm, setLoading]);

  // ── Handlers ───────────────────────────────────────────────

  const validate = useCallback((): boolean => {
    const newErrors: { contactEmail?: string; zipCode?: string } = {};
    if (form.contactEmail && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.contactEmail)) {
      newErrors.contactEmail = t('form.invalidEmail');
    }
    if (form.zipCode && !/^[A-Za-z0-9\s\-]{3,10}$/.test(form.zipCode)) {
      newErrors.zipCode = t('workspace.general.errors.invalidPostalCode');
    }
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }, [form.contactEmail, form.zipCode, setErrors]);

  const handleSave = useCallback(async () => {
    // Before the form holds the server's values, a save would send blanks over them.
    if (!formSeeded || !validate()) return;
    try {
      // 1. Update org details
      await OrgApi.updateOrg({
        registeredName: form.registeredName,
        shortName: form.displayName,
        contactEmail: form.contactEmail,
        permanentAddress: {
          addressLine1: form.streetAddress,
          country: form.country,
          state: form.state,
          city: form.city,
          postCode: form.zipCode,
        },
        dataCollectionConsent: form.dataCollection,
      });

      // 2. Toggle metrics collection if the value changed
      if (form.dataCollection !== savedForm.dataCollection) {
        await MetricsApi.toggleMetricsCollection(form.dataCollection);
      }

      markSaved();
      addToast({
        variant: 'success',
        title: t('workspace.general.toasts.saveSuccess'),
        description: t('workspace.general.toasts.saveSuccessDescription'),
      });
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.general.toasts.saveError'),
        description: t('workspace.general.toasts.saveErrorDescription'),
      });
    }
  }, [formSeeded, form, savedForm, validate, markSaved, addToast]);

  const handleDiscard = useCallback(() => {
    setDiscardDialogOpen(true);
  }, [setDiscardDialogOpen]);

  const handleDiscardConfirm = useCallback(() => {
    setPreviewLogoUrl(null);
    discardChanges();
    addToast({
      variant: 'success',
      title: t('workspace.general.toasts.discardSuccess'),
      description: t('workspace.general.toasts.discardSuccessDescription'),
    });
  }, [discardChanges, addToast]);

  const handleFileChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      e.target.value = '';

      const previewUrl = URL.createObjectURL(file);
      setPreviewLogoUrl(previewUrl);

      setLogoUploading(true);
      try {
        await OrgApi.uploadLogo(file);
        addToast({
          variant: 'success',
          title: t('workspace.general.toasts.logoUpdated'),
          description: t('workspace.general.toasts.logoUpdatedDescription'),
        });
      } catch {
        addToast({
          variant: 'error',
          title: t('workspace.general.toasts.logoUploadError'),
          description: t('workspace.general.toasts.logoUploadErrorDescription'),
        });
      } finally {
        URL.revokeObjectURL(previewUrl);
        setPreviewLogoUrl(null);
        setLogoUploading(false);
      }
    },
    [addToast]
  );

  const handleDeleteLogo = useCallback(async () => {
    setLogoDeleting(true);
    try {
      await OrgApi.deleteLogo();
      addToast({
        variant: 'success',
        title: t('workspace.general.toasts.logoRemoved'),
        description: t('workspace.general.toasts.logoRemovedDescription'),
      });
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.general.toasts.logoRemoveError'),
        description: t('workspace.general.toasts.logoRemoveErrorDescription'),
      });
    } finally {
      setLogoDeleting(false);
    }
  }, [addToast]);

  const logoInitial = form.displayName
    ? form.displayName.charAt(0).toUpperCase()
    : form.registeredName
      ? form.registeredName.charAt(0).toUpperCase()
      : 'P';

  if (isLoading) {
    return (
      <Flex
        align="center"
        justify="center"
        style={{ height: '100%', width: '100%' }}
      >
        <LottieLoader variant="loader" size={48} showLabel />
      </Flex>
    );
  }

  if (!isAdmin) {
    return <ReadOnlyGeneralPage form={form} logoUrl={logoUrl} />;
  }

  return (
    <Box style={{ height: '100%', overflowY: 'auto', position: 'relative' }}>
      {/* Hidden file input for logo */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        style={{ display: 'none' }}
        onChange={handleFileChange}
      />

      {/* Page content */}
      <SettingsPageContainer>
        {/* Page header */}
        <Box style={{ marginBottom: 'var(--space-6)' }}>
          <Heading size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('workspace.sidebar.nav.general')}
          </Heading>
          <Text size="2" style={{ color: 'var(--slate-10)', marginTop: 'var(--space-1)', display: 'block' }}>
           {t('workspace.general.manageSubtitle')}
          </Text>
        </Box>

        {/* ── Company Profile Section ── */}
        <Box style={{ marginBottom: 'var(--space-5)' }}>
          <SettingsSection title={t('workspace.general.companyProfile')}>
            {/* Logo */}
            <SettingsRow label={t('workspace.general.logoLabel')} description={t('workspace.general.logoDescription')}>
              <Flex align="center" justify="end" style={{ width: '100%' }}>
                <AvatarUploadWidget
                  src={logoUrl}
                  initial={logoInitial}
                  uploading={logoUploading || logoDeleting}
                  onEditClick={() => {
                    if (logoUploading || logoDeleting) return;
                    fileInputRef.current?.click();
                  }}
                  onDeleteClick={hasServerLogo ? handleDeleteLogo : undefined}
                  triggerAriaLabel={t('workspace.general.editLogoAria')}
                />
              </Flex>
            </SettingsRow>

            {/* Registered Name */}
            <SettingsRow label={t('workspace.general.registeredName')} description={t('workspace.general.registeredNameDescription')}>
              <TextField.Root
                placeholder={t('workspace.general.registeredNamePlaceholder')}
                value={form.registeredName}
                onChange={(e) => setField('registeredName', e.target.value)}
              />
            </SettingsRow>

            {/* Display Name */}
            <SettingsRow
              label={t('workspace.general.displayName')}
              description={t('workspace.general.displayNameDescription')}
            >
              <TextField.Root
                placeholder={t('workspace.general.displayNamePlaceholder')}
                value={form.displayName}
                onChange={(e) => setField('displayName', e.target.value)}
              />
            </SettingsRow>

            {/* Contact Email */}
            <SettingsRow
              label={t('workspace.general.contactEmail')}
              description={t('workspace.general.contactEmailDescription')}
            >
              <Flex direction="column" gap="1">
                <TextField.Root
                  placeholder={t('workspace.general.contactEmailPlaceholder')}
                  value={form.contactEmail}
                  onChange={(e) => setField('contactEmail', e.target.value)}
                  color={errors.contactEmail ? 'red' : undefined}
                />
                {errors.contactEmail && (
                  <Text size="1" style={{ color: 'var(--red-a11)' }}>
                    {errors.contactEmail}
                  </Text>
                )}
              </Flex>
            </SettingsRow>
          </SettingsSection>
        </Box>

        {/* ── Company Address Section ── */}
        <Box style={{ marginBottom: 'var(--space-5)' }}>
          <SettingsSection title={t('workspace.general.companyAddress')}>
            {/* Street Address */}
            <SettingsRow label={t('workspace.general.streetAddress')} description={t('workspace.general.streetAddressDescription')}>
              <TextField.Root
                placeholder={t('workspace.general.streetAddressPlaceholder')}
                value={form.streetAddress}
                onChange={(e) => setField('streetAddress', e.target.value)}
              />
            </SettingsRow>

            {/* Country */}
            <SettingsRow label={t('workspace.general.country')} description={t('workspace.general.countryDescription')}>
              <TextField.Root
                placeholder={t('workspace.general.countryPlaceholder')}
                value={form.country}
                onChange={(e) => setField('country', e.target.value)}
              />
            </SettingsRow>

            {/* State/Province */}
            <SettingsRow label={t('workspace.general.stateProvince')} description={t('workspace.general.stateProvinceDescription')}>
              <TextField.Root
                placeholder={t('workspace.general.stateProvincePlaceholder')}
                value={form.state}
                onChange={(e) => setField('state', e.target.value)}
              />
            </SettingsRow>

            {/* City */}
            <SettingsRow label={t('workspace.general.city')} description={t('workspace.general.cityDescription')}>
              <TextField.Root
                placeholder={t('workspace.general.cityPlaceholder')}
                value={form.city}
                onChange={(e) => setField('city', e.target.value)}
              />
            </SettingsRow>

            {/* Zip/Postal Code */}
            <SettingsRow
              label={t('workspace.general.postalCode')}
              description={t('workspace.general.postalCodeDescription')}
            >
              <Flex direction="column" gap="1">
                <TextField.Root
                  placeholder={t('workspace.general.postalCodePlaceholder')}
                  value={form.zipCode}
                  onChange={(e) => setField('zipCode', e.target.value)}
                  color={errors.zipCode ? 'red' : undefined}
                />
                {errors.zipCode && (
                  <Text size="1" style={{ color: 'var(--red-a11)' }}>
                    {errors.zipCode}
                  </Text>
                )}
              </Flex>
            </SettingsRow>
          </SettingsSection>
        </Box>

        {/* ── Data Collection Settings Section ── */}
        {/* Extra bottom padding so save bar doesn't overlap last section */}
        <Box style={{ marginBottom: 80 }}>
          <SettingsSection
            title={t('workspace.general.dataCollection')}
            rightAction={
              <Flex align="center" gap="2">
                <Switch
                  checked={form.dataCollection}
                  onCheckedChange={(checked) => setField('dataCollection', checked)}
                  size="1"
                />
                <Text size="2" style={{ color: 'var(--slate-11)' }}>
                  {t('workspace.general.enableDataCollection')}
                </Text>
              </Flex>
            }
          >
            <Box style={{ padding: 'var(--space-4) var(--space-5)' }}>
              <Text
                size="2"
                style={{ color: 'var(--slate-11)', display: 'block', marginBottom: 'var(--space-3)' }}
              >
                {t('workspace.general.dataCollectionDescription')}
              </Text>
              <Flex direction="column" gap="2" style={{ paddingLeft: 'var(--space-1)' }}>
                {[
                  t('workspace.general.dataCollectionReason1'),
                  t('workspace.general.dataCollectionReason2'),
                  t('workspace.general.dataCollectionReason3'),
                  t('workspace.general.dataCollectionReason4'),
                  t('workspace.general.dataCollectionReason5'),
                ].map((item) => (
                  <Flex key={item} align="start" gap="2">
                    <Box
                      style={{
                        width: 'var(--space-1)',
                        height: 'var(--space-1)',
                        borderRadius: '50%',
                        backgroundColor: 'var(--slate-9)',
                        marginTop: 'var(--space-2)',
                        flexShrink: 0,
                      }}
                    />
                    <Text size="2" style={{ color: 'var(--slate-11)' }}>
                      {item}
                    </Text>
                  </Flex>
                ))}
              </Flex>
            </Box>
          </SettingsSection>
        </Box>
      </SettingsPageContainer>

      {/* ── Discard Confirmation Dialog ── */}
      <ConfirmationDialog
        open={discardDialogOpen}
        onOpenChange={setDiscardDialogOpen}
        title={t('workspace.general.discardDialog.title')}
        message={t('workspace.general.discardDialog.message')}
        confirmLabel={t('workspace.general.discardDialog.confirm')}
        cancelLabel={t('workspace.general.discardDialog.cancel')}
        confirmVariant="danger"
        onConfirm={handleDiscardConfirm}
      />

      {/* ── Settings Save Bar (shown when dirty) ── */}
      <SettingsSaveBar visible={formSeeded && isDirty()} onDiscard={handleDiscard} onSave={handleSave} />
    </Box>
  );
}
