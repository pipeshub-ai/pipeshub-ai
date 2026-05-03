'use client';

import React from 'react';
import { Flex, Heading, Text, Button, TextField, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { Breakpoint } from '@/lib/hooks/use-breakpoint';

export interface Oauth2PageHeaderProps {
  title: string;
  subtitle: string;
  searchPlaceholder: string;
  searchValue: string;
  onSearchChange: (value: string) => void;
  newApplicationLabel: string;
  onNewApplication: () => void;
  docsOpenLabel: string;
  /** Current Radix Themes breakpoint tier (from `useBreakpoint`). */
  breakpoint: Breakpoint;
}

function isBelowMd(breakpoint: Breakpoint): boolean {
  return (
    breakpoint === 'initial' ||
    breakpoint === 'xs' ||
    breakpoint === 'sm'
  );
}

function isInitialTier(breakpoint: Breakpoint): boolean {
  return breakpoint === 'initial';
}

/**
 * Title + actions + search for OAuth 2.0 settings.
 * Stacks on viewports below Radix `md` (1024px); row layout at `md` and up.
 */
export function Oauth2PageHeader({
  title,
  subtitle,
  searchPlaceholder,
  searchValue,
  onSearchChange,
  newApplicationLabel,
  onNewApplication,
  docsOpenLabel,
  breakpoint,
}: Oauth2PageHeaderProps) {
  const compact = isBelowMd(breakpoint);
  const veryNarrow = isInitialTier(breakpoint);

  const actionsRow = (
    <Flex
      wrap="wrap"
      align="center"
      justify={compact ? 'start' : 'end'}
      gap="3"
      style={{ width: compact ? '100%' : 'auto' }}
    >
      <Button
        size="2"
        onClick={onNewApplication}
        style={{
          width: veryNarrow ? '100%' : undefined,
          flex: veryNarrow ? undefined : '0 0 auto',
        }}
      >
        <MaterialIcon name="add" size={16} color="currentColor" />
        {newApplicationLabel}
      </Button>
      <IconButton
        type="button"
        variant="outline"
        color="gray"
        size="2"
        aria-label={docsOpenLabel}
        onClick={() => window.open('https://docs.pipeshub.com/developer/oauth2', '_blank')}
      >
        <MaterialIcon name="open_in_new" size={16} color="var(--gray-11)" />
      </IconButton>
    </Flex>
  );

  const searchField = (
    <TextField.Root
      size="2"
      placeholder={searchPlaceholder}
      value={searchValue}
      onChange={(e) => onSearchChange(e.target.value)}
      style={{
        width: compact ? '100%' : '288px',
        maxWidth: '100%',
      }}
    >
      <TextField.Slot>
        <MaterialIcon name="search" size={16} color="var(--slate-9)" />
      </TextField.Slot>
    </TextField.Root>
  );

  const titleBlock = (
    <Flex direction="column" gap="2" style={{ minWidth: 0, maxWidth: '100%' }}>
      <Heading size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
        {title}
      </Heading>
      <Text size="2" style={{ color: 'var(--slate-11)', wordBreak: 'break-word' }}>
        {subtitle}
      </Text>
    </Flex>
  );

  const rightColumn = (
    <Flex
      direction="column"
      align={compact ? 'stretch' : 'end'}
      gap="3"
      style={{
        width: compact ? '100%' : 'auto',
        minWidth: compact ? undefined : 0,
        maxWidth: compact ? '100%' : undefined,
      }}
    >
      {actionsRow}
      {searchField}
    </Flex>
  );

  return (
    <Flex
      direction={compact ? 'column' : 'row'}
      align={compact ? 'stretch' : 'end'}
      justify="between"
      gap="4"
      style={{
        paddingTop: '64px',
        paddingBottom: '16px',
        width: '100%',
      }}
    >
      {titleBlock}
      {rightColumn}
    </Flex>
  );
}
