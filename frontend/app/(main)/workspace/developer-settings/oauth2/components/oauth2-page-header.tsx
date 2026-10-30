'use client';

import React from 'react';
import { Box, Button, Flex, Heading, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { WorkspaceDocsButton } from '../../../components/workspace-docs-button';
import { MOBILE_PAGE_PADDING_TOP, PAGE_PADDING_TOP } from '../../../components/settings-page-container';

export interface Oauth2PageHeaderProps {
  title: string;
  subtitle: string;
  searchPlaceholder: string;
  searchValue: string;
  onSearchChange: (value: string) => void;
  newApplicationLabel: string;
  onNewApplication: () => void;
}

/**
 * Title and subtitle on the left, New + Docs on the right (the workspace catalog header),
 * search on its own row below. Wraps under the title on narrow screens.
 */
export function Oauth2PageHeader({
  title,
  subtitle,
  searchPlaceholder,
  searchValue,
  onSearchChange,
  newApplicationLabel,
  onNewApplication,
}: Oauth2PageHeaderProps) {
  const isMobile = useIsMobile();

  return (
    <Flex
      direction="column"
      gap="4"
      style={{ paddingTop: isMobile ? MOBILE_PAGE_PADDING_TOP : PAGE_PADDING_TOP, paddingBottom: 16, width: '100%' }}
    >
      <Flex justify="between" align="start" wrap="wrap" gap="3" style={{ width: '100%' }}>
        <Flex direction="column" gap="2" style={{ flex: '1 1 240px', minWidth: 0 }}>
          <Heading size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {title}
          </Heading>
          <Text size="2" style={{ color: 'var(--slate-11)', wordBreak: 'break-word' }}>
            {subtitle}
          </Text>
        </Flex>
        <Flex align="center" gap="2" wrap="wrap" style={{ flexShrink: 0 }}>
          <Button size="2" className="touch-hit" onClick={onNewApplication} style={{ cursor: 'pointer' }}>
            <MaterialIcon name="add" size={16} color="currentColor" />
            {newApplicationLabel}
          </Button>
          <WorkspaceDocsButton href="https://docs.pipeshub.com/developer/oauth2" />
        </Flex>
      </Flex>
      <Box width={{ initial: '100%', sm: '288px' }}>
        <TextField.Root
          size="2"
          placeholder={searchPlaceholder}
          value={searchValue}
          onChange={(e) => onSearchChange(e.target.value)}
          style={{ width: '100%' }}
        >
          <TextField.Slot>
            <MaterialIcon name="search" size={16} color="var(--slate-9)" />
          </TextField.Slot>
        </TextField.Root>
      </Box>
    </Flex>
  );
}
