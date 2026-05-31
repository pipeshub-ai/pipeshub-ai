'use client';

import { useEffect, useState } from 'react';
import { Dialog, Flex, Text, Button, Box, RadioGroup, VisuallyHidden } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { useTranslation } from 'react-i18next';
import {
  getReindexMenuLabel,
  type ReindexMenuLabelKey,
} from '../../utils/reindex-label';
import { FOLDER_REINDEX_DEPTH, REINDEX_SELF_DEPTH } from '../../constants';

export type ReindexScopeChoice = 'self' | 'subtree';

export interface ReindexScopeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  itemName: string;
  primaryLabelKey: ReindexMenuLabelKey;
  onConfirm: (depth: number) => Promise<void>;
  isSubmitting?: boolean;
}

export function ReindexScopeDialog({
  open,
  onOpenChange,
  itemName,
  primaryLabelKey,
  onConfirm,
  isSubmitting = false,
}: ReindexScopeDialogProps) {
  const { t } = useTranslation();
  const [scope, setScope] = useState<ReindexScopeChoice>('subtree');

  useEffect(() => {
    if (!open) {
      setScope('subtree');
    }
  }, [open]);

  const primaryLabel = getReindexMenuLabel(
    { icon: 'refresh', labelKey: primaryLabelKey },
    t,
  );

  const handleConfirm = async () => {
    const depth = scope === 'self' ? REINDEX_SELF_DEPTH : FOLDER_REINDEX_DEPTH;
    await onConfirm(depth);
  };

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      {open && (
        <Box
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 999,
            cursor: 'pointer',
          }}
          onClick={() => !isSubmitting && onOpenChange(false)}
        />
      )}
      <Dialog.Content
        style={{
          maxWidth: '28rem',
          padding: 'var(--space-5)',
          zIndex: 1000,
        }}
      >
        <VisuallyHidden>
          <Dialog.Title>{primaryLabel}</Dialog.Title>
        </VisuallyHidden>
        <Flex direction="column" gap="4">
          <Flex direction="column" gap="1">
            <Text size="5" weight="bold">
              {primaryLabel}
            </Text>
            <Text size="2" style={{ color: 'var(--slate-11)' }}>
              {t('reindexScope.description', {
                name: itemName,
                defaultValue: 'Choose what to index for "{{name}}".',
              })}
            </Text>
          </Flex>

          <RadioGroup.Root
            value={scope}
            onValueChange={(v) => setScope(v as ReindexScopeChoice)}
          >
            <Flex direction="column" gap="3">
              <Text as="label" size="2">
                <Flex align="start" gap="2" style={{ cursor: 'pointer' }}>
                  <RadioGroup.Item value="self" style={{ marginTop: 2 }} />
                  <Flex direction="column" gap="0">
                    <Text weight="medium">
                      {t('reindexScope.thisItemOnly', { defaultValue: 'This item only' })}
                    </Text>
                  </Flex>
                </Flex>
              </Text>
              <Text as="label" size="2">
                <Flex align="start" gap="2" style={{ cursor: 'pointer' }}>
                  <RadioGroup.Item value="subtree" style={{ marginTop: 2 }} />
                  <Flex direction="column" gap="0">
                    <Text weight="medium">
                      {t('reindexScope.withChildren', {
                        defaultValue: 'This item and all children',
                      })}
                    </Text>
                  </Flex>
                </Flex>
              </Text>
            </Flex>
          </RadioGroup.Root>

          <Flex justify="end" gap="2">
            <Button
              variant="soft"
              color="gray"
              disabled={isSubmitting}
              onClick={() => onOpenChange(false)}
            >
              {t('common.cancel', { defaultValue: 'Cancel' })}
            </Button>
            <LoadingButton
              loading={isSubmitting}
              onClick={() => void handleConfirm()}
            >
              {primaryLabel}
            </LoadingButton>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
