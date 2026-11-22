'use client';

import React from 'react';
import { Flex, Box, Text, Button, DropdownMenu } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useTranslation } from 'react-i18next';

export interface EntityPaginationProps {
  /** Current page (1-based) */
  page: number;
  /** Items per page */
  limit: number;
  /** Total number of items */
  totalCount: number;
  /** Called when page changes */
  onPageChange: (page: number) => void;
  /** Called when items per page changes (optional) */
  onLimitChange?: (limit: number) => void;
}

/** 28px pager controls: ghost buttons without Radix's negative margin, page and size in a bordered pill */
const PAGER_BUTTON: React.CSSProperties = {
  margin: 0,
  height: 28,
  boxSizing: 'border-box',
  paddingInline: 'var(--space-2)',
  gap: 'var(--space-1)',
};
const PAGER_PILL: React.CSSProperties = {
  minWidth: 28,
  height: 28,
  padding: '0 var(--space-2)',
  boxSizing: 'border-box',
  borderRadius: 'var(--radius-1)',
  border: '1px solid var(--olive-4)',
  backgroundColor: 'var(--olive-2)',
};

/**
 * EntityPagination — pagination footer for entity list pages.
 *
 * Matches the Figma design:
 * - Left: "Showing X-Y of Z items"
 * - Right: "< Previous | page | Next > | limit selector"
 */
export function EntityPagination({
  page,
  limit,
  totalCount,
  onPageChange,
  onLimitChange,
}: EntityPaginationProps) {
  const { t } = useTranslation();

  const totalPages = Math.max(1, Math.ceil(totalCount / limit));
  const from = totalCount === 0 ? 0 : (page - 1) * limit + 1;
  const to = Math.min(page * limit, totalCount);
  const hasPrev = page > 1;
  const hasNext = page < totalPages;

  return (
    <Flex
      justify="between"
      align="center"
      wrap="wrap"
      gap="2"
      style={{
        // 16px around the count text on every side (12px + the 28px controls' centring)
        padding: 'var(--space-3) var(--space-4)',
        borderTop: '1px solid var(--olive-3)',
        flexShrink: 0,
        width: '100%',
        rowGap: 'var(--space-2)',
      }}
    >
      {/* Left: showing count (hidden with no rows; the table's empty state says so) */}
      {totalCount > 0 ? (
        <Text size="2" style={{ color: 'var(--olive-11)' }}>
          {t('workspace.pagination.showing', { from, to, count: totalCount })}
        </Text>
      ) : (
        <span />
      )}

      {/* Right: pagination controls */}
      <Flex gap="2" align="center">
        {/* Previous */}
        <Button className="touch-hit" variant="ghost" size="2" color="gray" disabled={!hasPrev} onClick={() => onPageChange(page - 1)} style={PAGER_BUTTON}>
          <MaterialIcon name="chevron_left" size={16} />
          {t('workspace.pagination.previous')}
        </Button>

        {/* Page number */}
        <Flex align="center" justify="center" style={PAGER_PILL}>
          <Text size="1" weight="medium" style={{ color: 'var(--olive-12)' }}>
            {page}
          </Text>
        </Flex>

        {/* Next */}
        <Button className="touch-hit" variant="ghost" size="2" color="gray" disabled={!hasNext} onClick={() => onPageChange(page + 1)} style={PAGER_BUTTON}>
          {t('workspace.pagination.next')}
          <MaterialIcon name="chevron_right" size={16} />
        </Button>

        {/* Separator */}
        <Box style={{ width: '1px', height: '20px', backgroundColor: 'var(--olive-4)' }} />

        {/* Limit selector */}
        {onLimitChange && (
          <DropdownMenu.Root>
            <DropdownMenu.Trigger>
              <Flex
                data-testid="page-size-trigger"
                align="center"
                gap="1"
                className="entity-pager-select touch-hit"
                style={{ ...PAGER_PILL, cursor: 'pointer', gap: 'var(--space-1)' }}
              >
                <Text size="1" weight="medium" style={{ color: 'var(--olive-12)' }}>
                  {limit}
                </Text>
                <MaterialIcon name="expand_less" size={14} color="var(--olive-11)" />
              </Flex>
            </DropdownMenu.Trigger>
            <DropdownMenu.Content align="end" sideOffset={4}>
              {[10, 25, 50, 100].map((value) => (
                <DropdownMenu.Item
                  key={value}
                  onClick={() => onLimitChange(value)}
                >
                  {t('common.pagination.perPage', { value })}
                </DropdownMenu.Item>
              ))}
            </DropdownMenu.Content>
          </DropdownMenu.Root>
        )}
      </Flex>
    </Flex>
  );
}
