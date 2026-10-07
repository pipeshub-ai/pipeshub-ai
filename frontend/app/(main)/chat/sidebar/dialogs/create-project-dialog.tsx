'use client';

import React, { useState, useCallback, useEffect, useRef } from 'react';
import { Box, Button, Callout, Dialog, Flex, Text, TextArea, TextField, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { ProjectApi } from '@/chat/project-api';
import type { ProjectDetail } from '@/chat/project-types';

export interface CreateProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (project: ProjectDetail) => void;
}

function extractApiError(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { message?: string } } })?.response?.data?.message;
  if (typeof detail === 'string' && detail.trim()) return detail.trim();
  if (e instanceof Error && e.message) return e.message;
  return fallback;
}

export function CreateProjectDialog({ open, onOpenChange, onCreated }: CreateProjectDialogProps) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [instructions, setInstructions] = useState('');
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nameError, setNameError] = useState(false);
  const createRef = useRef(false);

  useEffect(() => {
    if (!open) {
      setName('');
      setDescription('');
      setInstructions('');
      setCreating(false);
      setError(null);
      setNameError(false);
      createRef.current = false;
    }
  }, [open]);

  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (!next && creating) return;
      onOpenChange(next);
    },
    [creating, onOpenChange],
  );

  const handleCreate = useCallback(async () => {
    if (createRef.current) return;
    const trimmedName = name.trim();
    if (!trimmedName) {
      setNameError(true);
      return;
    }
    setNameError(false);
    setError(null);
    createRef.current = true;
    setCreating(true);
    try {
      const project = await ProjectApi.create({
        name: trimmedName,
        ...(description.trim() ? { description: description.trim() } : {}),
        ...(instructions.trim() ? { instructions: instructions.trim() } : {}),
      });
      onCreated(project);
      onOpenChange(false);
    } catch (e: unknown) {
      setError(extractApiError(e, t('chat.projects.createDialog.title')));
      createRef.current = false;
    } finally {
      setCreating(false);
    }
  }, [name, description, instructions, onCreated, onOpenChange, t]);

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Content
        style={{ maxWidth: '28rem', width: '100%', padding: 'var(--space-5)' }}
        onInteractOutside={(e) => e.preventDefault()}
      >
        <VisuallyHidden>
          <Dialog.Title>{t('chat.projects.createDialog.title')}</Dialog.Title>
        </VisuallyHidden>

        <Flex direction="column" gap="4">
          <Flex align="center" gap="3">
            <Box
              style={{
                width: 36,
                height: 36,
                borderRadius: 'var(--radius-2)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                background: 'var(--accent-3)',
                border: '1px solid var(--accent-6)',
                flexShrink: 0,
              }}
            >
              <MaterialIcon name="folder" size={20} style={{ color: 'var(--accent-11)' }} />
            </Box>
            <Text size="4" weight="bold" style={{ color: 'var(--olive-12)' }}>
              {t('chat.projects.createDialog.title')}
            </Text>
          </Flex>

          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--olive-12)' }}>
              {t('chat.projects.createDialog.nameLabel')}
            </Text>
            <TextField.Root
              placeholder={t('chat.projects.createDialog.namePlaceholder')}
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                if (nameError) setNameError(false);
                if (error) setError(null);
              }}
              disabled={creating}
              autoFocus
              maxLength={100}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) void handleCreate();
              }}
              style={nameError ? { outline: '2px solid var(--red-8)' } : {}}
            />
            {nameError && (
              <Text size="1" style={{ color: 'var(--red-11)' }}>
                {t('chat.projects.createDialog.nameRequired')}
              </Text>
            )}
          </Flex>

          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--olive-12)' }}>
              {t('chat.projects.createDialog.descriptionLabel')}
            </Text>
            <TextArea
              placeholder={t('chat.projects.createDialog.descriptionPlaceholder')}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              disabled={creating}
              rows={2}
              maxLength={1000}
            />
          </Flex>

          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--olive-12)' }}>
              {t('chat.projects.createDialog.instructionsLabel')}
            </Text>
            <TextArea
              placeholder={t('chat.projects.createDialog.instructionsPlaceholder')}
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              disabled={creating}
              rows={3}
              maxLength={8000}
            />
          </Flex>

          {error && (
            <Callout.Root color="red" variant="soft" size="2">
              <Callout.Icon>
                <MaterialIcon name="error" size={16} />
              </Callout.Icon>
              <Callout.Text size="2">{error}</Callout.Text>
            </Callout.Root>
          )}

          <Flex gap="2" justify="end">
            <Button
              type="button"
              variant="soft"
              color="gray"
              size="2"
              onClick={() => onOpenChange(false)}
              disabled={creating}
            >
              {t('action.cancel')}
            </Button>
            <LoadingButton
              type="button"
              size="2"
              color="jade"
              onClick={() => void handleCreate()}
              loading={creating}
              loadingLabel={t('chat.projects.createDialog.creating')}
            >
              {t('chat.projects.createDialog.create')}
            </LoadingButton>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
