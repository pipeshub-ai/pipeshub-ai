'use client';

import React, { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TipId } from '@/app/(main)/notifications/api';
import { Coachmark } from '../coachmark';
import type { MentionRef } from '../composer/composer-input.types';
import { classifyResponder, type RespondMode, type SessionKind } from '@/chat/mentions/classify';

type SendTip = Extract<TipId, 'mentions.firstSharedSend' | 'mentions.firstNote' | 'mentions.firstAgentMention'>;

interface TipInput {
  mentions: readonly MentionRef[] | undefined;
  shared: boolean;
  respondMode: RespondMode;
  sessionKind: SessionKind;
}

/** Which first-time tip a just-sent message earns. At most one; "note" is whatever the responder classifier says. */
export function tipForSend({ mentions, shared, respondMode, sessionKind }: TipInput): SendTip | null {
  const refs = mentions ?? [];
  if (classifyResponder({ mentions: refs, respondMode, sessionKind }) === 'note') return 'mentions.firstNote';
  if (refs.some((m) => m.type === 'agent')) return 'mentions.firstAgentMention';
  return shared ? 'mentions.firstSharedSend' : null;
}

/**
 * First-send coachmarks around the composer. `wrap` is the identity with the mentions flag off, so the
 * composer's DOM is untouched.
 */
export function useSendCoachmarks({
  enabled,
  shared,
  participantCount,
  respondMode,
  sessionKind,
  paused = false,
}: {
  enabled: boolean;
  /** Something above the composer needs the user first (the add-to-chat prompt, the busy banner); the tip waits instead of covering it. */
  paused?: boolean;
  shared: boolean;
  respondMode: RespondMode;
  sessionKind: SessionKind;
  participantCount?: number;
}) {
  const { t } = useTranslation();
  const [tip, setTip] = useState<SendTip | null>(null);

  const onSent = useCallback(
    (mentions: readonly MentionRef[] | undefined) => {
      if (enabled) setTip(tipForSend({ mentions, shared, respondMode, sessionKind }));
    },
    [enabled, shared, respondMode, sessionKind],
  );

  const message =
    tip === 'mentions.firstAgentMention'
      ? t('chat.mentions.tips.firstAgentMention')
      : tip === 'mentions.firstNote'
        ? t('chat.mentions.tips.firstNote')
        : participantCount !== undefined
          ? t('chat.mentions.tips.firstSharedSend', { count: participantCount })
          : t('chat.mentions.tips.firstSharedSendUnknown');

  const wrap = (children: React.ReactNode): React.ReactNode =>
    enabled ? (
      <Coachmark tipId={tip ?? 'mentions.firstSharedSend'} active={tip !== null && !paused} message={message}>
        {children}
      </Coachmark>
    ) : (
      children
    );

  return { onSent, wrap };
}
