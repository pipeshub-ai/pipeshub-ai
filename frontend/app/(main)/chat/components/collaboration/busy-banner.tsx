'use client';

import { useEffect, useState } from 'react';
import { Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useUserStore } from '@/lib/store/user-store';
import { toast } from '@/lib/store/toast-store';
import { conversationErrorMessage } from '../../utils/conversation-errors';
import { useChatStore } from '../../store';
import { ChatApi } from '../../api';
import { useConversationAccess } from '../../hooks/use-conversation-access';
import { useSendWhenFree } from '../../hooks/use-send-when-free';
import { cancelQueuedSend } from '../../utils/queued-send';
import { sendQueuedMessage } from '../../utils/send-when-free';
import type { ActiveRunDto } from '../../collaboration-types';

const boxStyle = {
  width: '100%',
  padding: 'var(--space-2) var(--space-3)',
  borderRadius: 'var(--radius-3)',
  background: 'var(--slate-3)',
  border: '1px solid var(--slate-6)',
  marginBottom: 'var(--space-2)',
} as const;

/** One announcement per run: the same run seen on every poll must not be read out again. */
function runKey(run: ActiveRunDto | null): string | null {
  return run ? (run.runId ?? `${run.userId}@${run.startedAt}`) : null;
}

interface BusyBannerProps {
  slotId: string;
}

/**
 * Shows who is running a turn in this chat, the message waiting for them to finish (send when free),
 * and the "new messages above" notice after a rejected send. Always mounted while the chat is open:
 * it also hosts `useSendWhenFree`, which must outlive the busy state it reacts to.
 */
export function BusyBanner({ slotId }: BusyBannerProps) {
  const { t } = useTranslation();
  const access = useConversationAccess(slotId);
  useSendWhenFree(slotId);
  const me = useUserStore((s) => s.profile?.userId);
  const activeRun = useChatStore((s) => s.slots[slotId]?.activeRun ?? null);
  const queued = useChatStore((s) => s.slots[slotId]?.queuedSend ?? null);
  const changed = useChatStore((s) => s.slots[slotId]?.changedNotice ?? null);
  const isStreaming = useChatStore((s) => s.slots[slotId]?.isStreaming ?? false);
  const convId = useChatStore((s) => s.slots[slotId]?.convId ?? null);
  const agentId = useChatStore((s) => s.slots[slotId]?.threadAgentId ?? null);

  const key = runKey(activeRun);
  const showBusy = access.collabEnabled && !access.accessLost && !isStreaming && activeRun !== null;
  const name = activeRun?.displayName || t('chat.collab.busy.someone');
  const inAnotherTab = Boolean(me) && activeRun?.userId === me;

  const [announcement, setAnnouncement] = useState('');
  useEffect(() => {
    if (!showBusy || key === null) {
      setAnnouncement('');
      return;
    }
    setAnnouncement(
      inAnotherTab ? t('chat.collab.busy.askingOtherTab') : t('chat.collab.busy.announce', { name }),
    );
    // Only a new run is announced; `name` and `t` are read at that moment.
  }, [showBusy, key]);

  if (!access.collabEnabled || access.accessLost) return null;

  const stop = () => {
    if (convId && activeRun?.runId) {
      void ChatApi.cancelStream(convId, activeRun.runId, agentId).catch((error: unknown) => {
        toast.error(conversationErrorMessage(t, error));
      });
    }
  };

  const resend = () => {
    if (!changed?.pending) return;
    useChatStore.getState().updateSlot(slotId, { queuedSend: changed.pending, changedNotice: null });
    sendQueuedMessage(slotId);
  };

  const editChanged = () => {
    const pending = changed?.pending;
    useChatStore.getState().updateSlot(slotId, {
      changedNotice: null,
      ...(pending ? { composerRestore: pending.query } : {}),
    });
  };

  return (
    <>
      <div aria-live="polite" role="status" data-testid="busy-announcer" style={visuallyHidden}>
        {announcement}
      </div>

      {changed && (
        <Flex align="center" gap="3" wrap="wrap" data-testid="changed-notice" style={boxStyle}>
          <MaterialIcon name="forum" size={16} color="var(--slate-11)" />
          <Text size="2" style={{ color: 'var(--slate-11)', flex: '1 1 12rem', minWidth: 0 }}>
            {t('chat.collab.sync.newMessages', { count: changed.count })}
          </Text>
          {changed.pending && (
            <>
              <Button size="1" variant="soft" onClick={editChanged}>
                {t('chat.collab.sync.edit')}
              </Button>
              <Button size="1" onClick={resend}>
                {t('chat.collab.sync.sendAgain')}
              </Button>
            </>
          )}
        </Flex>
      )}

      {showBusy && (
        <Flex align="center" gap="3" wrap="wrap" data-testid="busy-banner" style={boxStyle}>
          <MaterialIcon name="hourglass_top" size={16} color="var(--slate-11)" />
          <Text size="2" style={{ color: 'var(--slate-11)', flex: '1 1 12rem', minWidth: 0 }}>
            {queued
              ? t('chat.collab.busy.waiting', { name })
              : inAnotherTab
                ? t('chat.collab.busy.askingOtherTab')
                : t('chat.collab.busy.asking', { name })}
          </Text>
          {queued && (
            <Button size="1" variant="soft" onClick={() => cancelQueuedSend(slotId, { restore: true })}>
              {t('chat.collab.busy.cancelQueued')}
            </Button>
          )}
          {!queued && access.canSend && activeRun?.runId && (
            <Button size="1" variant="soft" color="gray" onClick={stop}>
              {t('chat.collab.busy.stop')}
            </Button>
          )}
        </Flex>
      )}
    </>
  );
}

const visuallyHidden = {
  position: 'absolute',
  width: 1,
  height: 1,
  margin: -1,
  padding: 0,
  overflow: 'hidden',
  clip: 'rect(0, 0, 0, 0)',
  whiteSpace: 'nowrap',
  border: 0,
} as const;
