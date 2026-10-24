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
        padding: 'var(--space-3) var(--space-4)',
        borderTop: '1px solid var(--slate-4)',
        backgroundColor: 'var(--slate-1)',
        flexShrink: 0,
        width: '100%',
        rowGap: 'var(--space-2)',
      }}
    >
      {/* Left: showing count (hidden with no rows; the table's empty state says so) */}
      {totalCount > 0 ? (
        <Text size="2" style={{ color: 'var(--slate-9)' }}>
          {t('workspace.pagination.showing', { from, to, count: totalCount })}
        </Text>
      ) : (
        <span />
      )}

      {/* Right: pagination controls */}
      <Flex gap="3" align="center">
        {/* Previous */}
        <Button variant="ghost" size="2" color="gray" disabled={!hasPrev} onClick={() => onPageChange(page - 1)}>
          <MaterialIcon name="chevron_left" size={16} />
          {t('workspace.pagination.previous')}
        </Button>

        {/* Page number */}
        <Box
          style={{
            padding: 'var(--space-1) var(--space-3)',
            backgroundColor: 'var(--slate-3)',
            borderRadius: 'var(--radius-2)',
            minWidth: 'var(--space-8)',
            textAlign: 'center',
          }}
        >
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {page}
          </Text>
        </Box>

        {/* Next */}
        <Button variant="ghost" size="2" color="gray" disabled={!hasNext} onClick={() => onPageChange(page + 1)}>
          {t('workspace.pagination.next')}
          <MaterialIcon name="chevron_right" size={16} />
        </Button>

        {/* Separator */}
        <Box style={{ width: '1px', height: '20px', backgroundColor: 'var(--slate-6)' }} />

        {/* Limit selector */}
        {onLimitChange && (
          <DropdownMenu.Root>
            <DropdownMenu.Trigger>
              <Flex
                data-testid="page-size-trigger"
                align="center"
                gap="1"
                style={{
                  cursor: 'pointer',
                  padding: 'var(--space-1) var(--space-2)',
                  backgroundColor: 'var(--slate-3)',
                  borderRadius: 'var(--radius-2)',
                }}
              >
                <Text size="2" style={{ color: 'var(--slate-12)' }}>
                  {limit}
                </Text>
                <MaterialIcon name="expand_less" size={14} color="var(--slate-11)" />
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
