'use client';

import React from 'react';
import { Flex, Switch, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

export interface AgentCapabilitiesBarProps {
  internalSearch: boolean;
  webSearch: boolean;
  onToggleInternalSearch: (enabled: boolean) => void;
  onToggleWebSearch: (enabled: boolean) => void;
  /**
   * Whether the agent was built with internal search (knowledge / connectors).
   * `undefined` = universal mode (capability always available to toggle).
   * `false` = scoped agent does not have this capability; toggle is disabled.
   */
  agentHasInternalSearch?: boolean;
  /**
   * Whether the agent was built with web search.
   * `undefined` = universal mode (capability always available to toggle).
   * `false` = scoped agent does not have this capability; toggle is disabled.
   */
  agentHasWebSearch?: boolean;
  /**
   * `panel` (default) — header row inside the expanded resources panel: shows the
   * "Capabilities" label and a bottom divider.
   * `toolbar` — compact inline row for the main chat input toolbar: no label,
   * no divider, tighter gap so it sits next to other toolbar controls.
   */
  variant?: 'panel' | 'toolbar';
}

/** Thin horizontal bar with Indexed Data Search / Web Search toggles for agent mode. */
export function AgentCapabilitiesBar({
  internalSearch,
  webSearch,
  onToggleInternalSearch,
  onToggleWebSearch,
  agentHasInternalSearch,
  agentHasWebSearch,
  variant = 'panel',
}: AgentCapabilitiesBarProps) {
  const { t } = useTranslation();
  const isToolbar = variant === 'toolbar';

  const internalSearchDisabled = agentHasInternalSearch === false;
  const webSearchDisabled = agentHasWebSearch === false;

  const notConfiguredLabel = t('chat.agentCapabilities.notConfiguredOnAgent', {
    defaultValue: 'Not configured on this agent',
  });

  return (
    <Flex
      align="center"
      gap={isToolbar ? '3' : '4'}
      style={
        isToolbar
          ? { flexShrink: 0 }
          : {
              flexShrink: 0,
              paddingBottom: 'var(--space-2)',
              borderBottom: '1px solid var(--gray-4)',
            }
      }
    >
      {!isToolbar && (
        <Text size="1" weight="medium" style={{ color: 'var(--gray-10)', whiteSpace: 'nowrap' }}>
          {t('chat.agentCapabilities.label', { defaultValue: 'Capabilities' })}
        </Text>
      )}

      <Flex align="center" gap="3" style={{ flexWrap: isToolbar ? 'nowrap' : 'wrap' }}>
        {/* Indexed Data Search toggle */}
        <Tooltip
          content={
            internalSearchDisabled
              ? notConfiguredLabel
              : t('chat.agentCapabilities.internalSearchHint', {
                  defaultValue: 'Search your connected knowledge sources (connectors & collections)',
                })
          }
        >
          <Flex
            align="center"
            gap="1"
            style={{
              opacity: internalSearchDisabled ? 0.45 : 1,
              cursor: internalSearchDisabled ? 'not-allowed' : 'pointer',
            }}
            onClick={() => {
              if (!internalSearchDisabled) onToggleInternalSearch(!internalSearch);
            }}
          >
            <Switch
              size="1"
              checked={internalSearch && !internalSearchDisabled}
              disabled={internalSearchDisabled}
              onCheckedChange={(checked) => {
                if (!internalSearchDisabled) onToggleInternalSearch(checked);
              }}
              aria-label={t('chat.agentCapabilities.internalSearchAria', {
                defaultValue: 'Toggle indexed data search',
              })}
            />
            <Text
              size="1"
              style={{
                color: internalSearch && !internalSearchDisabled ? 'var(--gray-12)' : 'var(--gray-9)',
                userSelect: 'none',
                whiteSpace: 'nowrap',
              }}
            >
              {t('chat.agentCapabilities.internalSearch', { defaultValue: 'Indexed Data Search' })}
            </Text>
          </Flex>
        </Tooltip>

        {/* Web Search toggle */}
        <Tooltip
          content={
            webSearchDisabled
              ? notConfiguredLabel
              : t('chat.agentCapabilities.webSearchHint', {
                  defaultValue: 'Search the web and fetch URLs',
                })
          }
        >
          <Flex
            align="center"
            gap="1"
            style={{
              opacity: webSearchDisabled ? 0.45 : 1,
              cursor: webSearchDisabled ? 'not-allowed' : 'pointer',
            }}
            onClick={() => {
              if (!webSearchDisabled) onToggleWebSearch(!webSearch);
            }}
          >
            <Switch
              size="1"
              checked={webSearch && !webSearchDisabled}
              disabled={webSearchDisabled}
              onCheckedChange={(checked) => {
                if (!webSearchDisabled) onToggleWebSearch(checked);
              }}
              aria-label={t('chat.agentCapabilities.webSearchAria', {
                defaultValue: 'Toggle web search',
              })}
            />
            <Text
              size="1"
              style={{
                color: webSearch && !webSearchDisabled ? 'var(--gray-12)' : 'var(--gray-9)',
                userSelect: 'none',
                whiteSpace: 'nowrap',
              }}
            >
              {t('chat.agentCapabilities.webSearch', { defaultValue: 'Web Search' })}
            </Text>
          </Flex>
        </Tooltip>
      </Flex>
    </Flex>
  );
}
