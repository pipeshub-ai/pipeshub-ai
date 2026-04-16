'use client';

import React, { useState, useEffect } from 'react';
import { Dialog, Flex, Text, TextField, Button, Box, VisuallyHidden } from '@radix-ui/themes';
import { FolderIcon } from '@/app/components/ui';
import { useTranslation } from 'react-i18next';

export interface CreateFolderDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (name: string, description: string) => void;
  isCreating?: boolean;
  isCollection?: boolean;
  parentFolderName?: string;
}

export function CreateFolderDialog({
  open,
  onOpenChange,
  onSubmit,
  isCreating = false,
  isCollection = false,
  parentFolderName,
}: CreateFolderDialogProps) {
  const { t } = useTranslation();
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');

  // Reset form when dialog closes
  useEffect(() => {
    if (!open) {
      setTitle('');
      setDescription('');
    }
  }, [open]);

  const handleSubmit = () => {
    if (title.trim()) {
      onSubmit(title.trim(), description.trim());
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && title.trim() && !isCreating) {
      handleSubmit();
    }
  };

  const isCreateDisabled = !title.trim() || isCreating;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      {/* Dark overlay matching Figma design */}
      {open && (
        <Box
          style={{
            position: 'fixed',
            inset: 0,
            // backgroundColor: 'rgba(28, 32, 36, 0.5)',
            zIndex: 999,
            cursor: 'pointer',
          }}
          onClick={() => onOpenChange(false)}
        />
      )}
      <Dialog.Content
        style={{
          maxWidth: '432px',
          width: '100%',
          padding: 'var(--space-6)',
          zIndex: 1000,
        }}
      >
        <VisuallyHidden>
          <Dialog.Title>{isCollection ? t('dialog.createCollection') : t('dialog.newFolder')}</Dialog.Title>
        </VisuallyHidden>
        <Flex direction="column" gap="6">
          {/* Header */}
          <Flex direction="column" gap="1">
            <Text size="5" weight="bold">
              {isCollection ? t('dialog.createCollection') : t('dialog.newFolder')}
            </Text>
            {parentFolderName && (
              <Flex align="center" gap="1">
                <Text size="2" style={{ color: 'var(--slate-11)' }}>
                  {t('dialog.in')}
                </Text>
                <FolderIcon variant="default" size={16} color="var(--emerald-11)" />
                <Text size="2" style={{ color: 'var(--slate-11)' }}>
                  {parentFolderName}
                </Text>
              </Flex>
            )}
          </Flex>

          {/* Form Fields */}
          <Flex direction="column" gap="5">
            {/* Title Field */}
            <Flex direction="column" gap="2">
              <Text size="2">{t('form.title')}</Text>
              <TextField.Root
                size="2"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="eg: Engineering"
                autoFocus
              />
            </Flex>

            {/* Description Field */}
            <Flex direction="column" gap="2">
              <Text size="2">{t('form.description')}</Text>
              <TextField.Root
                size="2"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="eg: Company repository for all the developers"
              />
            </Flex>
          </Flex>

          {/* Action Buttons */}
          <Flex justify="end" gap="2">
            <Button
              variant="outline"
              color="gray"
              size="2"
              onClick={() => onOpenChange(false)}
              disabled={isCreating}
              style={{cursor: 'pointer'}}
            >
              {t('action.cancel')}
            </Button>
            <Button
              variant={isCreateDisabled ? 'soft' : 'solid'}
              // @ts-expect-error - Radix color prop doesn't accept CSS variable strings
              color="--accent-9"
              size="2"
              onClick={handleSubmit}
              disabled={isCreateDisabled}
              style={{cursor: 'pointer'}}
            >
              {isCreating ? t('action.creating') : t('action.create')}
            </Button>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
