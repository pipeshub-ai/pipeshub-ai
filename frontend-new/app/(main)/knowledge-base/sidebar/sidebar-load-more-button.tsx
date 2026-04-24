'use client';

import React from 'react';
import { Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

const BUTTON_STYLE: React.CSSProperties = {
  background: 'none',
  border: 'none',
  color: 'var(--olive-9)',
  fontSize: 12,
  padding: 0,
  textAlign: 'left',
};

export type SidebarLoadMoreButtonProps = {
  onClick: () => void;
  disabled?: boolean;
  loading?: boolean;
  /** Merged into outer `Flex` (padding, marginBottom, etc.) */
  flexStyle?: React.CSSProperties;
  /** Override idle label (default: `agentBuilder.loadMore`) */
  idleLabel?: string;
  /** Override loading label (default: `agentBuilder.loadingMore`) */
  loadingLabel?: string;
};

/**
 * Shared “Load more” control for KB / All Records sidebars (olive text link).
 */
export function SidebarLoadMoreButton({
  onClick,
  disabled = false,
  loading = false,
  flexStyle,
  idleLabel,
  loadingLabel,
}: SidebarLoadMoreButtonProps) {
  const { t } = useTranslation();
  const idle = idleLabel ?? t('agentBuilder.loadMore');
  const busy = loadingLabel ?? t('agentBuilder.loadingMore');

  return (
    <Flex
      align="center"
      style={{
        width: '100%',
        minWidth: 0,
        boxSizing: 'border-box',
        ...flexStyle,
      }}
    >
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        style={{
          ...BUTTON_STYLE,
          cursor: disabled ? 'default' : 'pointer',
        }}
      >
        {loading ? busy : idle}
      </button>
    </Flex>
  );
}
