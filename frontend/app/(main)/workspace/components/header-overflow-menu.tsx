'use client';

import React from 'react';
import { DropdownMenu } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { IconMenuItem } from '@/app/components/ui/icon-menu-item';

export interface HeaderOverflowItem {
  label: string;
  icon: string;
  onSelect: () => void;
}

/** Collapses secondary header actions into one menu on phones. */
export function HeaderOverflowMenu({ items }: { items: HeaderOverflowItem[] }) {
  const { t } = useTranslation();
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger>
        <button
          type="button"
          className="touch-hit"
          aria-label={t('common.moreOptions')}
          style={{
            appearance: 'none',
            margin: 0,
            padding: 0,
            border: '1px solid var(--gray-a4)',
            borderRadius: 'var(--radius-2)',
            background: 'transparent',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 32,
            height: 32,
            cursor: 'pointer',
            flexShrink: 0,
          }}
        >
          <MaterialIcon name="more_horiz" size={16} color="var(--gray-11)" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Content align="end">
        {items.map((item) => (
          <IconMenuItem key={item.label} icon={item.icon} onSelect={item.onSelect}>
            {item.label}
          </IconMenuItem>
        ))}
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}
