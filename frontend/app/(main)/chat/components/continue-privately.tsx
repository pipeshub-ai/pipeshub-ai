'use client';

import { useEffect, useRef, useState } from 'react';
import { Box, Button, Dialog, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { useCommandStore } from '@/lib/store/command-store';
import { ChatApi } from '../api';
import { BUILTIN_BUS } from '../builtin-commands';

interface ContinuePrivatelyProps {
  conversationId: string;
  /** The owner's copy also takes the conversation's workspace files. */
  isOwner: boolean;
  /** Opens the private copy once it exists. */
  onContinued: (conversationId: string) => void;
  /** `own`: the owner's own chat, forked by `/fork` only (no header button, no other people's turns). */
  mode?: ForkMode;
  /** An agent conversation is forked through its agent's route. */
  agentKey?: string | null;
}

export type ForkMode = 'shared' | 'own';

/**
 * Where a fork can start: any participant of a shared non-agent chat ("Continue privately"), and the owner of
 * any of their own chats, agent or not (`/fork`). The server takes both (owner or participant).
 */
export function forkMode(chat: {
  served: boolean;
  conversationId: string | null;
  isOwner: boolean | null;
  isShared: boolean;
  agentThread: boolean;
}): ForkMode | null {
  if (!chat.served || !chat.conversationId) return null;
  if (chat.isShared && !chat.agentThread) return 'shared';
  return chat.isOwner === true ? 'own' : null;
}

const newRequestKey = (): string =>
  typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `fork-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;

/**
 * "Continue privately" (SS3): copies a shared conversation into one only the
 * caller sees. The confirmation says what is copied; one request key per
 * confirmation, so a double click or a retry opens the same copy.
 */
export function ContinuePrivately({
  conversationId,
  isOwner,
  onContinued,
  mode = 'shared',
  agentKey,
}: ContinuePrivatelyProps) {
  const own = mode === 'own';
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const requestKey = useRef<string | null>(null);

  const openDialog = () => {
    requestKey.current = newRequestKey();
    setFailed(false);
    setOpen(true);
  };
  // `/fork` (SLASH2) opens this same confirmation; while this is not mounted, `/fork` is greyed out.
  const openRef = useRef(openDialog);
  openRef.current = openDialog;
  useEffect(() => {
    const { register, unregister } = useCommandStore.getState();
    register(BUILTIN_BUS.continuePrivately, () => openRef.current());
    return () => unregister(BUILTIN_BUS.continuePrivately);
  }, []);

  const confirm = async () => {
    if (busy || requestKey.current === null) return;
    setBusy(true);
    setFailed(false);
    try {
      const { conversationId: copy } = await ChatApi.continuePrivately(conversationId, requestKey.current, agentKey ?? undefined);
      setOpen(false);
      onContinued(copy);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  const copied = [
    t('chat.continuePrivately.copiesYourTurns'),
    t('chat.continuePrivately.copiesSources'),
    ...(isOwner ? [t('chat.continuePrivately.copiesWorkspace')] : []),
  ];

  return (
    <>
      {!own && <Button
        size="1"
        variant="ghost"
        color="gray"
        onClick={openDialog}
        style={{ gap: 'var(--space-1)', height: '20px', padding: '0 var(--space-2)', color: 'var(--slate-11)' }}
      >
        <MaterialIcon name="lock" size={14} />
        <Text size="1" style={{ whiteSpace: 'nowrap', color: 'var(--slate-11)' }}>
          {t('chat.continuePrivately.action')}
        </Text>
      </Button>}
      <Dialog.Root open={open} onOpenChange={(next) => !busy && setOpen(next)}>
        <Dialog.Content style={{ maxWidth: '32rem', width: '100%', padding: 'var(--space-5)' }}>
          <Dialog.Title>{t(own ? 'chat.continuePrivately.forkTitle' : 'chat.continuePrivately.title')}</Dialog.Title>
          <Dialog.Description size="2" style={{ color: 'var(--slate-11)' }}>
            {t(own ? 'chat.continuePrivately.forkDescription' : 'chat.continuePrivately.description')}
          </Dialog.Description>
          <Flex direction="column" gap="2" mt="4">
            <Text size="2" weight="medium">
              {t('chat.continuePrivately.copiedHeading')}
            </Text>
            <Box asChild>
              <ul style={{ margin: 0, paddingLeft: 'var(--space-5)' }}>
                {copied.map((line) => (
                  <li key={line}>
                    <Text size="2">{line}</Text>
                  </li>
                ))}
              </ul>
            </Box>
            <Text size="2" weight="medium" mt="2">
              {t('chat.continuePrivately.notCopiedHeading')}
            </Text>
            <Box asChild>
              <ul style={{ margin: 0, paddingLeft: 'var(--space-5)' }}>
                {!own && (
                  <li>
                    <Text size="2">{t('chat.continuePrivately.othersBecomePlaceholders')}</Text>
                  </li>
                )}
                <li>
                  <Text size="2">{t('chat.continuePrivately.noToolResults')}</Text>
                </li>
              </ul>
            </Box>
            {failed && (
              <Text size="2" color="red" role="alert">
                {t('chat.continuePrivately.failed')}
              </Text>
            )}
          </Flex>
          <Flex gap="2" justify="end" mt="5">
            <Button variant="outline" color="gray" onClick={() => setOpen(false)} disabled={busy}>
              {t('action.cancel')}
            </Button>
            <LoadingButton onClick={confirm} loading={busy} loadingLabel={t('chat.continuePrivately.working')}>
              {t(own ? 'chat.continuePrivately.forkConfirm' : 'chat.continuePrivately.confirm')}
            </LoadingButton>
          </Flex>
        </Dialog.Content>
      </Dialog.Root>
    </>
  );
}
