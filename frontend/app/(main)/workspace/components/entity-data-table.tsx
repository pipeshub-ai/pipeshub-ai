'use client';

import { useTranslation } from 'react-i18next';

import React, { useState } from 'react';
import { Flex, Box, Text, Checkbox } from '@radix-ui/themes';
import { EmptyIcon } from '@/app/components/ui/empty-icon';
import { Spinner } from '@/app/components/ui/spinner';
import { useIsBelowDesktop, useIsMobile } from '@/lib/hooks/use-is-mobile';
import { TableSkeleton, type TableSkeletonColumnShape } from '@/app/components/data-display';

// ========================================
// Types
// ========================================

export interface ColumnConfig<T> {
  /** Unique column key */
  key: string;
  /** Column header label */
  label: string;
  /** Column width (CSS value) */
  width?: string;
  /** Minimum width */
  minWidth?: string;
  /** Hide this column on phones (the table also scrolls horizontally). */
  hideOnMobile?: boolean;
  /** Render function for cell content */
  render: (item: T) => React.ReactNode;
}

export interface EntityDataTableProps<T> {
  /** Column configuration array */
  columns: ColumnConfig<T>[];
  /** Data items to render as rows */
  data: T[];
  /** Extract unique ID from an item */
  getItemId: (item: T) => string;
  /** Currently selected item IDs */
  selectedIds: Set<string>;
  /** Called when selection changes */
  onSelectionChange: (ids: Set<string>) => void;
  /** Optional render function for row action cell (⋯ menu) */
  renderRowActions?: (item: T) => React.ReactNode;
  /** Whether data is currently loading */
  isLoading?: boolean;
  /** Hovered row ID for interactive states */
  hoveredRowId?: string | null;
  /** Callback when a row is clicked (not checkbox or actions) */
  onRowClick?: (item: T) => void;
}

// ========================================
// Component
// ========================================

/** Matches the people-table spec: 44px select column, 12px cell inset */
const CHECKBOX_COL_WIDTH = 44;
const CELL_PADDING = '0 var(--space-3)';

/**
 * EntityDataTable — reusable data table for Users, Groups, Teams.
 *
 * Features:
 * - Checkbox column for selection
 * - Configurable columns via ColumnConfig[]
 * - Hover state on rows
 * - Optional action menu column
 */
export function EntityDataTable<T>({
  columns: allColumns,
  data,
  getItemId,
  selectedIds,
  onSelectionChange,
  renderRowActions,
  isLoading,
  onRowClick,
}: EntityDataTableProps<T>) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const isBelowDesktop = useIsBelowDesktop();
  const columns = isMobile ? allColumns.filter((c) => !c.hideOnMobile) : allColumns;
  // checkbox (38) + columns + actions (80); percentage or missing widths fall back to minWidth, then 160
  const px = (v?: string) => (v && !v.endsWith('%') ? parseInt(v, 10) : NaN);
  const tableMinWidth =
    38 + columns.reduce((sum, c) => sum + (px(c.width) || px(c.minWidth) || 160), 0) + 80;
  const [hoveredRowId, setHoveredRowId] = useState<string | null>(null);
  // Below desktop the table scrolls sideways; pin the ⋯ menu to the right edge so it stays in view.
  const stickyActions: React.CSSProperties = isBelowDesktop
    ? { position: 'sticky', right: 0, zIndex: 1, backgroundColor: 'inherit', boxShadow: '-1px 0 0 var(--olive-3)' }
    : {};

  const allSelected = data.length > 0 && data.every((item) => selectedIds.has(getItemId(item)));
  const someSelected = data.some((item) => selectedIds.has(getItemId(item))) && !allSelected;

  const handleSelectAll = () => {
    if (allSelected) {
      onSelectionChange(new Set());
    } else {
      onSelectionChange(new Set(data.map((item) => getItemId(item))));
    }
  };

  const handleSelectItem = (id: string) => {
    const next = new Set(selectedIds);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    onSelectionChange(next);
  };

  // Refetch status while rows are shown: sticks below the header, announced to screen readers.
  const refreshingStatus = (top: string) =>
    isLoading && data.length > 0 ? (
      <Box
        style={{
          position: 'sticky',
          top,
          float: 'right',
          marginRight: 12,
          zIndex: 2,
          padding: '6px 10px',
          borderRadius: 'var(--radius-3)',
          backgroundColor: 'var(--olive-2)',
          border: '1px solid var(--olive-4)',
          boxShadow: '0 4px 12px rgba(0, 0, 0, 0.06)',
          color: 'var(--slate-11)',
        }}
        aria-live="polite"
        aria-label={t('common.refreshing')}
      >
        <Spinner size={14} />
      </Box>
    ) : null;

  // Phones get a list: a row of desktop columns does not fit 390px, and scrolling it sideways
  // under a pinned actions cell hid most of each row. The first column leads; the rest follow
  // as labelled values on one wrapping line.
  if (isMobile) {
    const [primary, ...rest] = columns;
    return (
      <Flex direction="column" data-testid="entity-data-table" style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
        <Flex
          align="center"
          gap="2"
          style={{
            minHeight: 44,
            padding: '0 var(--space-3)',
            borderBottom: '1px solid var(--olive-6)',
            backgroundColor: 'var(--olive-2)',
            position: 'sticky',
            top: 0,
            zIndex: 2,
            flexShrink: 0,
          }}
        >
          <Checkbox
            size="2"
            checked={allSelected ? true : someSelected ? 'indeterminate' : false}
            onCheckedChange={handleSelectAll}
            aria-label={t('table.selectAll')}
            style={{ cursor: 'pointer' }}
          />
          <Text size="1" weight="medium" style={{ color: 'var(--slate-9)' }}>
            {t('table.selectAll')}
          </Text>
        </Flex>
        <Box style={{ position: 'relative', flex: '1 0 auto', opacity: isLoading && data.length > 0 ? 0.55 : 1 }}>
          {isLoading && data.length === 0 ? (
            <TableSkeleton rows={6} columns={[{ barWidth: 0.6 }]} hasRowActions={Boolean(renderRowActions)} />
          ) : null}
          {refreshingStatus('52px')}
          {data.map((item) => {
            const id = getItemId(item);
            const isSelected = selectedIds.has(id);
            return (
              <Flex
                key={id}
                align="start"
                gap="3"
                tabIndex={0}
                role="row"
                aria-selected={isSelected}
                onClick={() => onRowClick?.(item)}
                style={{
                  padding: 'var(--space-3)',
                  borderBottom: '1px solid var(--olive-3)',
                  backgroundColor: isSelected ? 'var(--accent-3)' : 'var(--olive-1)',
                  cursor: 'pointer',
                  userSelect: 'none',
                }}
              >
                <Flex align="center" justify="center" className="touch-hit" style={{ paddingTop: 10 }} onClick={(e) => e.stopPropagation()}>
                  <Checkbox size="2" checked={isSelected} onCheckedChange={() => handleSelectItem(id)} style={{ cursor: 'pointer' }} />
                </Flex>
                <Flex direction="column" gap="2" style={{ flex: 1, minWidth: 0 }}>
                  {primary && <Flex align="center" style={{ minWidth: 0, overflow: 'hidden' }}>{primary.render(item)}</Flex>}
                  {rest.length > 0 && (
                    <Flex align="center" wrap="wrap" gap="3" style={{ rowGap: 'var(--space-1)' }}>
                      {rest.map((col) => (
                        <Flex key={col.key} align="center" gap="1" style={{ minWidth: 0, maxWidth: '100%' }}>
                          <Text size="1" style={{ color: 'var(--slate-9)', flexShrink: 0 }}>{col.label}</Text>
                          <Flex align="center" style={{ minWidth: 0, overflow: 'hidden' }}>{col.render(item)}</Flex>
                        </Flex>
                      ))}
                    </Flex>
                  )}
                </Flex>
                {renderRowActions && (
                  <Flex align="center" gap="1" style={{ flexShrink: 0, paddingTop: 4 }} onClick={(e) => e.stopPropagation()}>
                    {renderRowActions(item)}
                  </Flex>
                )}
              </Flex>
            );
          })}
        </Box>
      </Flex>
    );
  }

  return (
    // One element scrolls both ways, so the header (top) and the actions cell (right) can be sticky.
    <Flex
      direction="column"
      data-testid="entity-data-table"
      className={isBelowDesktop ? undefined : 'no-scrollbar'}
      style={{ flex: 1, minHeight: 0, overflowX: 'auto', overflowY: 'auto' }}
    >
    <Flex direction="column" style={{ flex: '1 0 auto', minWidth: isBelowDesktop ? tableMinWidth : undefined }}>
      {/* Table Header */}
      <Flex
        align="center"
        style={{
          height: 40,
          borderBottom: '1px solid var(--olive-3)',
          backgroundColor: 'var(--olive-2)',
          flexShrink: 0,
          position: 'sticky',
          top: 0,
          zIndex: 2,
        }}
      >
        {/* Checkbox column */}
        <Flex
          align="center"
          style={{ width: CHECKBOX_COL_WIDTH, flexShrink: 0, paddingLeft: 'var(--space-3)', boxSizing: 'border-box' }}
          onClick={(e) => e.stopPropagation()}
        >
          <Checkbox
            size="2"
            checked={allSelected ? true : someSelected ? 'indeterminate' : false}
            onCheckedChange={handleSelectAll}
            style={{ cursor: 'pointer' }}
          />
        </Flex>

        {/* Data columns */}
        {columns.map((col) => (
          <Flex
            key={col.key}
            align="center"
            style={{
              width: col.width,
              minWidth: col.minWidth,
              flex: col.width ? undefined : 1,
              padding: CELL_PADDING,
            }}
          >
            <Text size="1" style={{ color: 'var(--olive-9)' }}>
              {col.label}
            </Text>
          </Flex>
        ))}

        {/* Actions column header (empty) */}
        {renderRowActions && (
          <Box style={{ width: '80px', flexShrink: 0, alignSelf: 'stretch', ...stickyActions }} />
        )}
      </Flex>

      {/* Table Body */}
      <Box
        style={{
          position: 'relative',
          flex: '1 0 auto',
          opacity: isLoading && data.length > 0 ? 0.55 : 1,
          transition: 'opacity 150ms ease',
        }}
      >
        {/* Initial-load skeleton (no data yet) */}
        {isLoading && data.length === 0 ? (
          <TableSkeleton
            rows={6}
            checkboxWidth={CHECKBOX_COL_WIDTH}
            cellPadding={CELL_PADDING}
            columns={columns.map<TableSkeletonColumnShape>((col, i) => ({
              width: col.width,
              minWidth: col.minWidth,
              barWidth: i === 0 ? 0.6 : 0.75,
            }))}
            hasRowActions={Boolean(renderRowActions)}
          />
        ) : null}

        {/* Refetch overlay spinner (data present, refreshing) */}
        {refreshingStatus('calc(var(--space-9) + 8px)')}

        {data.map((item) => {
          const id = getItemId(item);
          const isSelected = selectedIds.has(id);
          const isHovered = hoveredRowId === id;

          return (
            <Flex
              key={id}
              align="center"
              tabIndex={0}
              role="row"
              aria-selected={isSelected}
              onMouseEnter={() => setHoveredRowId(id)}
              onMouseLeave={() => setHoveredRowId(null)}
              onClick={() => onRowClick?.(item)}
              className="entity-table-row"
              style={{
                height: '60px',
                borderBottom: '1px solid var(--gray-a5)',
                // Opaque so the sticky actions cell (backgroundColor: inherit) still covers what scrolls under it
                backgroundColor: isSelected
                  ? 'var(--accent-3)'
                  : isHovered
                    ? 'var(--olive-3)'
                    : 'var(--olive-1)',
                transition: 'background-color 120ms ease',
                cursor: 'pointer',
                userSelect: 'none',
              }}
            >
              {/* Checkbox cell */}
              <Flex
                align="center"
                style={{ width: CHECKBOX_COL_WIDTH, flexShrink: 0, paddingLeft: 'var(--space-3)', boxSizing: 'border-box' }}
                onClick={(e) => e.stopPropagation()}
              >
                <Checkbox
                  size="2"
                  checked={isSelected}
                  onCheckedChange={() => handleSelectItem(id)}
                  style={{ cursor: 'pointer' }}
                />
              </Flex>

              {/* Data cells */}
              {columns.map((col) => (
                <Flex
                  key={col.key}
                  align="center"
                  style={{
                    width: col.width,
                    minWidth: col.minWidth,
                    flex: col.width ? undefined : 1,
                    padding: CELL_PADDING,
                    overflow: 'hidden',
                  }}
                >
                  {col.render(item)}
                </Flex>
              ))}

              {/* Actions cell */}
              {renderRowActions && (
                <Flex
                  align="center"
                  gap="1"
                  style={{ width: '80px', flexShrink: 0, alignSelf: 'stretch', padding: '0 var(--space-2)', ...stickyActions }}
                >
                  {renderRowActions(item)}
                </Flex>
              )}
            </Flex>
          );
        })}
      </Box>
    </Flex>
    </Flex>
  );
}
