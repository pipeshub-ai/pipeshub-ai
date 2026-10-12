'use client';

import { Fragment } from 'react';
import { useTranslation } from 'react-i18next';
import { DropdownMenu, Flex, IconButton, Text } from '@radix-ui/themes';
import { ConnectorIcon, resolveConnectorType } from '@/app/components/ui/ConnectorIcon';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { mcpStatusLook, type McpConnectionState } from '../../connection-state';
import type { McpMyServerEntry } from '../../types';

/** The edit panel's title: the server's icon and name, and how it is doing. */
export function McpPanelTitle({ instance, state }: { instance: McpMyServerEntry; state: McpConnectionState | 'disabled' }) {
  const { t } = useTranslation();
  const look = state === 'disabled' ? { color: 'gray' as const, key: 'workspace.mcpServers.status.disabled' } : mcpStatusLook(state);
  return (
    <Flex align="center" gap="3" style={{ minWidth: 0 }}>
      <Flex
        align="center"
        justify="center"
        style={{
          width: 32,
          height: 32,
          borderRadius: 'var(--radius-2)',
          backgroundColor: 'var(--gray-a3)',
          flexShrink: 0,
        }}
      >
        <ConnectorIcon type={resolveConnectorType(instance.typeId || instance.name)} size={18} color="var(--gray-11)" />
      </Flex>
      <Flex direction="column" style={{ minWidth: 0 }}>
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }} truncate>
          {instance.name}
        </Text>
        <Flex align="center" gap="1" data-testid="mcp-panel-status">
          <span
            aria-hidden
            style={{ width: 7, height: 7, borderRadius: '50%', backgroundColor: `var(--${look.color}-9)`, flexShrink: 0 }}
          />
          <Text size="1" style={{ color: `var(--${look.color}-11)` }}>
            {t(look.key)}
          </Text>
        </Flex>
      </Flex>
    </Flex>
  );
}

export type McpMenuEntry =
  | {
      kind: 'item';
      id: string;
      icon: string;
      label: string;
      /** A second, quieter line saying what the action does. */
      description?: string;
      danger?: boolean;
      onSelect: () => void;
    }
  | { kind: 'info'; id: string; icon: string; label: string }
  | { kind: 'separator'; id: string };

/** "More actions" for the server. Drawn into `container` so it opens above the drawer. */
export function McpServerActionsMenu({ entries, container }: { entries: McpMenuEntry[]; container: HTMLElement | null }) {
  const { t } = useTranslation();
  if (entries.every((entry) => entry.kind !== 'item')) return null;
  return (
    <DropdownMenu.Root modal={false}>
      <DropdownMenu.Trigger>
        <IconButton variant="ghost" color="gray" size="2" aria-label={t('workspace.mcpServers.configPanel.menu.label')}>
          <MaterialIcon name="more_horiz" size={18} color="var(--slate-11)" />
        </IconButton>
      </DropdownMenu.Trigger>
      <DropdownMenu.Content align="end" container={container ?? undefined} style={{ minWidth: 260 }}>
        {entries.map((entry) => (
          <Fragment key={entry.id}>
            {entry.kind === 'separator' ? (
              <DropdownMenu.Separator />
            ) : entry.kind === 'info' ? (
              <DropdownMenu.Item disabled>
                <Flex align="center" gap="2">
                  <MaterialIcon name={entry.icon} size={16} color="var(--gray-10)" />
                  <Text size="1" style={{ color: 'var(--gray-11)' }}>
                    {entry.label}
                  </Text>
                </Flex>
              </DropdownMenu.Item>
            ) : (
              <DropdownMenu.Item
                color={entry.danger ? 'red' : undefined}
                onSelect={entry.onSelect}
                style={entry.description ? { height: 'auto', paddingTop: 6, paddingBottom: 6 } : undefined}
              >
                <Flex align="start" gap="2">
                  <MaterialIcon name={entry.icon} size={16} color={entry.danger ? 'var(--red-11)' : 'var(--gray-11)'} />
                  <Flex direction="column">
                    <Text size="2">{entry.label}</Text>
                    {entry.description && (
                      <Text size="1" style={{ color: entry.danger ? 'var(--red-10)' : 'var(--gray-10)' }}>
                        {entry.description}
                      </Text>
                    )}
                  </Flex>
                </Flex>
              </DropdownMenu.Item>
            )}
          </Fragment>
        ))}
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}
