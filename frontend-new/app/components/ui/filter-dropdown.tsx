'use client';

import React, { useState, useMemo, useRef, useCallback, useEffect } from 'react';
import { Flex, Box, Text, Badge, Button, Popover, Checkbox, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

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
  pluralLabel,
  onSearch,
  onLoadMore,
  isLoadingMore = false,
  hasMore = false,
}: FilterDropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const listRef = useRef<HTMLDivElement>(null);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  // Reset search when dropdown closes
  useEffect(() => {
    if (!isOpen) {
      setSearchQuery('');
      if (isServerSearch) onSearch!('');
    }
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

  return (
    <Popover.Root open={disabled ? false : isOpen} onOpenChange={disabled ? undefined : setIsOpen}>
      <Popover.Trigger>
        {hasSelection ? (
          <Flex
            align="center"
            style={{
              height: '26px',
              border: '1px solid var(--gray-a7)',
              borderRadius: 'var(--radius-2)',
              backgroundColor: 'var(--gray-a3)',
              cursor: 'pointer',
              overflow: 'hidden',
            }}
          >
            {/* Segment 1: icon + label */}
            <Flex
              align="center"
              style={{
                padding: icon ? '0 8px 0 8px' : '0 8px',
                borderRight: '1px solid var(--gray-a7)',
                height: '100%',
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
                height: '100%',
              }}
            >
              <Text size="1" style={{ color: 'var(--gray-11)', whiteSpace: 'nowrap' }}>
                is any of
              </Text>
            </Flex>

            {/* Segment 3: mini icons + count label */}
            <Flex
              align="center"
              style={{
                padding: '0 8px',
                borderRight: '1px solid var(--gray-a7)',
                height: '100%',
                gap: '2px',
              }}
            >
              {selectedValues.slice(0, 4).map((val) => {
                const opt = options.find((o) => o.value === val);
                if (!opt || !opt.icon) return null;
                return (
                  <MaterialIcon
                    key={val}
                    name={opt.icon}
                    size={12}
                    color="var(--gray-11)"
                  />
                );
              })}
              <Badge color="jade" variant="soft" size="1">
                {selectedValues.length}
              </Badge>
              <Text size="1" style={{ color: 'var(--gray-11)', whiteSpace: 'nowrap' }}>
                {pluralLabel ?? `${label}s`}
              </Text>
            </Flex>

            {/* Segment 4: clear button */}
            <Flex
              align="center"
              justify="center"
              onClick={clearSelection}
              style={{
                padding: '0 4px',
                height: '100%',
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

      <Popover.Content
        side="bottom"
        align="start"
        sideOffset={4}
        style={{
          padding: '8px',
          minWidth: '180px',
          maxWidth: '240px',
          backgroundColor: 'var(--olive-2)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-1)',
          boxShadow: "0 12px 32px -16px var(--slate-a5, rgba(217, 237, 254, 0.15)), 0 12px 60px 0 var(--black-a3, rgba(0, 0, 0, 0.15))"
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
                style={{cursor:'pointer'}}
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
            <Text size="1" style={{ color: 'var(--slate-9)', padding: '8px', textAlign: 'center' }}>
              Loading...
            </Text>
          )}
          {filteredOptions.length === 0 && !isLoadingMore && (
            <Text size="2" style={{ color: 'var(--slate-9)', padding: '8px' }}>
              No results found
            </Text>
          )}
        </Flex>
      </Popover.Content>
    </Popover.Root>
  );
}
