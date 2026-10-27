'use client';

import React, { useCallback, useEffect, useId, useState } from 'react';
import { Button, Dialog, Flex, TextArea, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { ProjectApi } from '@/chat/project-api';
import type { ProjectDetail } from '@/chat/project-types';
import { toast } from '@/lib/store/toast-store';
import { CharCount, LabeledField, PROJECT_DESCRIPTION_MAX, PROJECT_NAME_MAX } from './project-form-fields';

export interface EditProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: Pick<ProjectDetail, '_id' | 'name' | 'description'>;
  onSaved: (project: ProjectDetail) => void;
}

/** Renames a project and edits its description (editor or owner). */
export function EditProjectDialog({ open, onOpenChange, project, onSaved }: EditProjectDialogProps) {
  const { t } = useTranslation();
  const fieldId = useId();
  const nameId = `${fieldId}-name`;
  const nameErrorId = `${fieldId}-name-error`;
  const nameCountId = `${fieldId}-name-count`;
  const descriptionId = `${fieldId}-description`;
  const descriptionCountId = `${fieldId}-description-count`;
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description ?? '');
  const [nameError, setNameError] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      setName(project.name);
      setDescription(project.description ?? '');
      setNameError(false);
    }
  }, [open, project.name, project.description]);

  const handleSave = useCallback(async () => {
    if (saving) return;
    const trimmedName = name.trim();
    if (!trimmedName) {
      setNameError(true);
      return;
    }
    const trimmedDescription = description.trim();
    const patch: { name?: string; description?: string } = {};
    if (trimmedName !== project.name) patch.name = trimmedName;
    if (trimmedDescription !== (project.description ?? '').trim()) patch.description = trimmedDescription;
    if (Object.keys(patch).length === 0) {
      onOpenChange(false);
      return;
    }
    setSaving(true);
    try {
      const updated = await ProjectApi.update(project._id, patch);
      onSaved(updated);
      toast.success(t('chat.projects.editDialog.saved'));
      onOpenChange(false);
    } catch {
      toast.error(t('chat.projects.editDialog.saveFailed'));
    } finally {
      setSaving(false);
    }
  }, [saving, name, description, project, onSaved, onOpenChange, t]);

  return (
    <Dialog.Root open={open} onOpenChange={(next) => !saving && onOpenChange(next)}>
      <Dialog.Content style={{ maxWidth: '28rem', width: '100%', padding: 'var(--space-5)' }}>
        <Dialog.Title size="4">{t('chat.projects.editDialog.title')}</Dialog.Title>
        <Flex direction="column" gap="3">
          <LabeledField
            id={nameId}
            label={t('chat.projects.createDialog.nameLabel')}
            error={nameError ? t('chat.projects.createDialog.nameRequired') : null}
            errorId={nameErrorId}
            counter={<CharCount id={nameCountId} length={name.length} max={PROJECT_NAME_MAX} />}
          >
            <TextField.Root
              id={nameId}
              value={name}
              required
              maxLength={PROJECT_NAME_MAX}
              disabled={saving}
              autoFocus
              aria-invalid={nameError || undefined}
              aria-describedby={`${nameErrorId} ${nameCountId}`}
              onChange={(e) => {
                setName(e.target.value);
                if (nameError) setNameError(false);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void handleSave();
              }}
            />
          </LabeledField>
          <LabeledField
            id={descriptionId}
            label={t('chat.projects.createDialog.descriptionLabel')}
            counter={<CharCount id={descriptionCountId} length={description.length} max={PROJECT_DESCRIPTION_MAX} />}
          >
            <TextArea
              id={descriptionId}
              value={description}
              placeholder={t('chat.projects.workspace.descriptionPlaceholder')}
              rows={4}
              maxLength={PROJECT_DESCRIPTION_MAX}
              disabled={saving}
              aria-describedby={descriptionCountId}
              onChange={(e) => setDescription(e.target.value)}
              style={{ resize: 'vertical' }}
            />
          </LabeledField>
          <Flex gap="2" justify="end" mt="2">
            <Button type="button" variant="soft" color="gray" onClick={() => onOpenChange(false)} disabled={saving}>
              {t('action.cancel')}
            </Button>
            <LoadingButton
              type="button"
              color="jade"
              onClick={() => void handleSave()}
              loading={saving}
              loadingLabel={t('chat.projects.workspace.saving')}
            >
              {t('chat.projects.workspace.save')}
            </LoadingButton>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
