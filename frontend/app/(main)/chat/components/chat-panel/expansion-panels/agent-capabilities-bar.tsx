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
}

/** Thin horizontal bar with Internal Search / Web Search toggles for agent mode. */
export function AgentCapabilitiesBar({
  internalSearch,
  webSearch,
  onToggleInternalSearch,
  onToggleWebSearch,
  agentHasInternalSearch,
  agentHasWebSearch,
}: AgentCapabilitiesBarProps) {
  const { t } = useTranslation();

  const internalSearchDisabled = agentHasInternalSearch === false;
  const webSearchDisabled = agentHasWebSearch === false;

  const notConfiguredLabel = t('chat.agentCapabilities.notConfiguredOnAgent', {
    defaultValue: 'Not configured on this agent',
  });

  return (
    <Flex
      align="center"
      gap="4"
      style={{
        flexShrink: 0,
        paddingBottom: 'var(--space-2)',
        borderBottom: '1px solid var(--gray-4)',
      }}
    >
      <Text size="1" weight="medium" style={{ color: 'var(--gray-10)', whiteSpace: 'nowrap' }}>
        {t('chat.agentCapabilities.label', { defaultValue: 'Capabilities' })}
      </Text>

      <Flex align="center" gap="3" style={{ flexWrap: 'wrap' }}>
        {/* Internal Search toggle */}
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
                defaultValue: 'Toggle internal search',
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
              {t('chat.agentCapabilities.internalSearch', { defaultValue: 'Internal Search' })}
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
