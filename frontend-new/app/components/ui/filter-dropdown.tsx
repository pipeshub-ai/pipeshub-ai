'use client';

import React, { useState, useMemo, useRef, useCallback, useEffect } from 'react';
import { Flex, Box, Text, Badge, Button, Popover, Checkbox, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';

/**
 * Option item for the filter dropdown
 */
export interface FilterOption {
  /** Unique value identifier for the option */
  value: string;
  /** Display label shown in the dropdown */
  label: string;
  /** Optional Material icon name to display alongside the label */
  icon?: string;
  /** Optional color for the icon (CSS color value) */
  iconColor?: string;
  /** Optional custom icon element (takes priority over icon string) */
  customIcon?: React.ReactNode;
}

/**
 * Props for the FilterDropdown component
 */
export interface FilterDropdownProps {
  /** Label text displayed on the trigger button */
  label: string;
  /** Optional Material icon name for the trigger button */
  icon?: string;
  /** Array of selectable options */
  options: FilterOption[];
  /** Currently selected option values */
  selectedValues: string[];
  /** Callback fired when selection changes */
  onSelectionChange: (values: string[]) => void;
  /** Enable search/filter functionality within the dropdown (default: false) */
  searchable?: boolean;
  /** Disable the filter dropdown (default: false) */
  disabled?: boolean;
  /** Plural label shown in the applied state chip, e.g. "Types", "Statuses" */
  pluralLabel?: string;
  /**
   * Async search callback. When provided, search is server-side:
   * the component calls this instead of filtering `options` locally.
   * Should update `options` externally.
   */
  onSearch?: (query: string) => void;
  /**
   * Called when the user scrolls to the bottom of the options list.
   * Use this to load the next page of options.
   */
  onLoadMore?: () => void;
  /** Whether more options are being loaded (shows a spinner at the bottom) */
  isLoadingMore?: boolean;
  /** Whether there are more options to load */
  hasMore?: boolean;
  /**
   * First page / non-append fetch in progress (server-side `onSearch` / `onPopoverOpenChange`).
   * Avoids flashing “No results” before options arrive; optional banner when only prior selections exist.
   */
  isLoadingOptions?: boolean;
  /** Portal container for the popover (e.g. modal body) so the menu stacks above overlays */
  portalContainer?: HTMLElement | null;
  /** Fired when the popover opens or closes (use to load the first page of server options). */
  onPopoverOpenChange?: (open: boolean) => void;
  /** Merged onto Popover.Content style (width, maxHeight, etc.) */
  popoverContentStyle?: React.CSSProperties;
  /**
   * `segmented` (default): trigger shows label, "is any of", value chips, and clear — dense toolbar style.
   * `simple`: single outline control + optional clear beside it; use when value chips are shown elsewhere (e.g. connector filter summaries).
   */
  triggerLayout?: 'segmented' | 'simple';
  /** `title` / native tooltip on the trigger control (e.g. full phrase when `label` is shortened). */
  triggerTitle?: string;
  /**
   * When `triggerLayout` is `simple`, show count badge + clear under the trigger instead of inside / beside it
   * so the button stays one line and does not overflow narrow columns.
   */
  summaryBelowTrigger?: boolean;
}

/**
 * FilterDropdown - A reusable multi-select dropdown filter component
 *
 * @description Provides a popover-based multi-select filter with optional search functionality.
 * Features include:
 * - Checkbox-based multi-selection
 * - Optional search/filter within options
 * - Selection badge showing count of selected items
 * - Clear button to reset selection
 * - Accessible keyboard navigation via Radix Popover
 *
 * @example
 * ```tsx
 * // Basic usage
 * <FilterDropdown
 *   label="Status"
 *   icon="circle"
 *   options={[
 *     { value: 'active', label: 'Active', icon: 'check_circle' },
 *     { value: 'pending', label: 'Pending', icon: 'schedule' },
 *   ]}
 *   selectedValues={selectedStatuses}
 *   onSelectionChange={(values) => setFilter({ statuses: values })}
 * />
 *
 * // With search enabled
 * <FilterDropdown
 *   label="Source"
 *   options={sourceOptions}
 *   selectedValues={filter.sources || []}
 *   onSelectionChange={(values) => setFilter({ sources: values })}
 *   searchable
 * />
 * ```
 */
export function FilterDropdown({
  label,
  icon,
  options,
  selectedValues,
  onSelectionChange,
  searchable = false,
  disabled = false,
  pluralLabel: _pluralLabel,
  onSearch,
  onLoadMore,
  isLoadingMore = false,
  hasMore = false,
  isLoadingOptions = false,
  portalContainer,
  onPopoverOpenChange,
  popoverContentStyle,
  triggerLayout = 'segmented',
  triggerTitle,
  summaryBelowTrigger = false,
}: FilterDropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const listRef = useRef<HTMLDivElement>(null);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevOpenRef = useRef(false);

  const hasSelection = selectedValues.length > 0;
  const isServerSearch = !!onSearch;

  // Filter options by search query (only when not using server search)
  const filteredOptions = useMemo(() => {
    if (isServerSearch) return options; // server already filtered
    if (!searchQuery.trim()) return options;
    const lowerQuery = searchQuery.toLowerCase();
    return options.filter((option) =>
      option.label.toLowerCase().includes(lowerQuery)
    );
  }, [options, searchQuery, isServerSearch]);

  // Debounced search for server-side mode
  const handleSearchChange = useCallback(
    (value: string) => {
      setSearchQuery(value);
      if (isServerSearch) {
        if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
        searchTimerRef.current = setTimeout(() => onSearch!(value), 300);
      }
    },
    [isServerSearch, onSearch]
  );

  // Infinite scroll: load more when near bottom
  const handleScroll = useCallback(() => {
    if (!onLoadMore || !hasMore || isLoadingMore) return;
    const el = listRef.current;
    if (!el) return;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 40) {
      onLoadMore();
    }
  }, [onLoadMore, hasMore, isLoadingMore]);

  // Reset search when dropdown closes (not on first mount — avoids spurious server fetches)
  useEffect(() => {
    if (prevOpenRef.current && !isOpen) {
      setSearchQuery('');
      if (isServerSearch) onSearch!('');
    }
    prevOpenRef.current = isOpen;
  }, [isOpen, isServerSearch, onSearch]);

  // Toggle option selection
  const toggleOption = (value: string) => {
    if (selectedValues.includes(value)) {
      onSelectionChange(selectedValues.filter((v) => v !== value));
    } else {
      onSelectionChange([...selectedValues, value]);
    }
  };

  // Clear all selections
  const clearSelection = (e: React.MouseEvent) => {
    e.stopPropagation();
    onSelectionChange([]);
  };

  const clearSelectionButton = () => (
    <Button
      type="button"
      variant="ghost"
      color="gray"
      size="1"
      style={{ cursor: 'pointer', flexShrink: 0 }}
      onClick={clearSelection}
    >
      Clear
    </Button>
  );

  return (
    <Popover.Root
      open={disabled ? false : isOpen}
      onOpenChange={
        disabled
          ? undefined
          : (next) => {
              setIsOpen(next);
              onPopoverOpenChange?.(next);
            }
      }
    >
      {triggerLayout === 'simple' ? (
        <Flex direction="column" gap="2" style={{ width: '100%', minWidth: 0 }}>
          <Popover.Trigger>
            <Button
              variant="outline"
              color="gray"
              size="2"
              disabled={disabled}
              title={triggerTitle ?? label}
              aria-label={triggerTitle ?? label}
              style={{
                minHeight: 32,
                width: '100%',
                minWidth: 0,
                maxWidth: '100%',
                gap: 8,
                cursor: disabled ? 'not-allowed' : 'pointer',
                borderRadius: 'var(--radius-2)',
                opacity: disabled ? 0.5 : 1,
                justifyContent: 'flex-start',
                alignItems: 'center',
              }}
            >
              {icon && (
                <MaterialIcon name={icon} size={16} color="var(--slate-11)" style={{ flexShrink: 0 }} />
              )}
              <Box style={{ minWidth: 0, flex: 1, overflow: 'hidden', textAlign: 'left' }}>
                <Text
                  size="2"
                  style={{
                    color: 'var(--gray-12)',
                    display: 'block',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {label}
                </Text>
              </Box>
              {!summaryBelowTrigger && hasSelection ? (
                <Badge color="jade" variant="soft" size="1" radius="full" style={{ flexShrink: 0 }}>
                  {selectedValues.length} selected
                </Badge>
              ) : null}
            </Button>
          </Popover.Trigger>
          {summaryBelowTrigger && hasSelection ? (
            <Flex align="center" gap="2" wrap="wrap" justify="between">
              <Badge color="jade" variant="soft" size="1" radius="full">
                {selectedValues.length} selected
              </Badge>
              {clearSelectionButton()}
            </Flex>
          ) : !summaryBelowTrigger && hasSelection ? (
            clearSelectionButton()
          ) : null}
        </Flex>
      ) : (
        <Popover.Trigger>
          {hasSelection ? (
            <Flex
              align="center"
              wrap="wrap"
              style={{
                minHeight: 26,
                border: '1px solid var(--gray-a7)',
                borderRadius: 'var(--radius-2)',
                backgroundColor: 'var(--gray-a3)',
                cursor: 'pointer',
                overflow: 'hidden',
                maxWidth: '100%',
              }}
            >
              {/* Segment 1: icon + label */}
              <Flex
                align="center"
                style={{
                  padding: icon ? '0 8px 0 8px' : '0 8px',
                  borderRight: '1px solid var(--gray-a7)',
                  alignSelf: 'stretch',
                  minHeight: 26,
                  gap: '4px',
                }}
              >
                {icon && (
                  <MaterialIcon name={icon} size={14} color="var(--gray-11)" />
                )}
                <Text size="1" style={{ color: 'var(--gray-11)', whiteSpace: 'nowrap' }}>
                  {label}
                </Text>
              </Flex>

              {/* Segment 2: verb phrase */}
              <Flex
                align="center"
                style={{
                  padding: '0 8px',
                  borderRight: '1px solid var(--gray-a7)',
                  alignSelf: 'stretch',
                }}
              >
                <Text size="1" style={{ color: 'var(--gray-11)', whiteSpace: 'nowrap' }}>
                  is any of
                </Text>
              </Flex>

              {/* Segment 3: one chip per selected value (label), legacy parity */}
              <Flex
                align="center"
                wrap="wrap"
                gap="2"
                style={{
                  padding: '4px 8px',
                  borderRight: '1px solid var(--gray-a7)',
                  flex: 1,
                  minWidth: 0,
                  alignSelf: 'stretch',
                }}
              >
                {selectedValues.map((val) => {
                  const opt = options.find((o) => o.value === val);
                  const text = opt?.label ?? val;
                  return (
                    <Badge
                      key={val}
                      color="jade"
                      variant="soft"
                      size="1"
                      title={text}
                      style={{
                        maxWidth: 220,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {text}
                    </Badge>
                  );
                })}
              </Flex>

              {/* Segment 4: clear button */}
              <Flex
                align="center"
                justify="center"
                onClick={clearSelection}
                style={{
                  padding: '0 4px',
                  alignSelf: 'stretch',
                  minHeight: 26,
                  cursor: 'pointer',
                }}
              >
                <MaterialIcon name="close" size={14} color="var(--gray-11)" />
              </Flex>
            </Flex>
          ) : (
            <Button
              variant="outline"
              size="1"
              radius="medium"
              color="gray"
              disabled={disabled}
              style={{
                height: '24px',
                gap: '4px',
                cursor: disabled ? 'not-allowed' : 'pointer',
                borderRadius: 'var(--radius-2)',
                opacity: disabled ? 0.5 : 1,
              }}
            >
              {icon && (
                <MaterialIcon
                  name={icon}
                  size={14}
                  color="var(--slate-11)"
                />
              )}
              <Text size="1">{label}</Text>
            </Button>
          )}
        </Popover.Trigger>
      )}

      <Popover.Content
        side="bottom"
        align="start"
        sideOffset={4}
        container={portalContainer ?? undefined}
        style={{
          padding: '8px',
          minWidth: '180px',
          maxWidth: '240px',
          backgroundColor: 'var(--olive-2)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-1)',
          boxShadow:
            '0 12px 32px -16px var(--slate-a5, rgba(217, 237, 254, 0.15)), 0 12px 60px 0 var(--black-a3, rgba(0, 0, 0, 0.15))',
          ...popoverContentStyle,
        }}
      >
        {/* Search input */}
        {searchable && (
          <Box style={{ marginBottom: '8px' }}>
            <TextField.Root
              size="1"
              placeholder="Search"
              value={searchQuery}
              onChange={(e) => handleSearchChange(e.target.value)}
            >
              <TextField.Slot>
                <MaterialIcon name="search" size={14} color="var(--slate-9)" />
              </TextField.Slot>
            </TextField.Root>
          </Box>
        )}

        {/* Options list */}
        <Flex
          ref={listRef}
          direction="column"
          gap="1"
          className="no-scrollbar"
          onScroll={handleScroll}
          style={{ maxHeight: '200px', overflowY: 'auto' }}
        >
          {isLoadingOptions && filteredOptions.length === 0 ? (
            <Flex align="center" justify="center" gap="2" style={{ padding: '20px 12px' }}>
              <Spinner size={14} />
              <Text size="2" style={{ color: 'var(--slate-11)' }}>
                Loading options…
              </Text>
            </Flex>
          ) : null}
          {isLoadingOptions && filteredOptions.length > 0 ? (
            <Flex
              align="center"
              gap="2"
              style={{
                padding: '8px 10px',
                marginBottom: 4,
                borderRadius: 'var(--radius-1)',
                backgroundColor: 'var(--gray-a3)',
              }}
            >
              <Spinner size={12} />
              <Text size="1" style={{ color: 'var(--slate-11)' }}>
                Refreshing options…
              </Text>
            </Flex>
          ) : null}
          {filteredOptions.map((option) => (
            <Flex
              key={option.value}
              align="center"
              gap="2"
              onClick={() => toggleOption(option.value)}
              style={{
                padding: '6px 8px',
                borderRadius: 'var(--radius-1)',
                cursor: 'pointer',
                backgroundColor: selectedValues.includes(option.value)
                  ? 'var(--gray-a3)'
                  : 'transparent',
              }}
            >
              <Checkbox
                size="1"
                checked={selectedValues.includes(option.value)}
                onCheckedChange={() => toggleOption(option.value)}
                onClick={(e) => e.stopPropagation()}
                style={{ cursor: 'pointer' }}
              />
              {option.customIcon ? (
                option.customIcon
              ) : option.icon ? (
                <MaterialIcon
                  name={option.icon}
                  size={16}
                  color={option.iconColor || 'var(--slate-11)'}
                />
              ) : null}
              <Text size="2" style={{ color: 'var(--slate-12)' }}>
                {option.label}
              </Text>
            </Flex>
          ))}
          {isLoadingMore && (
            <Flex align="center" justify="center" gap="2" style={{ padding: '8px' }}>
              <Spinner size={12} />
              <Text size="1" style={{ color: 'var(--slate-9)' }}>
                Loading more…
              </Text>
            </Flex>
          )}
          {filteredOptions.length === 0 && !isLoadingMore && !isLoadingOptions && (
            <Text size="2" style={{ color: 'var(--slate-9)', padding: '8px' }}>
              No results found
            </Text>
          )}
        </Flex>
      </Popover.Content>
    </Popover.Root>
  );
}
