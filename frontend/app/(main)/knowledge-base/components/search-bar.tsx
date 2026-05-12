'use client';

import React, { useRef, useEffect } from 'react';
import { Flex, IconButton, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useTranslation } from 'react-i18next';

interface SearchBarProps {
  value: string;
  onChange: (value: string) => void;
  onClose: () => void;
  placeholder?: string;
}

export function SearchBar({
  value,
  onChange,
  onClose,
  placeholder,
}: SearchBarProps) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const defaultPlaceholder = placeholder || t('kb.searchPlaceholder');

  // Auto-focus input when search bar opens
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      onClose();
    }
  };

  // Validation: show error when input has 1 character (minimum is 2)
  const showValidationError = value.length === 1;

  return (
    <Flex
      direction="column"
      style={{
        backgroundColor: 'var(--effects-translucent)',
        backdropFilter: 'blur(8px)',
        borderBottom: '1px solid var(--olive-3)',
      }}
    >
      <Flex
        align="center"
        style={{
          height: 'var(--space-10)',
          padding: 'var(--space-1) var(--space-3) var(--space-1) var(--space-2)',
        }}
      >
        <Flex
          align="center"
          gap="2"
          style={{
            flex: 1,
            height: 'var(--space-6)',
            padding: '0 var(--space-2)',
            backgroundColor: 'var(--slate-a3)',
            borderRadius: 'var(--radius-1)',
            border: showValidationError ? '1px solid var(--red-8)' : 'none',
          }}
        >
          {/* Search Icon */}
          <MaterialIcon name="search" size={16} color="var(--slate-12)" />

          {/* Search Input */}
          <input
            ref={inputRef}
            type="text"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={defaultPlaceholder}
            className="kb-search-input"
            style={{
              flex: 1,
              height: '24px',
              border: 'none',
              outline: 'none',
              backgroundColor: 'transparent',
              color: 'var(--slate-12)',
              fontSize: '14px',
              fontFamily: 'inherit',
              fontWeight: 500,
            }}
          />

          {/* Close Button */}
          <IconButton
            variant="ghost"
            size="1"
            color="gray"
            onClick={onClose}
            style={{ cursor: 'pointer' }}
          >
            <MaterialIcon name="close" size={16} color="var(--olive-11)" />
          </IconButton>
        </Flex>
      </Flex>

      {/* Validation message */}
      {showValidationError && (
        <Flex
          style={{
            padding: '0 var(--space-3) var(--space-2) var(--space-3)',
          }}
        >
          <Text size="1" color="red">
            {t('kb.searchMinChars')}
          </Text>
        </Flex>
      )}
    </Flex>
  );
}
