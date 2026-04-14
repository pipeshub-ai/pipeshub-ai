'use client';

import React from 'react';
import { Flex, Box, Text, DropdownMenu } from '@radix-ui/themes';
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
 * - Left: "Showing X-Y of Z Item"
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
      style={{
        padding: '12px 16px',
        borderTop: '1px solid var(--slate-4)',
        backgroundColor: 'var(--slate-1)',
        flexShrink: 0,
        width: '100%',
      }}
    >
      {/* Left: showing count */}
      <Text size="2" style={{ color: 'var(--slate-9)' }}>
        {t('workspace.pagination.showing', { from, to, total: totalCount })}
      </Text>

      {/* Right: pagination controls */}
      <Flex gap="3" align="center">
        {/* Previous */}
        <Flex
          align="center"
          gap="1"
          onClick={() => hasPrev && onPageChange(page - 1)}
          style={{
            cursor: hasPrev ? 'pointer' : 'not-allowed',
            opacity: hasPrev ? 1 : 0.5,
            color: 'var(--slate-11)',
          }}
        >
          <MaterialIcon name="chevron_left" size={16} />
          <Text size="2">{t('workspace.pagination.previous')}</Text>
        </Flex>

        {/* Page number */}
        <Box
          style={{
            padding: '4px 12px',
            backgroundColor: 'var(--slate-3)',
            borderRadius: 'var(--radius-2)',
            minWidth: '32px',
            textAlign: 'center',
          }}
        >
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {page}
          </Text>
        </Box>

        {/* Next */}
        <Flex
          align="center"
          gap="1"
          onClick={() => hasNext && onPageChange(page + 1)}
          style={{
            cursor: hasNext ? 'pointer' : 'not-allowed',
            opacity: hasNext ? 1 : 0.5,
            color: 'var(--slate-11)',
          }}
        >
          <Text size="2">{t('workspace.pagination.next')}</Text>
          <MaterialIcon name="chevron_right" size={16} />
        </Flex>

        {/* Separator */}
        <Box style={{ width: '1px', height: '20px', backgroundColor: 'var(--slate-6)' }} />

        {/* Limit selector */}
        {onLimitChange && (
          <DropdownMenu.Root>
            <DropdownMenu.Trigger>
              <Flex
                align="center"
                gap="1"
                style={{
                  cursor: 'pointer',
                  padding: '4px 8px',
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
                  {value} per page
                </DropdownMenu.Item>
              ))}
            </DropdownMenu.Content>
          </DropdownMenu.Root>
        )}
      </Flex>
    </Flex>
  );
}
