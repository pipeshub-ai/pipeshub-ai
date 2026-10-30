'use client';

import { Flex, Text, Button, Checkbox, Separator } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { PermissionLockIcon } from '@/config';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { touchTarget } from '@/app/components/ui/responsive';
import type { PageViewMode } from '../types';

interface SelectionActionBarProps {
  selectedCount: number;
  onDeselectAll: () => void;
  onChat: () => void;
  onReindex?: () => void;
  onDelete?: () => void;
  deletePermissionDenied?: boolean;
  pageViewMode?: PageViewMode;
}

export function SelectionActionBar({
  selectedCount,
  onDeselectAll,
  onChat,
  onReindex,
  onDelete,
  deletePermissionDenied,
  pageViewMode = 'collections',
}: SelectionActionBarProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  if (selectedCount === 0) return null;

  return (
    <Flex
      align="center"
      gap="2"
      style={{
        position: 'fixed',
        bottom: '40px',
        left: isMobile ? '50%' : '60%',
        maxWidth: 'calc(100vw - 16px)',
        transform: 'translateX(-50%)',
        background: 'var(--frost-bg-strong)',
        backdropFilter: 'var(--frost-filter)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        boxShadow: '0 20px 28px 0 rgba(0, 0, 0, 0.15)',
        padding: 'var(--space-2) var(--space-2) var(--space-2) var(--space-3)',
        zIndex: 20,
        height: isMobile ? 'auto' : '42px',
      }}
    >
      {/* Checkbox to deselect all */}
      <Flex
        align="center"
        justify="center"
        onClick={(e) => e.stopPropagation()}
      >
        <Checkbox
          size="1"
          checked={true}
          onCheckedChange={onDeselectAll}
          style={{ cursor: 'pointer' }}
          color='gray'
        />
      </Flex>

      {/* Selection count text */}
      <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', whiteSpace: 'nowrap', fontStyle: 'normal' }}>
        {t('table.selected', { count: selectedCount })}
      </Text>

      {/* Divider */}
      <Separator orientation="vertical" size="1" style={{ height: '16px', background: 'var(--olive-3)' }} />

      <Button
        size={isMobile ? '3' : '1'}
        variant="surface"
        onClick={onChat}
        aria-label={t('kb.chatWithSelection')}
        style={{
          backgroundColor: 'var(--accent-surface)',
          border: '1px solid var(--accent-a7)',
          color: 'var(--accent-a11)',
          cursor: 'pointer',
          borderRadius: 'var(--radius-2)',
          padding: 'var(--space-3)',
          ...touchTarget(isMobile),
        }}
      >
        <MaterialIcon name="assistant" size={16} />
        {!isMobile && t('kb.chatWithSelection')}
      </Button>

      {/* Re-index button - Neutral style */}
      {onReindex && (
        <Button
          size={isMobile ? '3' : '1'}
          variant="outline"
          color="gray"
          onClick={onReindex}
          aria-label={t('menu.reindex')}
          style={{
            borderColor: 'var(--slate-a7)',
            color: 'var(--slate-11)',
            cursor: 'pointer',
            borderRadius: 'var(--radius-2)',
            padding: 'var(--space-3)',
            ...touchTarget(isMobile),
          }}
        >
          <MaterialIcon name="refresh" size={16} />
          {!isMobile && t('menu.reindex')}
        </Button>
      )}

      {/* Delete button - hidden only in all-records mode */}
      {pageViewMode !== 'all-records' && onDelete && (
        <Button
          size={isMobile ? '3' : '1'}
          variant="outline"
          color="red"
          disabled={deletePermissionDenied}
          aria-label={t('action.delete')}
          onClick={() => {
            if (deletePermissionDenied) return;
            onDelete();
          }}
          style={{
            borderColor: 'var(--red-7)',
            color: 'var(--red-9)',
            cursor: deletePermissionDenied ? 'not-allowed' : 'pointer',
            ...touchTarget(isMobile),
          }}
        >
          <MaterialIcon name="delete" size={16} />
          {!isMobile && t('action.delete')}
          {deletePermissionDenied && <PermissionLockIcon />}
        </Button>
      )}
    </Flex>
  );
}
