'use client';

import React from 'react';
import { Flex, Heading, Text, Button, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

export interface EntityPageHeaderProps {
  /** Page title (e.g. "Users") */
  title: string;
  /** Page subtitle */
  subtitle: string;
  /** Search input placeholder */
  searchPlaceholder?: string;
  /** Current search value */
  searchValue: string;
  /** Callback when search input changes */
  onSearchChange: (value: string) => void;
  /** CTA button label */
  ctaLabel: string;
  /** CTA button Material icon name */
  ctaIcon: string;
  /** Callback when CTA is clicked */
  onCtaClick: () => void;
}

/**
 * EntityPageHeader — shared header for Users, Groups, Teams pages.
 *
 * Renders title + subtitle on the left, search + CTA button on the right.
 * Matches the Figma design: 40px left padding, 64px top padding, 136px height.
 */
export function EntityPageHeader({
  title,
  subtitle,
  searchPlaceholder = 'Search...',
  searchValue,
  onSearchChange,
  ctaLabel,
  ctaIcon,
  onCtaClick,
}: EntityPageHeaderProps) {
  return (
    <Flex
      justify="between"
      align="end"
      style={{
        paddingTop: '64px',
        paddingBottom: 'var(--space-4)',
      }}
    >
      {/* Left: Title + Subtitle */}
      <Flex direction="column" gap="2">
        <Heading size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {title}
        </Heading>
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          {subtitle}
        </Text>
      </Flex>

      {/* Right: Search + CTA */}
      <Flex align="center" gap="3">
        <TextField.Root
          size="2"
          placeholder={searchPlaceholder}
          value={searchValue}
          onChange={(e) => onSearchChange(e.target.value)}
          style={{ width: '224px' }}
        >
          <TextField.Slot>
            <MaterialIcon name="search" size={16} color="var(--slate-9)" />
          </TextField.Slot>
        </TextField.Root>

        <Button size="2" onClick={onCtaClick}>
          <MaterialIcon name={ctaIcon} size={16} color="currentColor" />
          {ctaLabel}
        </Button>
      </Flex>
    </Flex>
  );
}
