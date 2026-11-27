'use client';

import React, { useState } from 'react';
import { Flex } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';

/** The search box at the top of each filter panel tab. Every tab uses this one; only the placeholder differs. */
export function PanelSearchInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}) {
  const [focused, setFocused] = useState(false);
  return (
    <Flex
      align="center"
      gap="2"
      style={{
        flex: 1,
        minWidth: 0,
        height: 'var(--space-6)',
        flexShrink: 0,
        border: `1px solid ${focused ? 'var(--accent-11)' : 'var(--olive-3)'}`,
        borderRadius: 'var(--radius-2)',
        paddingLeft: 'var(--space-2)',
        paddingRight: 'var(--space-2)',
        backgroundColor: 'var(--slate-1)',
      }}
    >
      <MaterialIcon name="search" size={ICON_SIZES.PRIMARY} color="var(--slate-11)" />
      <input
        type="text"
        className="collections-search-input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        placeholder={placeholder}
        aria-label={placeholder}
        style={{
          flex: 1,
          minWidth: 0,
          border: 'none',
          outline: 'none',
          backgroundColor: 'transparent',
          color: 'var(--slate-12)',
          fontSize: 'var(--font-size-2)',
          fontFamily: 'inherit',
        }}
      />
    </Flex>
  );
}
