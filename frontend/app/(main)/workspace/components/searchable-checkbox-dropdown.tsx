'use client';

import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { Flex, Box, Text, Avatar } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';

// ========================================
// Types
// ========================================

export interface CheckboxOption {
  id: string;
  label: string;
  /** Optional subtitle (e.g. email) shown below the label */
  subtitle?: string;
  /** Data URI for profile picture */
  profilePicture?: string;
}

interface SearchableCheckboxDropdownProps {
  /** Available options */
  options: CheckboxOption[];
  /** Currently selected option IDs */
  selectedIds: string[];
  /** Called when selection changes */
  onSelectionChange: (ids: string[]) => void;
  /** Placeholder text */
  placeholder?: string;
  /** Text shown when no options match search */
  emptyText?: string;
  /** Whether the dropdown is disabled */
  disabled?: boolean;
  /** Show avatar + subtitle for each option (user-style rows) */
  showAvatar?: boolean;
  /** Server-side search callback. When provided, search is handled externally. */
  onSearch?: (query: string) => void;
  /** Called when user scrolls to bottom of the options list (infinite scroll). */
  onLoadMore?: () => void;
  /** Whether more options are being loaded */
  isLoadingMore?: boolean;
  /** Whether there are more options to load */
  hasMore?: boolean;
}

// ========================================
// Component
// ========================================

function getInitials(name: string): string {
  if (!name) return '?';
  const parts = name.trim().split(/[\s._-]+/);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

export function SearchableCheckboxDropdown({
  options,
  selectedIds,
  onSelectionChange,
  placeholder = 'Search or select',
  emptyText = 'No options available',
  disabled = false,
  showAvatar = false,
  onSearch,
  onLoadMore,
  isLoadingMore = false,
  hasMore = false,
}: SearchableCheckboxDropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [openDirection, setOpenDirection] = useState<'down' | 'up'>('down');
  const dropdownRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const chipsContainerRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isServerSearch = !!onSearch;

  // Close on click outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    }
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  // Auto-scroll chips container to bottom when selection changes (for wrapped layout)
  useEffect(() => {
    if (chipsContainerRef.current) {
      chipsContainerRef.current.scrollTop =
        chipsContainerRef.current.scrollHeight;
    }
  }, [selectedIds]);

  // Filter options locally only when NOT using server search
  const filteredOptions = useMemo(() => {
    if (isServerSearch) return options;
    if (!searchQuery.trim()) return options;
    const q = searchQuery.toLowerCase();
    return options.filter(
      (o) =>
        o.label.toLowerCase().includes(q) ||
        (o.subtitle && o.subtitle.toLowerCase().includes(q))
    );
  }, [options, searchQuery, isServerSearch]);

  // Debounced server search
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

  // Infinite scroll
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

  const toggleOption = useCallback(
    (id: string) => {
      if (selectedIds.includes(id)) {
        onSelectionChange(selectedIds.filter((sid) => sid !== id));
      } else {
        onSelectionChange([...selectedIds, id]);
      }
      // Clear search text after selection so the full list is visible again
      setSearchQuery('');
    },
    [selectedIds, onSelectionChange]
  );

  const removeChip = useCallback(
    (id: string) => {
      onSelectionChange(selectedIds.filter((sid) => sid !== id));
    },
    [selectedIds, onSelectionChange]
  );

  const handleTriggerClick = () => {
    if (!disabled) {
      // Determine whether to open up or down based on available space
      if (dropdownRef.current) {
        const rect = dropdownRef.current.getBoundingClientRect();
        const spaceBelow = window.innerHeight - rect.bottom;
        const dropdownHeight = 220; // maxHeight (216) + margin (4)
        setOpenDirection(spaceBelow < dropdownHeight ? 'up' : 'down');
      }
      setIsOpen(true);

      // Focus the input and scroll the trigger into view after dropdown renders
      setTimeout(() => {
        inputRef.current?.focus();
        dropdownRef.current?.scrollIntoView({
          behavior: 'smooth',
          block: 'nearest',
        });
      }, 0);
    }
  };

  const handleSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (
      e.key === 'Backspace' &&
      searchQuery === '' &&
      selectedIds.length > 0
    ) {
      // Remove last selected chip
      onSelectionChange(selectedIds.slice(0, -1));
    }
    if (e.key === 'Escape') {
      setIsOpen(false);
    }
  };

  const selectedOptions = options.filter((o) => selectedIds.includes(o.id));

  return (
    <Box ref={dropdownRef} style={{ position: 'relative' }}>
      {/* Trigger */}
      <Box
        onClick={handleTriggerClick}
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 'var(--space-1)',
          width: '100%',
          minHeight: 'var(--space-8)',
          padding: isOpen ? 3 : 4,
          backgroundColor: 'var(--color-surface)',
          border: `${isOpen ? 2 : 1}px solid ${isOpen ? 'var(--accent-8)' : 'var(--slate-a5)'}`,
          borderRadius: 'var(--radius-2)',
          cursor: disabled ? 'not-allowed' : 'pointer',
          boxSizing: 'border-box',
          opacity: disabled ? 0.6 : 1,
        }}
      >
        {/* Chips + input area */}
        <Flex
          ref={chipsContainerRef}
          align="start"
          gap="1"
          style={{
            flex: 1,
            flexWrap: 'wrap',
            maxHeight: 120,
            overflowY: 'auto',
            minWidth: 0,
          }}
        >
          {selectedOptions.map((opt) => (
            <Flex
              key={opt.id}
              align="center"
              gap="1"
              style={{
                backgroundColor: 'var(--slate-a3)',
                borderRadius: 'var(--radius-2)',
                padding: '2px 8px',
                flexShrink: 0,
                whiteSpace: 'nowrap',
              }}
            >
              <Text
                size="2"
                weight="medium"
                style={{ color: 'var(--slate-12)' }}
              >
                {opt.label}
              </Text>
              <Box
                onClick={(e) => {
                  e.stopPropagation();
                  removeChip(opt.id);
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  cursor: 'pointer',
                  flexShrink: 0,
                }}
              >
                <MaterialIcon
                  name="close"
                  size={14}
                  color="var(--slate-11)"
                />
              </Box>
            </Flex>
          ))}

          {isOpen ? (
            <input
              ref={inputRef}
              type="text"
              value={searchQuery}
              onChange={(e) => handleSearchChange(e.target.value)}
              onKeyDown={handleSearchKeyDown}
              placeholder={selectedIds.length === 0 ? placeholder : ''}
              style={{
                border: 'none',
                outline: 'none',
                backgroundColor: 'transparent',
                fontSize: 14,
                lineHeight: '20px',
                fontFamily: 'var(--default-font-family)',
                color: 'var(--slate-12)',
                flex: 1,
                minWidth: 80,
                padding: '2px 4px',
              }}
            />
          ) : (
            selectedIds.length === 0 && (
              <Text
                size="2"
                style={{
                  color: 'var(--slate-a9)',
                  padding: '2px 4px',
                }}
              >
                {placeholder}
              </Text>
            )
          )}
        </Flex>

        {/* Chevron */}
        <Box
          style={{
            display: 'flex',
            alignItems: 'center',
            flexShrink: 0,
            height: 'var(--space-6)',
            marginTop: 2,
          }}
        >
          <MaterialIcon
            name={isOpen ? 'expand_less' : 'expand_more'}
            size={16}
            color="var(--slate-9)"
          />
        </Box>
      </Box>

      {/* Dropdown list */}
      {isOpen && (
        <Box
          ref={listRef}
          className="no-scrollbar"
          onScroll={handleScroll}
          style={{
            position: 'absolute',
            ...(openDirection === 'down'
              ? { top: '100%', marginTop: 'var(--space-1)' }
              : { bottom: '100%', marginBottom: 'var(--space-1)' }),
            left: 0,
            right: 0,
            maxHeight: 216,
            overflowY: 'auto',
            backgroundColor: 'var(--olive-2)',
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            boxShadow: '0 12px 32px -16px var(--slate-a5, rgba(217, 237, 254, 0.15)), 0 12px 60px 0 var(--black-3, rgba(0, 0, 0, 0.15))',
            zIndex: 100,
          }}
        >
          {filteredOptions.length === 0 && !isLoadingMore ? (
            <Flex
              align="center"
              justify="center"
              style={{ padding: 'var(--space-4)' }}
            >
              <Text size="2" style={{ color: 'var(--slate-9)' }}>
                {emptyText}
              </Text>
            </Flex>
          ) : (
            filteredOptions.map((option) => {
              const isChecked = selectedIds.includes(option.id);

              return (
                <Flex
                  key={option.id}
                  align="center"
                  gap="3"
                  onClick={() => toggleOption(option.id)}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLElement).style.backgroundColor =
                      'var(--slate-a3)';
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLElement).style.backgroundColor =
                      'transparent';
                  }}
                  style={{
                    padding: 'var(--space-2) var(--space-4)',
                    cursor: 'pointer',
                  }}
                >
                  {/* Custom checkbox */}
                  <Box
                    style={{
                      width: 'var(--space-4)',
                      height: 'var(--space-4)',
                      borderRadius: 'var(--radius-1)',
                      border: isChecked
                        ? 'none'
                        : '1px solid var(--slate-a7)',
                      backgroundColor: isChecked
                        ? 'var(--emerald-9)'
                        : 'transparent',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      flexShrink: 0,
                    }}
                  >
                    {isChecked && (
                      <MaterialIcon
                        name="check"
                        size={12}
                        color="white"
                      />
                    )}
                  </Box>

                  {showAvatar ? (
                    <Flex align="center" gap="2" style={{ flex: 1, minWidth: 0 }}>
                      <Avatar
                        size="2"
                        variant="soft"
                        src={option.profilePicture}
                        fallback={getInitials(option.label)}
                        style={{
                          width: 'var(--space-7)',
                          height: 'var(--space-7)',
                          flexShrink: 0,
                        }}
                      />
                      <Flex direction="column" style={{ minWidth: 0 }}>
                        <Text
                          size="2"
                          weight="medium"
                          style={{
                            color: 'var(--slate-12)',
                            whiteSpace: 'nowrap',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                          }}
                        >
                          {option.label}
                        </Text>
                        {option.subtitle && (
                          <Text
                            size="1"
                            style={{
                              color: 'var(--slate-11)',
                              whiteSpace: 'nowrap',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                            }}
                          >
                            {option.subtitle}
                          </Text>
                        )}
                      </Flex>
                    </Flex>
                  ) : (
                    <Text
                      size="2"
                      style={{ color: 'var(--slate-12)' }}
                    >
                      {option.label}
                    </Text>
                  )}
                </Flex>
              );
            })
          )}
          {isLoadingMore && (
            <Flex align="center" justify="center" gap="2" style={{ padding: '8px' }}>
              <Spinner size={12} />
              <Text size="1" style={{ color: 'var(--slate-9)' }}>Loading...</Text>
            </Flex>
          )}
        </Box>
      )}
    </Box>
  );
}

export type { SearchableCheckboxDropdownProps };
