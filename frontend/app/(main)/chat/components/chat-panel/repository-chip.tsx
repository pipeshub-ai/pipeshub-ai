'use client';

import { useState } from 'react';
import { Flex, IconButton, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { RepositoryApi, repositoryName, type ChatRepositoryView } from '@/chat/repository-api';
import { heldRepository, useRepositoryStore, type PendingRepository } from '@/chat/repository-store';

/** What the chip shows: the held choice, else a saved repository not yet copied; null when there is none. */
export function chipRepository(
  held: PendingRepository | null,
  saved: ChatRepositoryView | null | undefined,
): { name: string; branch: string | null } | null {
  if (held) return { name: held.fullName, branch: held.baseRef ?? null };
  return saved && !saved.cloned ? { name: repositoryName(saved.url), branch: saved.baseRef } : null;
}

/** Whether the chip shows for this chat, for the composer to leave out an empty chip row. */
export function useRepositoryChipShown(session: { sessionId: string } | null, agentId: string | null): boolean {
  return useRepositoryStore((s) =>
    chipRepository(session ? null : heldRepository(s.pending, agentId), session ? s.views[session.sessionId] : undefined) !== null,
  );
}

/**
 * The repository a chat will work on before its engine has copied it (WP-26), over the composer: "acme/widgets from
 * main". A new chat's is held for its first message; a saved chat's shows until its first engine turn copies it.
 * × drops it.
 */
export function RepositoryChip({
  session,
  agentId,
}: {
  session: { agentId?: string; sessionId: string } | null;
  agentId: string | null;
}) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [busy, setBusy] = useState(false);
  const held = useRepositoryStore((s) => (session ? null : heldRepository(s.pending, agentId)));
  const saved = useRepositoryStore((s) => (session ? s.views[session.sessionId] : undefined));
  const shown = chipRepository(held, saved);
  if (!shown) return null;

  const clear = async () => {
    const store = useRepositoryStore.getState();
    if (!session) {
      store.setPending(null);
      return;
    }
    setBusy(true);
    const answer = await RepositoryApi.remove(session);
    setBusy(false);
    // Refused (the copy was made meanwhile): read the chat's repository again rather than guess.
    if ('ok' in answer) store.setView(session.sessionId, null);
    else void store.load(session);
  };

  return (
    <Flex
      align="center"
      gap="1"
      data-testid="repository-chip"
      style={{
        alignSelf: 'flex-start',
        maxWidth: '100%',
        padding: '0 var(--space-1) 0 var(--space-2)',
        minHeight: isMobile ? 44 : 28,
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--olive-5)',
        background: 'var(--olive-2)',
      }}
    >
      <MaterialIcon name="call_split" size={14} color="var(--slate-11)" />
      <Text size="1" style={{ color: 'var(--slate-12)', overflowWrap: 'anywhere' }}>
        {shown.branch
          ? t('chat.repository.chip', { repository: shown.name, branch: shown.branch })
          : t('chat.repository.chipNoBranch', { repository: shown.name })}
      </Text>
      <IconButton
        size="1"
        variant="ghost"
        color="gray"
        disabled={busy}
        data-testid="repository-chip-clear"
        aria-label={t('chat.repository.clear')}
        onClick={() => void clear()}
        style={isMobile ? { minWidth: 44, minHeight: 44 } : undefined}
      >
        <MaterialIcon name="close" size={14} color="var(--slate-11)" />
      </IconButton>
    </Flex>
  );
}
