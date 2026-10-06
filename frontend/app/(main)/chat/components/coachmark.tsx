'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Flex, Popover, Text } from '@radix-ui/themes';
import { Popover as PopoverPrimitive } from 'radix-ui';
import type { TipId } from '@/app/(main)/notifications/api';
import { useTip } from '@/lib/store/tips-store';
import { useToastElementInset } from '@/lib/toast-safe-area';

export interface CoachmarkProps {
  tipId: TipId;
  /** The moment the tip applies (for example, a send just happened). The tip also needs to be unseen. */
  active: boolean;
  message: React.ReactNode;
  side?: 'top' | 'bottom';
  children: React.ReactNode;
}

/**
 * A once-per-user popover. The server-stored `tipsSeen` list decides, and any dismissal marks the tip seen
 * right away. The popover is anchored to the composer, not triggered by it: a Trigger would stamp
 * button/aria-haspopup attributes onto a wrapper div around the editor. It never takes focus, so it does not interrupt typing.
 */
export function Coachmark({ tipId, active, message, side = 'top', children }: CoachmarkProps) {
  const { t } = useTranslation();
  const { visible, markSeen } = useTip(tipId);
  const open = active && visible;
  // Portaled above the composer, so the composer's own toast inset does not cover it.
  const [content, setContent] = useState<HTMLDivElement | null>(null);
  useToastElementInset(open ? content : null);
  return (
    <Popover.Root open={open} onOpenChange={(next) => next || markSeen()}>
      <PopoverPrimitive.Anchor asChild>
        <div style={{ width: '100%' }}>{children}</div>
      </PopoverPrimitive.Anchor>
      <Popover.Content
        ref={setContent}
        side={side}
        align="start"
        sideOffset={8}
        size="1"
        role="status"
        data-testid={`coachmark-${tipId}`}
        aria-label={t('chat.mentions.tips.coachmarkLabel', { defaultValue: 'Tip' })}
        style={{ maxWidth: 320 }}
        onOpenAutoFocus={(e) => e.preventDefault()}
        onCloseAutoFocus={(e) => e.preventDefault()}
      >
        <Flex direction="column" gap="2" align="start">
          <Text size="2">{message}</Text>
          <Popover.Close>
            <Button size="1" variant="soft" highContrast>
              {t('chat.mentions.tips.gotIt', { defaultValue: 'Got it' })}
            </Button>
          </Popover.Close>
        </Flex>
      </Popover.Content>
    </Popover.Root>
  );
}
