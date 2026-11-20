'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { ListSkeleton, type ListSkeletonColumn } from '@/app/components/ui/list-skeleton';

export type TableSkeletonColumnShape = ListSkeletonColumn;

export interface TableSkeletonProps {
  /** Number of placeholder rows. Default: 6 */
  rows?: number;
  /** Column shapes — matches the real table's column layout. */
  columns?: TableSkeletonColumnShape[];
  /** Whether to reserve space for a row-action menu (⋯) column. Default: true */
  hasRowActions?: boolean;
  /** Width of the actions column. Default: 80 */
  actionsWidth?: number;
  /** Whether to reserve space for a leading checkbox column. Default: true */
  hasCheckbox?: boolean;
  /** Width of the checkbox column. Default: 38 */
  checkboxWidth?: number;
  /** Row height in px. Default: 60 (matches EntityDataTable) */
  rowHeight?: number;
  /** Horizontal padding of each data cell, to line up with the table's own cells. Default: '0 var(--space-2)' */
  cellPadding?: string;
  /** What is loading, for screen readers. Default: "Loading items…". */
  statusLabel?: string;
}

/**
 * TableSkeleton — placeholder rows for a table that has not finished its first
 * load. The shared list skeleton's table-row variant, with the layout
 * conventions of `EntityDataTable` (60px rows, 38px checkbox column, 80px
 * actions column, a bar per column).
 */
export function TableSkeleton({
  rows = 6,
  columns,
  hasRowActions = true,
  hasCheckbox = true,
  checkboxWidth = 38,
  rowHeight = 60,
  actionsWidth = 80,
  cellPadding,
  statusLabel,
}: TableSkeletonProps) {
  const { t } = useTranslation();
  return (
    <ListSkeleton
      variant="table-row"
      count={rows}
      columns={columns}
      hasRowActions={hasRowActions}
      hasCheckbox={hasCheckbox}
      checkboxWidth={checkboxWidth}
      rowHeight={rowHeight}
      actionsWidth={actionsWidth}
      cellPadding={cellPadding}
      statusLabel={statusLabel ?? t('loading.items')}
    />
  );
}
