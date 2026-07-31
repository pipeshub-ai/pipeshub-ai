'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

/** The three top-level sections of the resources panel. */
export type ResourceTab = 'connectors' | 'collections' | 'actions';

export interface ResourceTabMeta {
  id: ResourceTab;
  icon: string;
  labelKey: string;
  defaultLabel: string;
}

/** Single source of truth for the three resource-panel tabs. */
export const RESOURCE_TABS: readonly ResourceTabMeta[] = [
  {
    id: 'connectors',
    icon: 'hub',
    labelKey: 'nav.connectors',
    defaultLabel: 'Connectors',
  },
  {
    id: 'collections',
    icon: 'folder',
    labelKey: 'nav.collections',
    defaultLabel: 'Collections',
  },
  {
    id: 'actions',
    icon: 'bolt',
    labelKey: 'chat.agentResources.actionsTab',
    defaultLabel: 'Actions',
  },
];

export interface ResourceTabSwitcherProps {
  value: ResourceTab;
  onChange: (tab: ResourceTab) => void;
  /** Item count shown next to each tab's label — omitted per-tab when undefined. */
  counts?: Partial<Record<ResourceTab, number>>;
}

/** Segmented tab row shown inside both resource panels, above the search box. */
export function ResourceTabSwitcher({ value, onChange, counts }: ResourceTabSwitcherProps) {
  const { t } = useTranslation();
  return (
    <Flex align="center" gap="4" style={{ width: '100%', flexShrink: 0, borderBottom: '1px solid var(--gray-4)' }}>
      {RESOURCE_TABS.map((tab) => {
        const active = value === tab.id;
        const count = counts?.[tab.id];
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => onChange(tab.id)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--space-1)',
              background: 'none',
              border: 'none',
              borderBottom: active ? '2px solid var(--accent-9)' : '2px solid transparent',
              padding: 'var(--space-2) 0',
              marginBottom: '-1px',
              cursor: 'pointer',
            }}
          >
            <MaterialIcon name={tab.icon} size={14} color={active ? 'var(--accent-11)' : 'var(--gray-9)'} />
            <Text size="1" weight={active ? 'medium' : 'regular'} style={{ color: active ? 'var(--gray-12)' : 'var(--gray-9)' }}>
              {t(tab.labelKey, { defaultValue: tab.defaultLabel })}
              {typeof count === 'number' ? ` (${count})` : ''}
            </Text>
          </button>
        );
      })}
    </Flex>
  );
}
