'use client';

import React from 'react';
import { Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

/**
 * The sidebar's "Recents" fold header (QA-CLASSIC-FU-1): a button that says whether the list it controls is open,
 * reachable by keyboard and named "Recents" for a screen reader, drawn as the plain heading it replaced.
 */
export function RecentsToggle({
  collapsed,
  onToggle,
  controls,
  mark,
}: {
  collapsed: boolean;
  onToggle: () => void;
  controls: string;
  /** The strongest mark of the folded chats (CJ14-FU-2), drawn beside the chevron. */
  mark?: React.ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      aria-expanded={!collapsed}
      aria-controls={controls}
      data-testid="recents-toggle"
      onClick={onToggle}
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        width: '100%',
        height: 32,
        padding: '0 var(--space-3)',
        flexShrink: 0,
        cursor: 'pointer',
        borderRadius: 'var(--radius-2)',
        userSelect: 'none',
        border: 0,
        background: 'none',
        font: 'inherit',
        margin: 0,
      }}
    >
      <Text style={{ fontSize: 13, fontWeight: 600, color: 'var(--slate-12)', lineHeight: 1 }}>{t('chat.recents')}</Text>
      <span style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
        {mark}
        <MaterialIcon
          name="chevron_right"
          size={16}
          color="var(--slate-11)"
          style={{ transform: collapsed ? 'rotate(0deg)' : 'rotate(90deg)', transition: 'transform 0.2s ease', display: 'block' }}
        />
      </span>
    </button>
  );
}
