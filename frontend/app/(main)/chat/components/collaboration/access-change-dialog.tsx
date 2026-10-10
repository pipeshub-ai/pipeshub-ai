'use client';

import { useEffect, useRef, useState } from 'react';
import { Button, Dialog, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { Spinner } from '@/app/components/ui/spinner';
import { useFeatureFlagsStore, selectCollaborativeChatsEnabled } from '@/lib/store/feature-flags-store';
import { CollaborationApi, conversationErrorStatus } from '../../collaboration-api';
import type {
  AccessChange,
  AccessChangePreview,
  ConversationRef,
  PrincipalRole,
} from '../../collaboration-types';
import { resolvePrincipalNames, type PrincipalNames } from '../../utils/principal-names';

export interface AccessChangeDialogProps {
  /** The change to preview; the dialog is closed while this is null. */
  change: AccessChange | null;
  conversationRef: ConversationRef;
  /** Performs the real change. Called only after a successful preview and an explicit Apply. */
  onApply: () => Promise<void>;
  /** Called on Cancel, Escape and after a successful apply. */
  onClose: () => void;
}

type PreviewState =
  | { status: 'loading' }
  | { status: 'ready'; preview: AccessChangePreview; names: PrincipalNames }
  | { status: 'error'; notFound: boolean };

const CHANGE_DESCRIPTION: Record<string, string> = {
  link: 'chat.collab.access.dialog.descriptionLink',
  unlink: 'chat.collab.access.dialog.descriptionUnlink',
  'visibility:project': 'chat.collab.access.dialog.descriptionVisibilityProject',
  'visibility:private': 'chat.collab.access.dialog.descriptionVisibilityPrivate',
};

function descriptionKey(change: AccessChange): string {
  return CHANGE_DESCRIPTION[change.type === 'visibility' ? `visibility:${change.visibility}` : change.type];
}

/**
 * Shows who would gain access, lose it, or become read-only before a project link, unlink or
 * visibility change (H8). Fails closed: Apply is enabled only once the preview loaded, so a
 * preview error means nothing is applied.
 */
export function AccessChangeDialog({ change, conversationRef, onApply, onClose }: AccessChangeDialogProps) {
  const { t } = useTranslation();
  const enabled = useFeatureFlagsStore(selectCollaborativeChatsEnabled);
  const [state, setState] = useState<PreviewState>({ status: 'loading' });
  const [applying, setApplying] = useState(false);
  const [applyFailed, setApplyFailed] = useState(false);
  const refKey = JSON.stringify(conversationRef);
  const changeKey = change ? JSON.stringify(change) : null;
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!enabled || !change) return;
    const controller = new AbortController();
    setState({ status: 'loading' });
    setApplyFailed(false);
    (async () => {
      try {
        const preview = await CollaborationApi.previewAccessChange(conversationRef, change, controller.signal);
        const all = [...preview.gains, ...preview.loses, ...preview.becomesReadOnly];
        const names = await resolvePrincipalNames(conversationRef, {
          userIds: all.flatMap((p) => (p.userId ? [p.userId] : [])),
          teamIds: all.flatMap((p) => (p.teamId ? [p.teamId] : [])),
        });
        if (!controller.signal.aborted) setState({ status: 'ready', preview, names });
      } catch (error) {
        if (!controller.signal.aborted) setState({ status: 'error', notFound: conversationErrorStatus(error) === 404 });
      }
    })();
    return () => controller.abort();
    // `change` and `conversationRef` are tracked by their serialized keys.
  }, [enabled, changeKey, refKey]);

  if (!enabled || !change) return null;

  const apply = async () => {
    if (state.status !== 'ready' || applying) return;
    setApplying(true);
    setApplyFailed(false);
    try {
      await onApply();
      onClose();
    } catch {
      setApplyFailed(true);
    } finally {
      setApplying(false);
    }
  };

  return (
    <Dialog.Root open onOpenChange={(open) => !open && !applying && onClose()}>
      <Dialog.Content
        style={{ maxWidth: '32rem', width: '100%', padding: 'var(--space-5)' }}
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          cancelRef.current?.focus();
        }}
      >
        <Flex direction="column" gap="4">
          <Flex direction="column" gap="1">
            <Dialog.Title size="5" mb="0">
              {t('chat.collab.access.dialog.title')}
            </Dialog.Title>
            <Dialog.Description size="2" style={{ color: 'var(--slate-11)' }}>
              {t(descriptionKey(change))}
            </Dialog.Description>
          </Flex>

          <div aria-live="polite" role="status" data-testid="access-change-status">
            {state.status === 'loading' && (
              <Flex align="center" gap="2">
                <Spinner size={14} />
                <Text size="2">{t('chat.collab.access.dialog.loading')}</Text>
              </Flex>
            )}
            {state.status === 'error' && (
              <Text size="2" color="red" role="alert">
                {t(state.notFound ? 'chat.collab.access.dialog.projectUnavailable' : 'chat.collab.access.dialog.previewFailed')}
              </Text>
            )}
            {state.status === 'ready' && <PreviewLists preview={state.preview} names={state.names} />}
          </div>

          {applyFailed && (
            <Text size="2" color="red" role="alert">
              {t('chat.collab.access.dialog.applyFailed')}
            </Text>
          )}

          <Flex gap="2" justify="end">
            <Button ref={cancelRef} variant="outline" color="gray" onClick={onClose} disabled={applying}>
              {t('action.cancel')}
            </Button>
            <LoadingButton
              onClick={() => void apply()}
              disabled={state.status !== 'ready'}
              loading={applying}
              loadingLabel={t('chat.collab.access.dialog.applying')}
            >
              {t('chat.collab.access.dialog.apply')}
            </LoadingButton>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}

function PreviewLists({ preview, names }: { preview: AccessChangePreview; names: PrincipalNames }) {
  const { t } = useTranslation();
  const sections: Array<{ id: string; heading: string; rows: PrincipalRole[] }> = [
    { id: 'gains', heading: t('chat.collab.access.dialog.gains'), rows: preview.gains },
    { id: 'loses', heading: t('chat.collab.access.dialog.loses'), rows: preview.loses },
    { id: 'readOnly', heading: t('chat.collab.access.dialog.becomesReadOnly'), rows: preview.becomesReadOnly },
  ].filter((s) => s.rows.length > 0);

  const label = (p: PrincipalRole) =>
    p.userId
      ? (names.users.get(p.userId) ?? t('chat.collab.access.unknownUser'))
      : (names.teams.get(p.teamId ?? '') ?? t('chat.collab.access.unknownTeam'));

  return (
    <Flex direction="column" gap="3">
      {sections.length === 0 && <Text size="2">{t('chat.collab.access.dialog.noChanges')}</Text>}
      {sections.map((s) => (
        <section key={s.id} aria-labelledby={`access-change-${s.id}`} data-testid={`access-change-${s.id}`}>
          <Text as="p" id={`access-change-${s.id}`} size="2" weight="medium" style={{ marginBottom: 4 }}>
            {s.heading}
          </Text>
          <ul style={{ margin: 0, paddingLeft: 'var(--space-5)', maxHeight: 160, overflowY: 'auto' }}>
            {s.rows.map((p) => (
              <li key={p.userId ?? `team:${p.teamId}`}>
                <Text size="2">
                  {t('chat.collab.access.dialog.row', {
                    name: label(p),
                    role: t(`chat.collab.access.role.${p.role}`),
                  })}
                </Text>
              </li>
            ))}
          </ul>
        </section>
      ))}
      {preview.truncated && (
        <Text size="1" style={{ color: 'var(--slate-11)' }} data-testid="access-change-truncated">
          {t('chat.collab.access.dialog.truncated')}
        </Text>
      )}
    </Flex>
  );
}
