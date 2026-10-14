'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import {
  Box,
  Flex,
  Text,
  Heading,
  TextField,
  IconButton,
  Switch,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  ConfirmationDialog,
  SettingsSaveBar,
  SettingsSection,
  SettingsRow,
} from '../components';
import { useToastStore } from '@/lib/store/toast-store';
import { useLabsStore } from './store';
import { LabsApi, bytesToMb, mbToBytes, type AvailableFlag } from './api';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { VectorStoreActions } from '../connectors/components/vector-store-actions';

// ========================================
// Local Sub-components
// ========================================

/** Accent-tinted info callout used as a standalone banner */
function InfoCallout({ children }: { children: React.ReactNode }) {
  return (
    <Flex
      align="center"
      gap="3"
      style={{
        backgroundColor: 'var(--accent-a2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: '12px',
      }}
    >
      <IconButton variant="soft" size="1" style={{ flexShrink: 0, cursor: 'default', background: 'var(--slate-a2)' }} tabIndex={-1}>
        <MaterialIcon name="info" size={16} color="var(--accent-11)" />
      </IconButton>
      <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: '16px' }}>
        {children}
      </Text>
    </Flex>
  );
}

function FeatureFlagRow({
  flag,
  checked,
  onCheckedChange,
}: {
  flag: AvailableFlag;
  checked: boolean;
  onCheckedChange: (value: boolean) => void;
}) {
  return (
    <Flex
      align="center"
      justify="between"
      style={{ width: '100%', gap: 'var(--space-4)' }}
    >
      <Box style={{ flex: 1 }}>
        <Text
          size="2"
          weight="medium"
          style={{ color: 'var(--slate-12)', display: 'block' }}
        >
          {flag.label || flag.key}
        </Text>
        {flag.description && (
          <Text
            size="1"
            style={{
              color: 'var(--slate-11)',
              display: 'block',
              marginTop: 2,
              lineHeight: '16px',
              fontWeight: 300,
            }}
          >
            {flag.description}
          </Text>
        )}
      </Box>
      <Switch
        checked={checked}
        onCheckedChange={onCheckedChange}
        aria-label={flag.label || flag.key}
        style={{ flexShrink: 0, cursor: 'pointer' }}
        size="1"
      />
    </Flex>
  );
}

function AdvancedFlagsAccordion({
  flags,
  values,
  onFlagChange,
}: {
  flags: AvailableFlag[];
  values: Record<string, boolean>;
  onFlagChange: (key: string, value: boolean) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [hovered, setHovered] = useState(false);

  return (
    <Flex
      direction="column"
      style={{
        width: '100%',
        border: '1px solid var(--olive-4)',
        borderRadius: 'var(--radius-2)',
        backgroundColor: 'var(--olive-3)',
        overflow: 'hidden',
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        aria-expanded={open}
        style={{
          appearance: 'none',
          margin: 0,
          padding: '12px 14px',
          border: 'none',
          background: hovered || open ? 'var(--olive-4)' : 'transparent',
          cursor: 'pointer',
          width: '100%',
          textAlign: 'left',
        }}
      >
        <Flex align="center" justify="between" gap="3">
          <Flex align="center" gap="3" style={{ flex: 1, minWidth: 0 }}>
            <Flex
              align="center"
              justify="center"
              style={{
                width: 28,
                height: 28,
                borderRadius: 'var(--radius-2)',
                backgroundColor: 'var(--accent-a3)',
                flexShrink: 0,
              }}
            >
              <MaterialIcon name="tune" size={16} color="var(--accent-11)" />
            </Flex>
            <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {t('workspace.labs.featureFlags.advancedTitle', 'Show advanced feature flags')}
              </Text>
              <Text size="1" style={{ color: 'var(--slate-11)', fontWeight: 300, lineHeight: '16px' }}>
                {t(
                  'workspace.labs.featureFlags.advancedSubtitle',
                  'Internal flags that most workspaces can leave at their defaults.'
                )}
              </Text>
            </Flex>
          </Flex>
          <MaterialIcon
            name={open ? 'expand_less' : 'expand_more'}
            size={20}
            color="var(--slate-11)"
            style={{ flexShrink: 0 }}
          />
        </Flex>
      </button>
      {open && (
        <Flex
          direction="column"
          gap="5"
          style={{
            padding: 14,
            borderTop: '1px solid var(--olive-4)',
            backgroundColor: 'var(--olive-2)',
          }}
        >
          {flags.map((flag) => (
            <FeatureFlagRow
              key={flag.key}
              flag={flag}
              checked={!!values[flag.key]}
              onCheckedChange={(value) => onFlagChange(flag.key, value)}
            />
          ))}
        </Flex>
      )}
    </Flex>
  );
}

/** Bottom info note (Platform Configuration) */
function PlatformConfigNote() {
  const { t } = useTranslation();
  return (
    <Flex
      align="center"
      gap="3"
      style={{
        backgroundColor: 'var(--accent-a2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 'var(--space-4)',
      }}
    >
      <IconButton variant="soft" size="1" style={{ flexShrink: 0, cursor: 'default', background: 'var(--slate-a2)' }} tabIndex={-1}>
        <MaterialIcon name="info" size={16} color="var(--accent-11)" />
      </IconButton>
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
  const refreshGlobalFlags = useFeatureFlagsStore((s) => s.fetchFlags);

  // ── Store selectors (must run every render; see Rules of Hooks) ──
  const form = useLabsStore((s) => s.form);
  const savedForm = useLabsStore((s) => s.savedForm);
  const availableFlags = useLabsStore((s) => s.availableFlags);
  const errors = useLabsStore((s) => s.errors);
  const discardDialogOpen = useLabsStore((s) => s.discardDialogOpen);
  const isLoading = useLabsStore((s) => s.isLoading);

  const setFileSizeLimitMb = useLabsStore((s) => s.setFileSizeLimitMb);
  const setFlagValue = useLabsStore((s) => s.setFlagValue);
  const setForm = useLabsStore((s) => s.setForm);
  const markSaved = useLabsStore((s) => s.markSaved);
  const setErrors = useLabsStore((s) => s.setErrors);
  const discardChanges = useLabsStore((s) => s.discardChanges);
  const setDiscardDialogOpen = useLabsStore((s) => s.setDiscardDialogOpen);
  const setLoading = useLabsStore((s) => s.setLoading);
  const isDirty = useLabsStore((s) => s.isDirty);
  const regularFlags = availableFlags.filter((flag) => !flag.advanced);
  const advancedFlags = availableFlags.filter((flag) => !!flag.advanced);

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
        const [settingsResult, flagsResult] = await Promise.allSettled([
          LabsApi.getSettings(),
          LabsApi.getAvailableFlags(),
        ]);

        const settings = settingsResult.status === 'fulfilled' ? settingsResult.value : null;
        const flags = flagsResult.status === 'fulfilled' ? flagsResult.value : [];

        // Merge server-provided flag values with descriptor defaults so every
        // available flag has an effective boolean value in the form.
        const serverFlags = settings?.featureFlags ?? {};
        const featureFlags: Record<string, boolean> = {};
        for (const def of flags) {
          featureFlags[def.key] =
            typeof serverFlags[def.key] === 'boolean'
              ? serverFlags[def.key]
              : !!def.defaultEnabled;
        }

        setForm({
          fileSizeLimitMb: settings ? bytesToMb(settings.fileUploadMaxSizeBytes) : '',
          featureFlags,
        }, flags);
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
      refreshGlobalFlags();

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
  }, [form, savedForm, validate, markSaved, refreshGlobalFlags, addToast]);

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
        <Box style={{ marginBottom: 'var(--space-6)' }}>
          <Heading size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('workspace.sidebar.nav.labs')}
          </Heading>
          <Text size="2" style={{ color: 'var(--slate-10)', marginTop: 'var(--space-1)', display: 'block' }}>
            {t('workspace.labs.manageSubtitle')}
          </Text>
        </Box>

        {/* ── File Upload Limit Section ── */}
        <Box style={{ marginBottom: 'var(--space-5)' }}>
          <Flex direction="column" gap="3">
            <SettingsSection>
              <Flex align="center" justify="between" style={{ width: '100%' }}>
                {/* Label + description */}
                <Box style={{ flex: 1 }}>
                  <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
                    {t('workspace.labs.fileUploadLimit')}
                  </Text>
                  <Text size="1" style={{ color: 'var(--slate-11)', display: 'block', marginTop: 2, lineHeight: '16px', fontWeight: 300 }}>
                    {t('workspace.labs.fileUploadLimitDescription')}
                  </Text>
                </Box>
                {/* Input — fixed 158px, right-aligned */}
                <Flex direction="column" gap="1" style={{ width: 158, flexShrink: 0 }}>
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
                          backgroundColor: 'var(--accent-a3)',
                          borderRadius: 'var(--radius-1)',
                          padding: '2px 8px',
                          height: 18,
                        }}
                      >
                        <Text size="1" weight="medium" style={{ color: 'var(--accent-a11)' }}>
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
              </Flex>
                <InfoCallout>
              {t('workspace.labs.callout')}
            </InfoCallout>
            </SettingsSection>
          </Flex>
        </Box>

        {/* ── Feature Flags Section ── */}
        {availableFlags.length > 0 && (
          <Box style={{ marginBottom: 'var(--space-5)' }}>
            <SettingsSection
              title={t('workspace.labs.featureFlags.title', 'Feature flags')}
              description={t(
                'workspace.labs.featureFlags.subtitle',
                'Toggle experimental capabilities for every agent in this workspace.'
              )}
            >
              {regularFlags.map((flag) => (
                <FeatureFlagRow
                  key={flag.key}
                  flag={flag}
                  checked={!!form.featureFlags[flag.key]}
                  onCheckedChange={(val) => setFlagValue(flag.key, val)}
                />
              ))}
              {advancedFlags.length > 0 && (
                <AdvancedFlagsAccordion
                  flags={advancedFlags}
                  values={form.featureFlags}
                  onFlagChange={setFlagValue}
                />
              )}
            </SettingsSection>
          </Box>
        )}

        {/* ── Vector Store Section ── */}
        {savedForm.featureFlags.ENABLE_VECTOR_STORE_REBUILD === true && (
          <Box style={{ marginBottom: 'var(--space-5)' }}>
            <SettingsSection
              title={t('workspace.labs.vectorStore.title', 'Vector Store')}
              description={t(
                'workspace.labs.vectorStore.subtitle',
                'Delete all embeddings or re-embed every record from stored content.'
              )}
            >
              <VectorStoreActions />
            </SettingsSection>
          </Box>
        )}

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
