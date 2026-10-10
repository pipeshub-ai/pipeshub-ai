'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { TipId } from '@/app/(main)/notifications/api';
import { useTip } from '@/lib/store/tips-store';

export interface CoachmarkProps {
  tipId: TipId;
  /** The moment the tip applies (for example, a send just happened). The tip also needs to be unseen. */
  active: boolean;
  message: React.ReactNode;
}

/**
 * A once-per-user tip row. The server-stored `tipsSeen` list decides, and dismissal marks the tip seen right away.
 * It sits in the composer block's flow, so it pushes the thread up instead of covering the newest message.
 */
export function Coachmark({ tipId, active, message }: CoachmarkProps) {
  const { t } = useTranslation();
  const { visible, markSeen } = useTip(tipId);
  if (!active || !visible) return null;
  return (
    <Flex
      align="center"
      gap="2"
      role="status"
      data-testid={`coachmark-${tipId}`}
      aria-label={t('chat.mentions.tips.coachmarkLabel', { defaultValue: 'Tip' })}
      style={{
        width: '100%',
        marginBottom: 'var(--space-2)',
        padding: 'var(--space-1) var(--space-2)',
        borderRadius: 'var(--radius-2)',
        background: 'var(--accent-a3)',
        border: '1px solid var(--accent-a6)',
      }}
    >
      <MaterialIcon name="lightbulb" size={16} color="var(--accent-11)" />
      <Text size="2" style={{ flex: 1, minWidth: 0 }}>
        {message}
      </Text>
      <Button size="1" variant="soft" highContrast onClick={markSeen} style={{ flexShrink: 0 }}>
        {t('chat.mentions.tips.gotIt', { defaultValue: 'Got it' })}
      </Button>
    </Flex>
  );
}
