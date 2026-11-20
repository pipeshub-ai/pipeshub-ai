'use client';

import React, { createContext, useContext } from 'react';
import { Button, IconButton, Text, Tooltip } from '@radix-ui/themes';
import Image from 'next/image';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

interface ShareButtonProps {
  onClick: () => void;
}

/**
 * How Share draws itself: `text` (a glyph and "Share", as in a page header) or `icon` (a 24px icon button with the
 * name in a tooltip, as in a chat's top bar). A surface that holds a share group sets it for everything inside.
 */
export const ShareButtonAppearance = createContext<'text' | 'icon'>('text');

export function ShareButton({ onClick }: ShareButtonProps) {
  const { t } = useTranslation();
  const appearance = useContext(ShareButtonAppearance);

  if (appearance === 'icon') {
    return (
      <Tooltip content={t('action.share')} delayDuration={200}>
        <IconButton
          size="1"
          variant="ghost"
          color="gray"
          aria-label={t('action.share')}
          onClick={onClick}
          className="ghost-box touch-size"
          style={{ flexShrink: 0, borderRadius: 'var(--radius-2)', cursor: 'pointer' }}
        >
          <MaterialIcon name="ios_share" size={16} color="var(--text-secondary)" />
        </IconButton>
      </Tooltip>
    );
  }

  return (
    <Button
      size="1"
      variant="ghost"
      color="gray"
      onClick={onClick}
      style={{
        padding: '0 var(--space-2)',
        gap: 'var(--space-1)',
        borderRadius: 'var(--radius-1)',
        cursor: 'pointer',
        flexShrink: 0,
        color: 'var(--text-secondary)',
        height: '20px',
      }}
    >
      <Image
        src="/icons/share/share-2.svg"
        alt=""
        width={14}
        height={14}
      />
      <Text
        size="1"
        weight="regular"
        style={{ whiteSpace: 'nowrap', color: 'var(--text-secondary)', letterSpacing: '0.04px' }}
      >
        {t('action.share')}
      </Text>
    </Button>
  );
}
