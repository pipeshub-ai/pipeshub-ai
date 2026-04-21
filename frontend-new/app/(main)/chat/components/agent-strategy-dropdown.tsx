'use client';

import React, { useMemo } from 'react';
import { DropdownMenu, Button, Text, Flex } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useTranslation } from 'react-i18next';
import type { AgentStrategy } from '@/chat/types';

/** Canonical strategy order for dropdowns and mode panels — single source of truth. */
export const AGENT_STRATEGIES: readonly AgentStrategy[] = [
  'auto',
  'quick',
  'verify',
  'deep',
];

/** Material Icons names (outlined set) per strategy */
export const AGENT_STRATEGY_ICONS: Record<AgentStrategy, string> = {
  auto: 'auto_awesome',
  quick: 'bolt',
  verify: 'fact_check',
  deep: 'psychology',
};

export interface AgentStrategyDropdownProps {
  value: AgentStrategy;
  onChange: (next: AgentStrategy) => void;
  disabled?: boolean;
  accentColor: string;
}

export function AgentStrategyDropdown({
  value,
  onChange,
  disabled,
  accentColor,
}: AgentStrategyDropdownProps) {
  const { t } = useTranslation();

  const triggerLabel = useMemo(
    () => t(`chat.agentStrategy.modes.${value}.short`),
    [t, value]
  );

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger disabled={disabled}>
        <Button
          type="button"
          variant="ghost"
          color="gray"
          size="2"
          disabled={disabled}
          title={t('chat.agentStrategy.triggerTitle')}
          style={{
            maxWidth: 'min(200px, 40vw)',
            cursor: disabled ? 'default' : 'pointer',
            flexShrink: 1,
          }}
        >
          <MaterialIcon name={AGENT_STRATEGY_ICONS[value]} size={16} color={accentColor} />
          <Text
            size="2"
            style={{
              color: 'var(--slate-12)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {triggerLabel}
          </Text>
          <MaterialIcon name="expand_more" size={16} color={accentColor} />
        </Button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Content
        size="2"
        sideOffset={4}
        align="end"
        style={{ minWidth: 'min(320px, calc(100vw - 32px))', maxWidth: '360px' }}
      >
        {AGENT_STRATEGIES.map((id) => (
          <DropdownMenu.Item
            key={id}
            data-agent-strategy-row=""
            onSelect={() => onChange(id)}
            style={{
              paddingTop: 'var(--space-2)',
              paddingBottom: 'var(--space-2)',
            }}
          >
            <Flex
              direction="row"
              gap="2"
              align="start"
              style={{ width: '100%', minWidth: 0, whiteSpace: 'normal' }}
            >
              <MaterialIcon
                name={AGENT_STRATEGY_ICONS[id]}
                size={20}
                color={accentColor}
                style={{ flexShrink: 0, marginTop: '1px' }}
              />
              <Flex
                direction="column"
                gap="1"
                align="start"
                style={{ flex: 1, minWidth: 0 }}
              >
                <Text
                  size="2"
                  weight="medium"
                  style={{ color: 'var(--slate-12)', whiteSpace: 'normal' }}
                >
                  {t(`chat.agentStrategy.modes.${id}.label`)}
                </Text>
                <Text
                  size="1"
                  style={{
                    color: 'var(--slate-11)',
                    lineHeight: 1.45,
                    whiteSpace: 'normal',
                    wordWrap: 'break-word',
                  }}
                >
                  {t(`chat.agentStrategy.modes.${id}.hint`)}
                </Text>
              </Flex>
            </Flex>
          </DropdownMenu.Item>
        ))}
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}
