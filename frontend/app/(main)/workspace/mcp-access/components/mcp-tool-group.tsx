'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Switch, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { ToolGroup } from '../tool-groups';

export interface McpToolGroupProps {
  group: ToolGroup;
  /** Tools of the group the policy lets the server offer. */
  offered: number;
  total: number;
  /** Every tool of the group that can be on is on. */
  allOn: boolean;
  /** No tool of the group can be switched (MCP off, or all of them held off by another setting). */
  switchDisabled: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAllChange: (on: boolean) => void;
  children: React.ReactNode;
}

/** A section of related tools, with a count and a switch for all of them. */
export function McpToolGroup({
  group,
  offered,
  total,
  allOn,
  switchDisabled,
  open,
  onOpenChange,
  onAllChange,
  children,
}: McpToolGroupProps) {
  const { t } = useTranslation();
  const label = t(`workspace.mcpAccess.tools.groups.${group}.label`);
  const bodyId = `mcp-tool-group-${group}`;

  return (
    <Box
      style={{
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        background: 'var(--olive-2)',
        padding: '0 var(--space-4)',
      }}
    >
      <Flex align="center" gap="3" style={{ minHeight: 56, padding: 'var(--space-2) 0' }}>
        <Button
          variant="ghost"
          color="gray"
          size="2"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => onOpenChange(!open)}
          style={{ flex: 1, minWidth: 0, justifyContent: 'flex-start', height: 'auto', padding: 'var(--space-2)', margin: 0, textAlign: 'left' }}
        >
          <span aria-hidden="true" style={{ display: 'inline-flex' }}>
            <MaterialIcon name={open ? 'expand_less' : 'expand_more'} size={18} color="var(--gray-11)" />
          </span>
          <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {label}
              <Text weight="regular" style={{ color: 'var(--slate-11)' }}>
                {' · '}
                {t('workspace.mcpAccess.tools.groupCount', { offered, total })}
              </Text>
            </Text>
            <Text size="1" weight="regular" style={{ color: 'var(--slate-11)', lineHeight: '16px', whiteSpace: 'normal' }}>
              {t(`workspace.mcpAccess.tools.groups.${group}.description`)}
            </Text>
          </Flex>
        </Button>
        <Switch
          size="1"
          checked={allOn}
          disabled={switchDisabled}
          onCheckedChange={onAllChange}
          aria-label={t('workspace.mcpAccess.tools.groupSwitch', { group: label })}
          style={{ flexShrink: 0, cursor: switchDisabled ? 'default' : 'pointer' }}
        />
      </Flex>
      {open && <Box id={bodyId}>{children}</Box>}
    </Box>
  );
}
