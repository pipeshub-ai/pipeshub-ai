'use client';

import React, { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Dialog, Button, Flex, Box, Text, Callout, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { useToastStore } from '@/lib/store/toast-store';
import { ConnectorsApi } from '../api';

// ========================================
// Types
// ========================================

type VectorStoreOperation = 'cleanup' | 'reindex';

interface OperationCopy {
  label: string;
  description: string;
  buttonLabel: string;
  dialogTitle: string;
  dialogBody: string;
  warning: string;
  confirm: string;
}

/**
 * Literal the operator types to unlock each action. Deliberately not translated:
 * a fixed token reads the same in every locale, so the control does not silently
 * weaken when the UI language changes.
 */
const CONFIRMATION_TOKENS: Record<VectorStoreOperation, string> = {
  cleanup: 'DELETE',
  reindex: 'REINDEX',
};

// ========================================
// Component
// ========================================

export function VectorStoreActions() {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const [pendingOperation, setPendingOperation] =
    useState<VectorStoreOperation | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [typedConfirmation, setTypedConfirmation] = useState('');

  const confirmationToken = pendingOperation
    ? CONFIRMATION_TOKENS[pendingOperation]
    : null;
  const isConfirmationSatisfied =
    confirmationToken === null ||
    typedConfirmation.trim().toUpperCase() === confirmationToken;

  const closeDialog = useCallback(() => {
    setPendingOperation(null);
    setTypedConfirmation('');
  }, []);

  const copy: Record<VectorStoreOperation, OperationCopy> = {
    cleanup: {
      label: t('workspace.connectors.vectorStore.cleanup.label', 'Clean up embeddings'),
      description: t(
        'workspace.connectors.vectorStore.cleanup.rowDescription',
        'Drop all embeddings and recreate the vector collection empty. Records are not deleted.',
      ),
      buttonLabel: t('workspace.connectors.vectorStore.cleanup.buttonLabel', 'Clean up'),
      dialogTitle: t(
        'workspace.connectors.vectorStore.cleanup.title',
        'Delete all embeddings?',
      ),
      dialogBody: t(
        'workspace.connectors.vectorStore.cleanup.body',
        'This drops the shared vector collection and recreates it empty.',
      ),
      warning: t(
        'workspace.connectors.vectorStore.cleanup.warning',
        'Search will stop returning results for everyone and stays broken until a reindex has finished. Reindexing large deployments can take hours. Your records are not deleted — only their embeddings.',
      ),
      confirm: t('workspace.connectors.vectorStore.cleanup.confirm', 'Delete embeddings'),
    },
    reindex: {
      label: t('workspace.connectors.vectorStore.reindex.label', 'Reindex embeddings'),
      description: t(
        'workspace.connectors.vectorStore.reindex.rowDescription',
        'Re-embed every record from stored content. Sources are not re-downloaded or parsed again.',
      ),
      buttonLabel: t('workspace.connectors.vectorStore.reindex.buttonLabel', 'Reindex'),
      dialogTitle: t('workspace.connectors.vectorStore.reindex.title', 'Reindex all records?'),
      dialogBody: t(
        'workspace.connectors.vectorStore.reindex.body',
        'This re-embeds every record from its stored content. Sources are not downloaded or parsed again.',
      ),
      warning: t(
        'workspace.connectors.vectorStore.reindex.warning',
        'This can take a long time on large deployments and runs in the background.',
      ),
      confirm: t('workspace.connectors.vectorStore.reindex.confirm', 'Start reindex'),
    },
  };

  const runOperation = useCallback(
    async (operation: VectorStoreOperation) => {
      setIsRunning(true);
      try {
        if (operation === 'cleanup') {
          await ConnectorsApi.cleanupVectorStore();
        } else {
          await ConnectorsApi.reindexVectorStore();
        }
        closeDialog();
        addToast({
          variant: 'success',
          title: t(
            `workspace.connectors.vectorStore.${operation}.started`,
            operation === 'cleanup'
              ? 'Cleanup started'
              : 'Reindex started',
          ),
          description: t(
            `workspace.connectors.vectorStore.${operation}.startedDetail`,
            operation === 'cleanup'
              ? 'The collection is being recreated. Search stays empty until a reindex completes.'
              : 'Records are being re-embedded in the background. This can take a while.',
          ),
        });
      } catch {
        // apiClient surfaces the server message (409 when a job is already
        // running); keep the dialog open so the operator can retry.
      } finally {
        setIsRunning(false);
      }
    },
    [addToast, closeDialog, t],
  );

  const active = pendingOperation ? copy[pendingOperation] : null;

  const operations: VectorStoreOperation[] = ['reindex', 'cleanup'];

  return (
    <>
      {operations.map((op) => {
        const item = copy[op];
        const isDestructive = op === 'cleanup';
        return (
          <Flex
            key={op}
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
                {item.label}
              </Text>
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
                {item.description}
              </Text>
            </Box>
            <Button
              size="2"
              variant="soft"
              color={isDestructive ? 'red' : 'gray'}
              style={{ flexShrink: 0, cursor: 'pointer', minHeight: 44 }}
              onClick={() => setPendingOperation(op)}
            >
              <MaterialIcon
                name={isDestructive ? 'delete_sweep' : 'refresh'}
                size={16}
              />
              {item.buttonLabel}
            </Button>
          </Flex>
        );
      })}

      <Dialog.Root
        open={pendingOperation !== null}
        onOpenChange={(open) => {
          if (!open && !isRunning) closeDialog();
        }}
      >
        <Dialog.Content maxWidth="460px">
          {active && (
            <>
              <Dialog.Title>{active.dialogTitle}</Dialog.Title>
              <Dialog.Description size="2" mb="3">
                {active.dialogBody}
              </Dialog.Description>

              <Callout.Root
                color={pendingOperation === 'cleanup' ? 'red' : 'gray'}
                size="1"
                mb="4"
              >
                <Callout.Icon>
                  <MaterialIcon
                    name={pendingOperation === 'cleanup' ? 'warning' : 'schedule'}
                    size={16}
                  />
                </Callout.Icon>
                <Callout.Text>
                  <Text size="2">{active.warning}</Text>
                </Callout.Text>
              </Callout.Root>

              {confirmationToken && (
                <Flex direction="column" gap="2" mb="4">
                  <Text size="2" style={{ color: 'var(--gray-11)' }}>
                    {t(
                      'workspace.connectors.vectorStore.typePrompt',
                      'Type {{token}} to confirm.',
                      { token: confirmationToken },
                    )}
                  </Text>
                  <TextField.Root
                    size="2"
                    value={typedConfirmation}
                    placeholder={confirmationToken}
                    disabled={isRunning}
                    autoFocus
                    onChange={(e) => setTypedConfirmation(e.target.value)}
                    onKeyDown={(e) => {
                      if (
                        e.key === 'Enter' &&
                        isConfirmationSatisfied &&
                        !isRunning &&
                        pendingOperation
                      ) {
                        runOperation(pendingOperation);
                      }
                    }}
                  />
                </Flex>
              )}

              <Flex gap="3" justify="end">
                <Dialog.Close>
                  <Button variant="soft" color="gray" disabled={isRunning}>
                    {t('common.cancel', 'Cancel')}
                  </Button>
                </Dialog.Close>
                <LoadingButton
                  loading={isRunning}
                  disabled={!isConfirmationSatisfied}
                  color={pendingOperation === 'cleanup' ? 'red' : undefined}
                  onClick={() => pendingOperation && runOperation(pendingOperation)}
                >
                  {active.confirm}
                </LoadingButton>
              </Flex>
            </>
          )}
        </Dialog.Content>
      </Dialog.Root>
    </>
  );
}
