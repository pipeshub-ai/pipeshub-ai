'use client';

import React from 'react';
import { IconButton, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';
import { useChatStore } from '../../store';
import { setPanelOpen, usePanelOpen } from './answer-panel-store';

// Clipped: where the icon font can't load, the glyph's name must not spill over its neighbours.
const GLYPH_BUTTON: React.CSSProperties = { width: 'var(--space-5)', height: 'var(--space-5)', padding: 0, margin: 0, borderRadius: 'var(--radius-2)', overflow: 'hidden' };

/**
 * The top bar's Context opener: a 24px glyph button, the size of the bar's other controls. It stays in the bar while
 * the column is open, pressed, and pressing it again closes the column.
 */
export function ContextToggle({ open }: { open: boolean }) {
  const { t } = useTranslation();
  return (
    <Tooltip content={open ? t('chat.answerPanel.close') : t('chat.answerPanel.openerHint')} delayDuration={200}>
      <IconButton
        size="1"
        variant="ghost"
        color="gray"
        aria-label={t('chat.answerPanel.opener')}
        aria-pressed={open}
        data-testid="context-toggle"
        onClick={() => setPanelOpen(!open)}
        style={{ ...GLYPH_BUTTON, backgroundColor: open ? 'var(--fill-muted)' : undefined }}
      >
        <MaterialIcon name="stacks" size={16} color={open ? 'var(--text-primary)' : 'var(--text-secondary)'} />
      </IconButton>
    </Tooltip>
  );
}

/** The cross in the column's header: the column is a card, and a card closes. */
export function PanelClose() {
  const { t } = useTranslation();
  const label = t('chat.answerPanel.close');
  return (
    <Tooltip content={label} delayDuration={200}>
      <IconButton size="1" variant="ghost" color="gray" aria-label={label} data-testid="panel-close" onClick={() => setPanelOpen(false)} style={GLYPH_BUTTON}>
        <MaterialIcon name="close" size={16} color="var(--text-secondary)" />
      </IconButton>
    </Tooltip>
  );
}

/** The Context opener for a one-person or agent chat: from 1024 px, once the chat has a question for the column to describe. */
export function ContextButton({ slotId }: { slotId: string | null }) {
  const open = usePanelOpen();
  const wide = !useIsBelowDesktop();
  const asked = useChatStore((s) => Boolean(slotId && s.slots[slotId]?.messages.some((m) => m.role === 'user')));
  return wide && asked ? <ContextToggle open={open} /> : null;
}
