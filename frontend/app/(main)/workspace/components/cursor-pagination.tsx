'use client';

import React from 'react';
import { Flex, Box, Text, DropdownMenu } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

export interface CursorPaginationProps {
  /** 1-based position of the first row on this page */
  from: number;
  /** Rows on this page */
  count: number;
  hasNewer: boolean;
  hasOlder: boolean;
  onNewer: () => void;
  onOlder: () => void;
  limit?: number;
  onLimitChange?: (limit: number) => void;
}

const LIMITS = [10, 25, 50, 100];

function PageButton({
  enabled,
  onClick,
  children,
}: {
  enabled: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={!enabled}
      onClick={onClick}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 2,
        minHeight: 44,
        padding: '0 var(--space-1)',
        border: 0,
        background: 'none',
        font: 'inherit',
        color: 'var(--slate-11)',
        cursor: enabled ? 'pointer' : 'not-allowed',
        opacity: enabled ? 1 : 0.5,
      }}
    >
      {children}
    </button>
  );
}

/**
 * The EntityPagination footer for lists paged by a cursor, which have no
 * cheap total: "Showing 1–25" and Newer / Older.
 */
export function CursorPagination({
  from,
  count,
  hasNewer,
  hasOlder,
  onNewer,
  onOlder,
  limit,
  onLimitChange,
}: CursorPaginationProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();

  return (
    <Flex
      justify={isMobile ? 'center' : 'between'}
      align="center"
      wrap="wrap"
      gap="2"
      style={{
        padding: 'var(--space-2) var(--space-4)',
        borderTop: '1px solid var(--slate-4)',
        backgroundColor: 'var(--slate-1)',
        flexShrink: 0,
        width: '100%',
      }}
    >
      <Text size="2" style={{ color: 'var(--slate-11)', flexBasis: isMobile ? '100%' : undefined, textAlign: 'center' }}>
        {count === 0 ? '' : t('workspace.pagination.showingRange', { from, to: from + count - 1 })}
      </Text>

      <Flex gap="3" align="center">
        <PageButton enabled={hasNewer} onClick={onNewer}>
          <MaterialIcon name="chevron_left" size={16} />
          <Text size="2">{t('workspace.pagination.newer')}</Text>
        </PageButton>
        <PageButton enabled={hasOlder} onClick={onOlder}>
          <Text size="2">{t('workspace.pagination.older')}</Text>
          <MaterialIcon name="chevron_right" size={16} />
        </PageButton>

        {onLimitChange && limit !== undefined && (
          <>
            <Box style={{ width: '1px', height: '20px', backgroundColor: 'var(--slate-6)' }} />
            <DropdownMenu.Root>
              <DropdownMenu.Trigger>
                <button
                  type="button"
                  aria-label={t('workspace.pagination.perPage', { count: limit })}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                    minHeight: 32,
                    padding: 'var(--space-1) var(--space-2)',
                    border: 0,
                    font: 'inherit',
                    color: 'var(--slate-12)',
                    backgroundColor: 'var(--slate-3)',
                    borderRadius: 'var(--radius-2)',
                    cursor: 'pointer',
                  }}
                >
                  <Text size="2">{limit}</Text>
                  <MaterialIcon name="expand_less" size={14} color="var(--slate-11)" />
                </button>
              </DropdownMenu.Trigger>
              <DropdownMenu.Content align="end" sideOffset={4}>
                {LIMITS.map((value) => (
                  <DropdownMenu.Item key={value} onClick={() => onLimitChange(value)}>
                    {t('workspace.pagination.perPage', { count: value })}
                  </DropdownMenu.Item>
                ))}
              </DropdownMenu.Content>
            </DropdownMenu.Root>
          </>
        )}
      </Flex>
    </Flex>
  );
}
