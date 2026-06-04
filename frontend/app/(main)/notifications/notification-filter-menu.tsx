'use client';

import { useState } from 'react';
import { DropdownMenu, Flex, Text, IconButton, Tooltip, Box } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useTranslation } from 'react-i18next';
import type { NotificationListFilter } from './api';

/** Raised above the notifications panel (9100) when portaled to document.body. */
export const NOTIFICATIONS_PANEL_TOOLTIP_CLASS = 'ph-notifications-panel-tooltip';

interface NotificationFilterMenuProps {
  value: NotificationListFilter;
  onChange: (filter: NotificationListFilter) => void;
}

const FILTER_OPTIONS: { id: NotificationListFilter; icon: string }[] = [
  { id: 'all', icon: 'all_inbox' },
  { id: 'unread', icon: 'mark_email_unread' },
  { id: 'archived', icon: 'archive' },
];

/**
 * Filter icon + dropdown (All / Only Unread), matching inbox filter popover layout.
 */
export function NotificationFilterMenu({ value, onChange }: NotificationFilterMenuProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  const labels: Record<NotificationListFilter, string> = {
    all: t('notifications.filterAll', { defaultValue: 'All' }),
    unread: t('notifications.filterOnlyUnread', { defaultValue: 'Only Unread' }),
    archived: t('notifications.filterArchived', { defaultValue: 'Archived' }),
  };

  const filterTooltip = t('notifications.filterTooltip', {
    defaultValue: 'Filter: {{filter}}',
    filter: labels[value],
  });

  return (
    <Box style={{ display: 'inline-flex', flexShrink: 0, position: 'relative' }}>
      <DropdownMenu.Root open={open} onOpenChange={setOpen} modal={false}>
        <Tooltip
          className={NOTIFICATIONS_PANEL_TOOLTIP_CLASS}
          content={filterTooltip}
          side="bottom"
        >
          <DropdownMenu.Trigger>
            <IconButton
              variant="ghost"
              size="1"
              color="gray"
              aria-label={t('notifications.filter', { defaultValue: 'Filter notifications' })}
              onClick={(e) => e.stopPropagation()}
              style={{
                cursor: 'pointer',
                backgroundColor: open ? 'var(--olive-5)' : undefined,
              }}
            >
              <MaterialIcon name="filter_list" size={18} color="var(--slate-11)" />
            </IconButton>
          </DropdownMenu.Trigger>
        </Tooltip>
        <DropdownMenu.Content
        data-ph-notifications-filter-menu=""
        side="bottom"
        align="end"
        sideOffset={4}
        onClick={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
        style={{
          minWidth: 200,
          padding: 'var(--space-1)',
          zIndex: 9200,
        }}
      >
        {FILTER_OPTIONS.map((option) => {
          const selected = value === option.id;
          return (
            <DropdownMenu.Item
              key={option.id}
              onSelect={(e) => {
                e.preventDefault();
                onChange(option.id);
                setOpen(false);
              }}
              style={{ padding: 'var(--space-2) var(--space-3)' }}
            >
              <Flex align="center" justify="between" gap="3" style={{ width: '100%' }}>
                <Flex align="center" gap="2">
                  <MaterialIcon name={option.icon} size={18} color="var(--slate-11)" />
                  <Text size="2" style={{ color: 'var(--slate-12)' }}>
                    {labels[option.id]}
                  </Text>
                </Flex>
                {selected && (
                  <MaterialIcon name="check" size={18} color="var(--slate-12)" />
                )}
              </Flex>
            </DropdownMenu.Item>
          );
        })}
        </DropdownMenu.Content>
      </DropdownMenu.Root>
    </Box>
  );
}
