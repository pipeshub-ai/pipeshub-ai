'use client';

import React, { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Button, Flex, Text, TextArea } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { ConfirmationDialog } from '../../components/confirmation-dialog';
import { formatDateTime } from '../../harness-ui/format';
import { isHarnessError, refusalText, toHarnessError } from '../../harness-ui/refusal';
import { RELEASE_DEFAULTS } from '@/lib/contracts/harness/generated/core/policy-data';
import { MEMORY_REFUSALS, type Memory, type ReviewDecision } from '../api';

const MAX_CHARS = RELEASE_DEFAULTS.memory.maxChars;

export interface MemoryActions {
  edit: (content: string) => Promise<void>;
  review: (decision: ReviewDecision) => Promise<void>;
  remove: () => Promise<void>;
}

/** One memory: what it says, where it applies, and what its viewer may decide. */
export function MemoryRow({ memory, actions }: { memory: Memory; actions: MemoryActions }) {
  const { t, i18n } = useTranslation();
  const id = useId();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(memory.content);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<'remove' | 'reject' | null>(null);

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (failure) {
      setError(refusalText(t, MEMORY_REFUSALS, isHarnessError(failure) ? failure : toHarnessError(failure, true)));
    } finally {
      setBusy(false);
    }
  };

  const reviewable = memory.status !== 'accepted';

  return (
    <Flex
      direction="column"
      gap="2"
      role="group"
      aria-label={t('memories.rowLabel', { date: formatDateTime(memory.createdAt, i18n.language) })}
      data-testid="memory-row"
      style={{ borderBottom: '1px solid var(--olive-3)', paddingBottom: 'var(--space-3)' }}
    >
      <Flex gap="2" align="center" wrap="wrap">
        <Badge variant="soft" color={memory.status === 'accepted' ? 'green' : 'amber'}>
          {t(`memories.status.${memory.status}`)}
        </Badge>
        <Badge variant="soft" color="gray">
          {t(`memories.kind.${memory.kind}`)}
        </Badge>
        <Badge variant="soft" color="gray">
          {t(`memories.scope.${memory.scope}`)}
        </Badge>
        {memory.origin === 'user' && (
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {t('memories.editedByYou')}
          </Text>
        )}
      </Flex>

      {editing ? (
        <Flex direction="column" gap="2">
          <Text as="label" htmlFor={`${id}-content`} size="2" weight="medium">
            {t('memories.content')}
          </Text>
          <TextArea id={`${id}-content`} value={draft} maxLength={MAX_CHARS} rows={3} onChange={(e) => setDraft(e.target.value)} />
          <Flex gap="2">
            <LoadingButton
              type="button"
              loading={busy}
              disabled={draft.trim() === '' || draft === memory.content}
              onClick={() =>
                void run(async () => {
                  await actions.edit(draft);
                  setEditing(false);
                })
              }
            >
              {t('common.save')}
            </LoadingButton>
            <Button
              type="button"
              variant="soft"
              color="gray"
              disabled={busy}
              onClick={() => {
                setDraft(memory.content);
                setEditing(false);
                setError(null);
              }}
            >
              {t('common.cancel')}
            </Button>
          </Flex>
        </Flex>
      ) : (
        <Text size="2" style={{ color: 'var(--slate-12)', whiteSpace: 'pre-wrap' }}>
          {memory.content}
        </Text>
      )}

      {!memory.labelComplete && (
        <Text size="1" role="note" style={{ color: 'var(--amber-11)' }}>
          {t('memories.unknownOrigin')}
        </Text>
      )}

      {reviewable && memory.followedOutsideContent && (
        <Text size="1" role="note" style={{ color: 'var(--amber-11)' }}>
          {t('memories.followedOutsideContent')}
        </Text>
      )}

      {!editing && (
        <Flex gap="2" wrap="wrap">
          {reviewable && (
            <>
              <Button type="button" disabled={busy} onClick={() => void run(() => actions.review('accepted'))}>
                {t('memories.actions.accept')}
              </Button>
              <Button type="button" variant="soft" color="red" disabled={busy} onClick={() => setConfirm('reject')}>
                {t('memories.actions.reject')}
              </Button>
            </>
          )}
          <Button
            type="button"
            variant="soft"
            disabled={busy}
            onClick={() => {
              setDraft(memory.content);
              setEditing(true);
            }}
          >
            {t('memories.actions.edit')}
          </Button>
          {!reviewable && (
            <Button type="button" variant="soft" color="red" disabled={busy} onClick={() => setConfirm('remove')}>
              {t('memories.actions.delete')}
            </Button>
          )}
        </Flex>
      )}

      {error && (
        <Text size="1" role="alert" style={{ color: 'var(--red-11)' }}>
          {error}
        </Text>
      )}

      <ConfirmationDialog
        open={confirm !== null}
        onOpenChange={(open) => !open && setConfirm(null)}
        title={t(confirm === 'reject' ? 'memories.confirmReject.title' : 'memories.confirmDelete.title')}
        message={t(confirm === 'reject' ? 'memories.confirmReject.message' : 'memories.confirmDelete.message')}
        confirmLabel={t(confirm === 'reject' ? 'memories.actions.reject' : 'memories.actions.delete')}
        isLoading={busy}
        onConfirm={() => {
          const chosen = confirm;
          setConfirm(null);
          void run(() => (chosen === 'reject' ? actions.review('rejected') : actions.remove()));
        }}
      />
    </Flex>
  );
}
