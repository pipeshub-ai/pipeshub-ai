'use client';

import React from 'react';
import { Box, Flex, Heading, Text, Button, TextField, Tooltip } from '@radix-ui/themes';
import { useCanHover } from '@/lib/hooks/use-can-hover';
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
  /** Optional extra actions rendered to the left of the primary CTA button */
  additionalActions?: React.ReactNode;
  /** When true, the CTA button is disabled (e.g. a prerequisite isn't met) */
  ctaDisabled?: boolean;
  /** Tooltip shown on hover — most useful to explain why the CTA is disabled */
  ctaTooltip?: string;
  /** Space above the title (default 64px). Pass a smaller value when a back link already sits above the header. */
  paddingTop?: React.CSSProperties['paddingTop'];
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
  additionalActions,
  ctaDisabled = false,
  ctaTooltip,
  paddingTop = '64px',
}: EntityPageHeaderProps) {
  const canHover = useCanHover();
  const ctaButton = (
    <Button
      size="2"
      className="touch-hit"
      onClick={onCtaClick}
      disabled={ctaDisabled}
      style={{ cursor: ctaDisabled ? 'not-allowed' : 'pointer' }}
    >
      <MaterialIcon name={ctaIcon} size={16} color="currentColor" />
      {ctaLabel}
    </Button>
  );

  return (
    <Flex
      direction={{ initial: 'column', md: 'row' }}
      justify="between"
      align={{ initial: 'start', md: 'end' }}
      gap="3"
      style={{
        paddingTop,
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
      <Flex align="center" gap="3" wrap="wrap" width={{ initial: '100%', md: 'auto' }} style={{ maxWidth: '100%' }}>
        <Box width={{ initial: '100%', sm: '224px' }}>
          <TextField.Root
            size="2"
            placeholder={searchPlaceholder}
            value={searchValue}
            onChange={(e) => onSearchChange(e.target.value)}
          >
            <TextField.Slot>
              <MaterialIcon name="search" size={16} color="var(--slate-9)" />
            </TextField.Slot>
          </TextField.Root>
        </Box>

        {additionalActions}

        {ctaTooltip ? (
          <Tooltip content={ctaTooltip}>
            <span style={{ display: 'inline-flex' }}>{ctaButton}</span>
          </Tooltip>
        ) : (
          ctaButton
        )}
        {ctaTooltip && !canHover && (
          <Text size="1" style={{ color: 'var(--slate-11)', width: '100%' }}>
            {ctaTooltip}
          </Text>
        )}
      </Flex>
    </Flex>
  );
}
