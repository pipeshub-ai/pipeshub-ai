'use client';

import React from 'react';
import { Badge } from '@radix-ui/themes';
import type { BadgeProps } from '@radix-ui/themes';

export type UserStatus = 'Active' | 'Inactive' | 'Pending' | 'Expired' | 'Deactivated';

/**
 * Map status to Radix Badge color.
 *
 * | Status       | Color   |
 * |-------------|---------|
 * | Active       | jade    |
 * | Inactive     | gray    |
 * | Pending      | amber   |
 * | Expired      | red     |
 * | Deactivated  | gray    |
 */
const STATUS_COLOR_MAP: Record<UserStatus, BadgeProps['color']> = {
  Active: 'jade',
  Inactive: 'gray',
  Pending: 'amber',
  Expired: 'red',
  Deactivated: 'gray',
};

export interface StatusBadgeProps {
  /** User status value */
  status: UserStatus;
}

/**
 * StatusBadge — color-coded status badge for the Users table.
 */
export function StatusBadge({ status }: StatusBadgeProps) {
  return (
    <Badge variant="soft" color={STATUS_COLOR_MAP[status] ?? 'gray'} size="1">
      {status}
    </Badge>
  );
}
