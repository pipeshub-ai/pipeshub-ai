'use client';

import React, { useState } from 'react';
import { Flex, Text, Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';

// ========================================
// Types
// ========================================

export interface BulkAction {
  /** Unique key for this action */
  key: string;
  /** Display label */
  label: string;
  /** Material icon name */
  icon: string;
  /** Visual variant — 'danger' renders red/destructive styling */
  variant?: 'default' | 'danger';
  /** Whether this action is disabled */
  disabled?: boolean;
  /** Async handler called when button is clicked */
  onClick: () => void | Promise<void>;
}

export interface EntityBulkActionBarProps {
  /** Number of selected items */
  selectedCount: number;
  /** The noun for the items (e.g. "Users", "Groups") */
  itemLabel?: string;
  /** Actions to display as buttons on the right side */
  actions: BulkAction[];
  /** Whether to show the bar (typically selectedCount > 0) */
  visible: boolean;
}

// ========================================
// Component
// ========================================

/**
 * EntityBulkActionBar — floating bar shown at the bottom of entity list pages
 * when one or more items are selected via checkboxes.
 *
 * Overlays the pagination footer. Displays:
 * - Left: checkmark icon + "N Items Selected"
 * - Right: contextual action buttons
 *
 * This is a common workspace component usable by Users, Groups, and Teams pages.
 */
export function EntityBulkActionBar({
  selectedCount,
  itemLabel = 'Users',
  actions,
  visible,
}: EntityBulkActionBarProps) {
  const [loadingKey, setLoadingKey] = useState<string | null>(null);

  if (!visible) return null;

  const handleClick = async (action: BulkAction) => {
    if (loadingKey) return; // prevent double-clicks
    setLoadingKey(action.key);
    try {
      await action.onClick();
    } finally {
      setLoadingKey(null);
    }
  };

  return (
    <Flex
      align="center"
      gap="3"
      style={{
        position: 'absolute',
        bottom: 'var(--space-2)',
        left: '50%',
        transform: 'translateX(-50%)',
        backgroundColor: 'var(--slate-2)',
        border: '1px solid var(--slate-6)',
        borderRadius: 'var(--radius-3)',
        padding: '6px 16px',
        zIndex: 10,
        boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)',
      }}
    >
      {/* Left: selection count */}
      <Flex align="center" gap="2">
        <MaterialIcon name="check" size={16} color="var(--slate-11)" />
        <Text size="2" weight="medium" style={{ color: 'var(--slate-11)', whiteSpace: 'nowrap' }}>
          {selectedCount} {itemLabel} Selected
        </Text>
      </Flex>

      {/* Right: action buttons */}
      {actions.map((action) => {
        const isDanger = action.variant === 'danger';
        const isLoading = loadingKey === action.key;
        const isDisabled = action.disabled || isLoading || (loadingKey !== null && loadingKey !== action.key);

        const iconColor = isDanger && !isDisabled ? 'white' : 'var(--slate-12)';
        const button = (
          <Button
            key={action.key}
            size="1"
            variant={isDanger ? 'solid' : 'soft'}
            color={isDanger ? 'red' : 'gray'}
            disabled={isDisabled}
            onClick={() => handleClick(action)}
            style={{
              cursor: isDisabled ? 'not-allowed' : isLoading ? 'wait' : 'pointer',
              whiteSpace: 'nowrap',
              gap: 4,
            }}
          >
            {isLoading ? (
              <Spinner size={12} color={iconColor} />
            ) : (
              <MaterialIcon name={action.icon} size={14} color={iconColor} />
            )}
            {action.label}
          </Button>
        );

        return button;
      })}
    </Flex>
  );
}
