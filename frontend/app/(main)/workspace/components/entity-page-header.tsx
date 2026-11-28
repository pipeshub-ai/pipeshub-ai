'use client';

import React from 'react';
import { Box, Flex, Heading, Text, Button, TextField, Tooltip } from '@radix-ui/themes';
import { useCanHover } from '@/lib/hooks/use-can-hover';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { MOBILE_PAGE_PADDING_TOP, PAGE_PADDING_TOP } from './settings-page-container';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

export interface EntityPageHeaderProps {
  /** Page title (e.g. "Users") */
  title: string;
  /** Shown beside the title, e.g. a Beta badge */
  badge?: React.ReactNode;
  /** Page subtitle */
  subtitle: string;
  /** Search input placeholder */
  searchPlaceholder?: string;
  /** Current search value; the search field shows only with `onSearchChange` */
  searchValue?: string;
  /** Callback when search input changes */
  onSearchChange?: (value: string) => void;
  /** CTA button label; the button shows only with `onCtaClick` */
  ctaLabel?: string;
  /** CTA button Material icon name */
  ctaIcon?: string;
  /** Callback when CTA is clicked */
  onCtaClick?: () => void;
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
 * 40px left padding; top padding matches the other settings pages (PAGE_PADDING_TOP).
 */
export function EntityPageHeader({
  title,
  badge,
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
  paddingTop: paddingTopProp,
}: EntityPageHeaderProps) {
  // Phones clear the fixed menu button, like every other settings page
  const isMobile = useIsMobile();
  const paddingTop = paddingTopProp ?? (isMobile ? MOBILE_PAGE_PADDING_TOP : PAGE_PADDING_TOP);
  const canHover = useCanHover();
  const ctaButton = onCtaClick === undefined ? null : (
    <Button
      size="2"
      className="touch-hit"
      onClick={onCtaClick}
      disabled={ctaDisabled}
      style={{ cursor: ctaDisabled ? 'not-allowed' : 'pointer' }}
    >
      {ctaIcon && <MaterialIcon name={ctaIcon} size={16} color="currentColor" />}
      {ctaLabel}
    </Button>
  );

  return (
    <Flex
      direction={{ initial: 'column', md: 'row' }}
      justify="between"
      align={{ initial: 'start', md: 'end' }}
      wrap="wrap"
      gap="3"
      style={{
        paddingTop,
        paddingBottom: 'var(--space-4)',
      }}
    >
      {/* Left: Title + Subtitle */}
      <Flex direction="column" gap="2" style={{ flex: '1 1 240px', minWidth: 0 }}>
        <Flex align="center" gap="2" wrap="wrap">
          <Heading size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {title}
          </Heading>
          {badge}
        </Flex>
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          {subtitle}
        </Text>
      </Flex>

      {/* Right: Search + CTA */}
      <Flex align="center" gap="3" wrap="wrap" width={{ initial: '100%', md: 'auto' }} style={{ maxWidth: '100%' }}>
        {onSearchChange && (
          <Box width={{ initial: '100%', sm: '224px' }}>
            <TextField.Root
              size="2"
              placeholder={searchPlaceholder}
              value={searchValue ?? ''}
              onChange={(e) => onSearchChange(e.target.value)}
            >
              <TextField.Slot>
                <MaterialIcon name="search" size={16} color="var(--slate-11)" />
              </TextField.Slot>
            </TextField.Root>
          </Box>
        )}

        {additionalActions}

        {ctaButton === null ? null : ctaTooltip ? (
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
