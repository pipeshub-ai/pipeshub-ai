'use client';

import React, { useCallback, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import {
  Box,
  Flex,
  Text,
  Heading,
  TextField,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  ConfirmationDialog,
  SettingsSaveBar,
} from '../components';
import { useToastStore } from '@/lib/store/toast-store';
import { useLabsStore } from './store';
import { LabsApi, bytesToMb, mbToBytes } from './api';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';

// ========================================
// Local Sub-components (mirror general/page.tsx patterns)
// ========================================

interface SettingsSectionProps {
  title: string;
  description?: string;
  children: React.ReactNode;
}

function SettingsSection({ title, description, children }: SettingsSectionProps) {
  return (
    <Flex
      direction="column"
      gap="4"
      style={{
        border: '1px solid var(--slate-5)',
        borderRadius: 'var(--radius-1)',
        padding: 16,
        backdropFilter: 'blur(25px)',
        backgroundColor: 'var(--slate-2)',
      }}
    >
      {/* Section header */}
      <Flex direction="column" gap="1">
        <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {title}
        </Text>
        {description && (
          <Text size="1" style={{ color: 'var(--slate-9)', fontWeight: 300, lineHeight: '16px' }}>
            {description}
          </Text>
        )}
      </Flex>
      {/* Divider */}
      <Box style={{ height: 1, backgroundColor: 'var(--slate-5)', width: '100%' }} />
      {/* Content */}
      <Flex direction="column" gap="5">
        {children}
      </Flex>
    </Flex>
  );
}

interface SettingsRowProps {
  label: string;
  description?: string;
  children: React.ReactNode;
}

function SettingsRow({ label, description, children }: SettingsRowProps) {
  return (
    <Flex align="center" justify="between" style={{ width: '100%' }}>
      <Box style={{ flex: 1 }}>
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
          {label}
        </Text>
        {description && (
          <Text
            size="1"
            style={{ color: 'var(--slate-9)', display: 'block', marginTop: 2, lineHeight: '16px', fontWeight: 300 }}
          >
            {description}
          </Text>
        )}
      </Box>
      <Box style={{ flex: '0 0 38%', minWidth: 200 }}>{children}</Box>
    </Flex>
  );
}

/** Accent-tinted info callout used inside sections */
function InfoCallout({ children }: { children: React.ReactNode }) {
  return (
    <Flex
      align="center"
      gap="2"
      style={{
        backgroundColor: 'var(--accent-2)',
        border: '1px solid var(--accent-6)',
        borderRadius: 'var(--radius-1)',
        padding: '10px 12px',
      }}
    >
      <MaterialIcon name="info" size={16} color="var(--accent-9)" style={{ flexShrink: 0 }} />
      <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: '16px' }}>
        {children}
      </Text>
    </Flex>
  );
}

/** Bottom info note (Platform Configuration) */
function PlatformConfigNote() {
  const { t } = useTranslation();
  return (
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
      <Box style={{ flexShrink: 0, marginTop: 1 }}>
        <MaterialIcon name="info" size={16} color="var(--accent-9)" />
      </Box>
      <Flex direction="column" gap="1">
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('workspace.labs.title')}
        </Text>
        <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: '16px', fontWeight: 300 }}>
          {t('workspace.labs.subtitle')}
        </Text>
      </Flex>
    </Flex>
  );
}

// ========================================
// Main Page
// ========================================

export default function LabsPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const addToast = useToastStore((s) => s.addToast);
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  // ── Store selectors (must run every render; see Rules of Hooks) ──
  const form = useLabsStore((s) => s.form);
  const savedForm = useLabsStore((s) => s.savedForm);
  const errors = useLabsStore((s) => s.errors);
  const discardDialogOpen = useLabsStore((s) => s.discardDialogOpen);
  const isLoading = useLabsStore((s) => s.isLoading);

  const setFileSizeLimitMb = useLabsStore((s) => s.setFileSizeLimitMb);
  const setForm = useLabsStore((s) => s.setForm);
  const markSaved = useLabsStore((s) => s.markSaved);
  const setErrors = useLabsStore((s) => s.setErrors);
  const discardChanges = useLabsStore((s) => s.discardChanges);
  const setDiscardDialogOpen = useLabsStore((s) => s.setDiscardDialogOpen);
  const setLoading = useLabsStore((s) => s.setLoading);
  const isDirty = useLabsStore((s) => s.isDirty);

  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) {
      router.replace('/workspace/general');
    }
  }, [isProfileInitialized, isAdmin, router]);

  // ── Load config on mount ───────────────────────────────────
  useEffect(() => {
    if (!isProfileInitialized || isAdmin === false) {
      return;
    }
    const fetchConfig = async () => {
      try {
        const [settingsResult] = await Promise.allSettled([
          LabsApi.getSettings(),
        ]);

        const settings = settingsResult.status === 'fulfilled' ? settingsResult.value : null;

        setForm({
            fileSizeLimitMb: settings ? bytesToMb(settings.fileUploadMaxSizeBytes) : '',
          featureFlags: {},
        }, []);
      } catch {
        setLoading(false);
      }
    };
    fetchConfig();
  }, [isProfileInitialized, isAdmin, setForm, setLoading]);

  // ── Validation ─────────────────────────────────────────────
  const validate = useCallback((): boolean => {
    const newErrors: { fileSizeLimitMb?: string } = {};
    const limit = form.fileSizeLimitMb;
    if (limit === '' || limit === undefined) {
      newErrors.fileSizeLimitMb = t('workspace.labs.errors.fileSizeRequired');
    } else if (Number(limit) > 1000) {
      newErrors.fileSizeLimitMb = t('workspace.labs.errors.fileSizeMax');
    } else if (Number(limit) <= 0) {
      newErrors.fileSizeLimitMb = t('workspace.labs.errors.fileSizeMin');
    }
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }, [form.fileSizeLimitMb, setErrors]);

  // ── Save ───────────────────────────────────────────────────
  const handleSaveRef = useRef<() => Promise<void>>(async () => {});

  const handleSave = useCallback(async () => {
    if (!validate()) return;

    const fileSizeDirty = form.fileSizeLimitMb !== savedForm.fileSizeLimitMb;

    try {
      await LabsApi.saveSettings({
        fileUploadMaxSizeBytes: mbToBytes(Number(form.fileSizeLimitMb)),
        featureFlags: form.featureFlags,
      });

      markSaved();

      if (fileSizeDirty) {
        addToast({
          variant: 'success',
          title: t('workspace.labs.toasts.saveSuccess'),
          description: t('workspace.labs.toasts.saveSuccessDescription'),
        });
      }

    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.labs.toasts.saveError'),
        description: t('workspace.labs.toasts.saveErrorDescription'),
        action: {
          label: t('message.tryAgain'),
          onClick: () => handleSaveRef.current(),
        },
      });
    }
  }, [form, savedForm, validate, markSaved, addToast]);

  handleSaveRef.current = handleSave;

  // ── Discard ────────────────────────────────────────────────
  const handleDiscard = useCallback(() => {
    setDiscardDialogOpen(true);
  }, [setDiscardDialogOpen]);

  const handleDiscardConfirm = useCallback(() => {
    discardChanges();
    addToast({
      variant: 'success',
      title: t('workspace.labs.toasts.discardSuccess'),
      description: t('workspace.labs.toasts.discardSuccessDescription'),
    });
  }, [discardChanges, addToast]);

  // No UI (and no fetch — see effect guard) until profile is known and user is not a confirmed non-admin.
  if (!isProfileInitialized || isAdmin === false) {
    return null;
  }

  // ── File size limit input handler ──────────────────────────
  const handleFileSizeLimitChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value;
    if (raw === '') {
      setFileSizeLimitMb('');
      return;
    }
    const num = parseInt(raw, 10);
    if (!isNaN(num)) {
      setFileSizeLimitMb(num);
    }
  };

  // ── Loading state ──────────────────────────────────────────
  if (isLoading) {
    return (
      <Flex align="center" justify="center" style={{ height: '100%', width: '100%' }}>
        <LottieLoader variant="loader" size={48} showLabel />
      </Flex>
    );
  }

  return (
    <Box style={{ height: '100%', overflowY: 'auto' }}>
      {/* Page content */}
      <Box style={{ padding: '64px 100px', paddingBottom: 80 }}>
        {/* Page header */}
        <Box style={{ marginBottom: 24 }}>
          <Heading size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('workspace.sidebar.nav.labs')}
          </Heading>
          <Text size="2" style={{ color: 'var(--slate-10)', marginTop: 4, display: 'block' }}>
            {t('workspace.labs.manageSubtitle')}
          </Text>
        </Box>

        {/* ── File Upload Limit Section ── */}
        <Box style={{ marginBottom: 20 }}>
          <SettingsSection title={t('workspace.labs.fileUploadLimit')}>
            <Flex direction="column" gap="2">
              <SettingsRow label={t('workspace.labs.fileUploadLimitLabel')} description={t('workspace.labs.fileUploadLimitDescription')}>
                <Flex direction="column" gap="1">
                  <TextField.Root
                    type="number"
                    placeholder={t('workspace.labs.fileUploadLimitPlaceholder')}
                    value={form.fileSizeLimitMb === '' ? '' : String(form.fileSizeLimitMb)}
                    onChange={handleFileSizeLimitChange}
                    color={errors.fileSizeLimitMb ? 'red' : undefined}
                    min={1}
                    max={1000}
                  >
                    <TextField.Slot side="right">
                      <Flex
                        align="center"
                        justify="center"
                        style={{
                          backgroundColor: 'var(--accent-3)',
                          borderRadius: 'var(--radius-1)',
                          padding: '2px 8px',
                          height: 24,
                        }}
                      >
                        <Text size="1" weight="medium" style={{ color: 'var(--accent-11)' }}>
                          {t('units.mb')}
                        </Text>
                      </Flex>
                    </TextField.Slot>
                  </TextField.Root>
                  {errors.fileSizeLimitMb && (
                    <Text size="1" style={{ color: 'var(--red-a11)' }}>
                      {errors.fileSizeLimitMb}
                    </Text>
                  )}
                </Flex>
              </SettingsRow>

              <InfoCallout>
                {t('workspace.labs.callout')}
              </InfoCallout>
            </Flex>
          </SettingsSection>
        </Box>

        {/* ── Platform Configuration note ── */}
        <PlatformConfigNote />
      </Box>

      {/* ── Discard Confirmation Dialog ── */}
      <ConfirmationDialog
        open={discardDialogOpen}
        onOpenChange={setDiscardDialogOpen}
        title={t('workspace.labs.discardDialog.title')}
        message={t('workspace.labs.discardDialog.message')}
        confirmLabel={t('workspace.labs.discardDialog.confirm')}
        cancelLabel={t('workspace.labs.discardDialog.cancel')}
        confirmVariant="danger"
        onConfirm={handleDiscardConfirm}
      />

      {/* ── Settings Save Bar (shown when dirty) ── */}
      <SettingsSaveBar visible={isDirty()} onDiscard={handleDiscard} onSave={handleSave} />
    </Box>
  );
}
