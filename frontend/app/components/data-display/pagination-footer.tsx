'use client';

import { Button, DropdownMenu, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

export interface PaginationState {
  page: number;
  limit: number;
  totalCount: number;
  totalPages: number;
  /** Real visible range, when the source reports one (cursor paging); derived from page otherwise. */
  startIndex?: number;
  endIndex?: number;
  /** Authoritative flags for sources whose page number is only an estimate. */
  hasPrev?: boolean;
  hasNext?: boolean;
}

export function PaginationFooter({
  pagination,
  onPageChange,
  onLimitChange,
}: {
  pagination: PaginationState;
  onPageChange: (page: number) => void;
  /** When set, a per-page selector is shown (hidden on phones to save room). */
  onLimitChange?: (limit: number) => void;
}) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const { page, limit, totalCount, totalPages } = pagination;
  const start = pagination.startIndex ?? (totalCount === 0 ? 0 : (page - 1) * limit + 1);
  const end = pagination.endIndex ?? Math.min(page * limit, totalCount);
  const hasPrev = pagination.hasPrev ?? page > 1;
  const hasNext = pagination.hasNext ?? page < totalPages;

  return (
    <Flex
      justify="between"
      align="center"
      wrap="wrap"
      gap="3"
      style={{
        padding: 'var(--space-2) var(--space-4)',
        borderTop: '1px solid var(--olive-3)',
        background: 'var(--olive-2)',
        flexShrink: 0,
      }}
    >
      <Text size="1" style={{ color: 'var(--slate-10)' }}>
        {t('common.pagination.showing', { start, end, total: totalCount })}
      </Text>
      <Flex gap="2" align="center">
        <Button className="touch-hit" variant="ghost" size="1" color="gray" disabled={!hasPrev} onClick={() => onPageChange(page - 1)}>
          <MaterialIcon name="chevron_left" size={16} />
          {t('common.previous')}
        </Button>
        <Text size="1" weight="medium" style={{ minWidth: 48, textAlign: 'center', color: 'var(--slate-11)' }}>
          {t('common.pagination.pageOf', { page, total: Math.max(totalPages, 1) })}
        </Text>
        <Button
          className="touch-hit"
          variant="ghost"
          size="1"
          color="gray"
          disabled={!hasNext}
          onClick={() => onPageChange(page + 1)}
        >
          {t('common.next')}
          <MaterialIcon name="chevron_right" size={16} />
        </Button>
        {onLimitChange && !isMobile && (
          <DropdownMenu.Root>
            <DropdownMenu.Trigger>
              <Button variant="soft" size="1" color="gray">
                {limit}
                <MaterialIcon name="expand_less" size={16} />
              </Button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Content align="end" sideOffset={4}>
              {[10, 25, 50, 100].map((value) => (
                <DropdownMenu.Item key={value} onClick={() => onLimitChange(value)}>
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
