'use client';

import { Flex, Text, Button, Checkbox, Separator } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { PageViewMode } from '../types';

interface SelectionActionBarProps {
  selectedCount: number;
  onDeselectAll: () => void;
  onChat: () => void;
  onReindex: () => void;
  onDelete: () => void;
  pageViewMode?: PageViewMode;
}

export function SelectionActionBar({
  selectedCount,
  onDeselectAll,
  onChat,
  onReindex,
  onDelete,
  pageViewMode = 'collections',
}: SelectionActionBarProps) {
  if (selectedCount === 0) return null;

  return (
    <Flex
      align="center"
      gap="2"
      style={{
        position: 'fixed',
        bottom: '40px',
        left: '60%',
        transform: 'translateX(-50%)',
        background: 'var(--effects-translucent)',
        backdropFilter: 'blur(25px)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        boxShadow: '0 20px 28px 0 rgba(0, 0, 0, 0.15)',
        padding: 'var(--space-2) var(--space-2) var(--space-2) var(--space-3)',
        zIndex: 20,
        height: '42px',
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
        {selectedCount} {selectedCount === 1 ? 'Item' : 'Items'} Selected
      </Text>

      {/* Divider */}
      <Separator orientation="vertical" size="1" style={{ height: '16px', background: 'var(--olive-3)' }} />

      {/* Chat button - temporarily hidden
      <Button
        size="1"
        variant="surface"
        onClick={onChat}
        style={{
          backgroundColor: 'var(--accent-surface)',
          border: '1px solid var(--accent-a7)',
          color: 'var(--accent-a11)',
          cursor: 'pointer',
          borderRadius: 'var(--radius-2)',
          padding: 'var(--space-3)'
        }}
      >
        <MaterialIcon name="assistant" size={16} />
        Chat
      </Button>
      */}

      {/* Re-index button - Neutral style */}
      <Button
        size="1"
        variant="outline"
        color="gray"
        onClick={onReindex}
        style={{
          borderColor: 'var(--slate-a7)',
          color: 'var(--slate-11)',
          cursor: 'pointer',
          borderRadius: 'var(--radius-2)',
          padding: 'var(--space-3)'
        }}
      >
        <MaterialIcon name="refresh" size={16} />
        Re-index
      </Button>

      {/* Delete button - Error style (hidden in all-records mode) */}
      {pageViewMode !== 'all-records' && (
        <Button
          size="1"
          variant="outline"
          color="red"
          onClick={onDelete}
          style={{
            borderColor: 'var(--red-7)',
            color: 'var(--red-9)',
            cursor: 'pointer',
          }}
        >
          <MaterialIcon name="delete" size={16} />
          Delete
        </Button>
      )}
    </Flex>
  );
}
