'use client';

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Flex, Box, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

// ========================================
// Types
// ========================================

export interface SelectOption {
  value: string;
  label: string;
  description?: string;
}

interface SelectDropdownProps {
  /** Currently selected value */
  value: string | null;
  /** Callback when value changes */
  onChange: (value: string) => void;
  /** Available options */
  options: SelectOption[];
  /** Placeholder when no value selected */
  placeholder?: string;
  /** Whether the dropdown is disabled */
  disabled?: boolean;
}

// ========================================
// Component
// ========================================

export function SelectDropdown({
  value,
  onChange,
  options,
  placeholder = 'Select an option',
  disabled = false,
}: SelectDropdownProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close on click outside
  const handleClickOutside = useCallback(
    (e: MouseEvent) => {
      if (
        menuRef.current &&
        !menuRef.current.contains(e.target as Node) &&
        triggerRef.current &&
        !triggerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    },
    []
  );

  // Close on Escape
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') setOpen(false);
  }, []);

  useEffect(() => {
    if (open) {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('keydown', handleKeyDown);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open, handleClickOutside, handleKeyDown]);

  const selectedOption = options.find((o) => o.value === value);

  return (
    <Box style={{ position: 'relative' }}>
      {/* Trigger */}
      <button
        ref={triggerRef}
        type="button"
        onClick={() => {
          if (!disabled) setOpen((v) => !v);
        }}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          width: '100%',
          height: 32,
          padding: '6px 8px',
          backgroundColor: 'var(--color-surface)',
          border: '1px solid var(--slate-a5)',
          borderRadius: 'var(--radius-2)',
          fontSize: 14,
          fontFamily: 'var(--default-font-family)',
          color: selectedOption ? 'var(--slate-12)' : 'var(--slate-a9)',
          cursor: disabled ? 'not-allowed' : 'pointer',
          boxSizing: 'border-box',
          opacity: disabled ? 0.6 : 1,
        }}
      >
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {selectedOption ? selectedOption.label : placeholder}
        </span>
        <MaterialIcon
          name={open ? 'expand_less' : 'expand_more'}
          size={16}
          color="var(--slate-9)"
        />
      </button>

      {/* Dropdown menu */}
      {open && (
        <Box
          ref={menuRef}
          style={{
            position: 'absolute',
            top: 'calc(100% + 4px)',
            left: 0,
            right: 0,
            backgroundColor: 'var(--olive-2)',
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            boxShadow:
              '0px 12px 32px -16px rgba(0, 9, 50, 0.12), 0px 12px 60px 0px rgba(0, 0, 0, 0.15)',
            zIndex: 100,
            overflow: 'hidden',
          }}
        >
          {options.map((option, index) => {
            const isSelected = option.value === value;
            const isFirst = index === 0;
            const isLast = index === options.length - 1;

            return (
              <Flex
                key={option.value}
                align="center"
                justify="between"
                onClick={() => {
                  onChange(option.value);
                  setOpen(false);
                }}
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
                  paddingTop: isFirst ? 12 : 8,
                  paddingBottom: isLast ? 12 : 8,
                  cursor: 'pointer',
                }}
              >
                <Flex direction="column" gap="1">
                  <Text
                    size="2"
                    weight={isSelected ? 'medium' : 'regular'}
                    style={{ color: 'var(--slate-12)' }}
                  >
                    {option.label}
                  </Text>
                  {option.description && (
                    <Text size="1" style={{ color: 'var(--slate-11)' }}>
                      {option.description}
                    </Text>
                  )}
                </Flex>

                {/* Radio indicator */}
                <Box
                  style={{
                    width: 16,
                    height: 16,
                    borderRadius: '50%',
                    border: `2px solid ${isSelected ? 'var(--accent-9)' : 'var(--slate-a7)'}`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexShrink: 0,
                  }}
                >
                  {isSelected && (
                    <Box
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: '50%',
                        backgroundColor: 'var(--accent-9)',
                      }}
                    />
                  )}
                </Box>
              </Flex>
            );
          })}
        </Box>
      )}
    </Box>
  );
}

export type { SelectDropdownProps };
