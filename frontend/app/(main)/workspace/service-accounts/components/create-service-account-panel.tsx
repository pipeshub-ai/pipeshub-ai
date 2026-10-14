'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Flex, Text, TextArea, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FormField, WorkspaceRightPanel } from '../../components';
import { useToastStore } from '@/lib/store/toast-store';
import { ServiceAccountsApi } from '../api';

const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const SLUG_MIN = 3;
const SLUG_MAX = 48;

export interface CreateServiceAccountPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: () => void;
}

export function CreateServiceAccountPanel({
  open,
  onOpenChange,
  onCreated,
}: CreateServiceAccountPanelProps) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);

  const [slug, setSlug] = useState('');
  const [fullName, setFullName] = useState('');
  const [description, setDescription] = useState('');
  const [slugError, setSlugError] = useState<string | undefined>(undefined);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (!open) {
      setSlug('');
      setFullName('');
      setDescription('');
      setSlugError(undefined);
      setIsSubmitting(false);
    }
  }, [open]);

  const validateSlug = useCallback(
    (value: string): boolean => {
      const trimmed = value.trim().toLowerCase();
      if (trimmed.length < SLUG_MIN || trimmed.length > SLUG_MAX) {
        setSlugError(t('workspace.serviceAccounts.create.slugLengthError'));
        return false;
      }
      if (!SLUG_PATTERN.test(trimmed)) {
        setSlugError(t('workspace.serviceAccounts.create.slugFormatError'));
        return false;
      }
      setSlugError(undefined);
      return true;
    },
    [t]
  );

  const handleCreate = useCallback(async () => {
    if (!validateSlug(slug)) return;
    setIsSubmitting(true);
    try {
      await ServiceAccountsApi.create({
        slug: slug.trim().toLowerCase(),
        fullName: fullName.trim() || slug.trim(),
        description: description.trim() || undefined,
      });
      addToast({
        variant: 'success',
        title: t('workspace.serviceAccounts.create.successTitle'),
        duration: 3000,
      });
      onCreated?.();
      onOpenChange(false);
    } catch (err: unknown) {
      addToast({
        variant: 'error',
        title: t('workspace.serviceAccounts.create.errorTitle'),
        description: err instanceof Error ? err.message : undefined,
        duration: 5000,
      });
    } finally {
      setIsSubmitting(false);
    }
  }, [slug, fullName, description, validateSlug, addToast, t, onCreated, onOpenChange]);

  return (
    <WorkspaceRightPanel
      open={open}
      onOpenChange={onOpenChange}
      title={t('workspace.serviceAccounts.create.title')}
      icon={<MaterialIcon name="precision_manufacturing" size={18} />}
      primaryLabel={t('workspace.serviceAccounts.create.submit')}
      secondaryLabel={t('common.cancel')}
      primaryDisabled={slug.trim().length === 0}
      primaryLoading={isSubmitting}
      onPrimaryClick={() => void handleCreate()}
      onSecondaryClick={() => onOpenChange(false)}
    >
      <Flex direction="column" gap="4">
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          {t('workspace.serviceAccounts.create.intro')}
        </Text>

        <FormField
          label={t('workspace.serviceAccounts.create.slugLabel')}
          required
          error={slugError}
        >
          <TextField.Root
            value={slug}
            placeholder="nightly-sync"
            onChange={(e) => {
              setSlug(e.target.value);
              if (slugError) setSlugError(undefined);
            }}
            onBlur={() => slug.trim() && validateSlug(slug)}
          />
          <Text size="1" style={{ color: 'var(--slate-10)' }}>
            {t('workspace.serviceAccounts.create.slugHelp')}
          </Text>
        </FormField>

        <FormField label={t('workspace.serviceAccounts.create.nameLabel')} optional>
          <TextField.Root
            value={fullName}
            placeholder={t('workspace.serviceAccounts.create.namePlaceholder')}
            onChange={(e) => setFullName(e.target.value)}
          />
        </FormField>

        <FormField
          label={t('workspace.serviceAccounts.create.descriptionLabel')}
          optional
        >
          <TextArea
            value={description}
            rows={3}
            placeholder={t('workspace.serviceAccounts.create.descriptionPlaceholder')}
            onChange={(e) => setDescription(e.target.value)}
          />
        </FormField>

        <Flex
          gap="2"
          style={{
            backgroundColor: 'var(--olive-2)',
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            padding: 'var(--space-3)',
          }}
        >
          <MaterialIcon name="info" size={16} color="var(--slate-10)" />
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {t('workspace.serviceAccounts.create.note')}
          </Text>
        </Flex>
      </Flex>
    </WorkspaceRightPanel>
  );
}
