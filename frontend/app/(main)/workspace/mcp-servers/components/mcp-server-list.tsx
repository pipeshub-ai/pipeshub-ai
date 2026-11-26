'use client';

import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { SegmentedTabs } from '@/app/components/ui/segmented-tabs';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { asArray } from '@/lib/utils/safe';
import { McpInstanceBadges } from '@/config';
import {
  McpStatusBadge,
  mcpConnectionState,
  mcpFixAction,
  mcpStatusGroup,
  usesSharedCredential,
  type McpStatusGroup,
} from '../connection-state';
import { MCP_AUTH_MODE_LABELS, isPersonalMcpInstance, type McpMyServerEntry, type McpServerTemplate } from '../types';
import { McpDisabledBadge, isMcpInstanceDisabled } from './mcp-stdio-policy';
import { McpServerIcon } from './mcp-server-icon';

type Filter = 'all' | McpStatusGroup;
const FILTERS: Filter[] = ['all', 'ready', 'action', 'unavailable'];
const FILTER_LABEL_KEYS: Record<Filter, string> = {
  all: 'workspace.mcpServers.personal.tabs.all',
  ready: 'workspace.mcpServers.personal.tabs.connected',
  action: 'workspace.mcpServers.filters.needsAction',
  unavailable: 'workspace.mcpServers.filters.unavailable',
};
const GROUP_ORDER: Record<McpStatusGroup, number> = { action: 0, unavailable: 1, ready: 2 };

interface McpServerListProps {
  instances: McpMyServerEntry[];
  /** The catalog, to name the service a server was made from. */
  templates: McpServerTemplate[];
  /** Status as an administrator sees it: a missing shared credential is theirs to add. */
  isAdmin: boolean;
  busyInstanceId: string | null;
  onOpen: (instance: McpMyServerEntry) => void;
  onConnect: (instance: McpMyServerEntry) => void;
  onReconnect: (instance: McpMyServerEntry) => void;
  /** Absent where this person can't change the server's settings. */
  onEdit?: (instance: McpMyServerEntry) => void;
}

/** Every server, one row each, with how it is doing and the one button that fixes it. Servers
 * that need something come first. */
export function McpServerList({ instances, templates, isAdmin, busyInstanceId, onOpen, onConnect, onReconnect, onEdit }: McpServerListProps) {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<Filter>('all');

  const rows = useMemo(
    () =>
      instances
        .map((instance) => {
          const state = isMcpInstanceDisabled(instance) ? ('disabled' as const) : mcpConnectionState(instance, { isAdmin });
          return { instance, state, group: mcpStatusGroup(state) };
        })
        .sort(
          (a, b) =>
            GROUP_ORDER[a.group] - GROUP_ORDER[b.group] ||
            a.instance.name.localeCompare(b.instance.name, undefined, { sensitivity: 'base' })
        ),
    [instances, isAdmin]
  );
  const counts: Record<Filter, number> = { all: rows.length, ready: 0, action: 0, unavailable: 0 };
  rows.forEach((row) => counts[row.group]++);
  const shown = filter === 'all' ? rows : rows.filter((row) => row.group === filter);

  return (
    <Flex direction="column" gap="3" style={{ width: '100%' }}>
      <Box className="no-scrollbar tab-scroll-fade" style={{ maxWidth: '100%', overflowX: 'auto' }}>
        <SegmentedTabs
          value={filter}
          onValueChange={(next) => setFilter(next as Filter)}
          aria-label={t('workspace.mcpServers.filters.label')}
          items={FILTERS.map((id) => ({ value: id, label: `${t(FILTER_LABEL_KEYS[id])} (${counts[id]})` }))}
          style={{ minWidth: 'max-content' }}
        />
      </Box>
      <Flex
        direction="column"
        style={{
          width: '100%',
          backgroundColor: 'var(--olive-2)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-3)',
          overflow: 'hidden',
        }}
      >
        {shown.length === 0 ? (
          <Text size="2" align="center" style={{ color: 'var(--gray-11)', padding: 'var(--space-5)' }}>
            {t('workspace.mcpServers.personal.noneInTab')}
          </Text>
        ) : (
          shown.map(({ instance, state }, index) => (
            <McpServerRow
              key={instance._id}
              instance={instance}
              template={templates.find((tpl) => tpl.typeId === instance.typeId) ?? null}
              state={state}
              isBusy={busyInstanceId === instance._id}
              isLast={index === shown.length - 1}
              onOpen={() => onOpen(instance)}
              onConnect={() => onConnect(instance)}
              onReconnect={() => onReconnect(instance)}
              onEdit={onEdit ? () => onEdit(instance) : undefined}
            />
          ))
        )}
      </Flex>
    </Flex>
  );
}

function McpServerRow({
  instance,
  template,
  state,
  isBusy,
  isLast,
  onOpen,
  onConnect,
  onReconnect,
  onEdit,
}: {
  instance: McpMyServerEntry;
  template: McpServerTemplate | null;
  state: ReturnType<typeof mcpConnectionState> | 'disabled';
  isBusy: boolean;
  isLast: boolean;
  onOpen: () => void;
  onConnect: () => void;
  onReconnect: () => void;
  onEdit?: () => void;
}) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [hovered, setHovered] = useState(false);

  const toolCount = asArray(instance.tools).length;
  const details = [
    template?.displayName ?? (instance.isCustom ? t('workspace.mcpServers.custom') : null),
    MCP_AUTH_MODE_LABELS[instance.authMode],
    usesSharedCredential(instance) ? t('workspace.mcpServers.sharedAuth') : null,
    toolCount > 0 ? t('workspace.mcpServers.toolsCount', { count: toolCount }) : null,
  ].filter(Boolean);

  const fix = mcpFixAction(state);
  const fixButton =
    fix && (fix !== 'edit' || onEdit) ? (
      <LoadingButton
        size={isMobile ? '3' : '2'}
        variant="soft"
        color="amber"
        loading={isBusy}
        onClick={(e) => {
          e.stopPropagation();
          if (fix === 'connect') onConnect();
          else if (fix === 'reconnect') onReconnect();
          else onEdit?.();
        }}
      >
        {t(fix === 'connect' ? 'workspace.mcpServers.cta.connect' : fix === 'reconnect' ? 'workspace.mcpServers.cta.reconnect' : 'workspace.mcpServers.configPanel.editServer')}
      </LoadingButton>
    ) : null;

  const status = state === 'disabled' ? <McpDisabledBadge instance={instance} /> : <McpStatusBadge state={state} />;

  return (
    // The whole row opens the server for a pointer; the name is the link a keyboard or screen
    // reader uses, so the fix button is never nested inside another control.
    <Flex
      data-testid="mcp-server-row"
      onClick={onOpen}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      align={isMobile ? 'start' : 'center'}
      gap="3"
      style={{
        padding: 'var(--space-3) var(--space-4)',
        borderBottom: isLast ? 'none' : '1px solid var(--olive-3)',
        backgroundColor: hovered ? 'var(--olive-3)' : 'transparent',
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <Flex
        align="center"
        justify="center"
        style={{ width: 36, height: 36, borderRadius: 'var(--radius-2)', backgroundColor: 'var(--gray-a3)', flexShrink: 0 }}
      >
        <McpServerIcon instanceId={instance._id} typeId={instance.typeId} name={instance.name} size={18} />
      </Flex>

      <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0 }}>
        <Flex align="center" gap="2" style={{ minWidth: 0 }}>
          <button
            type="button"
            role="link"
            onClick={(e) => {
              e.stopPropagation();
              onOpen();
            }}
            style={{
              appearance: 'none',
              margin: 0,
              padding: 0,
              border: 'none',
              background: 'none',
              font: 'inherit',
              textAlign: 'left',
              minWidth: 0,
              cursor: 'pointer',
            }}
          >
            <Text as="span" size="2" weight="medium" truncate style={{ color: 'var(--gray-12)', display: 'block' }}>
              {instance.name}
            </Text>
          </button>
          {isPersonalMcpInstance(instance) && (
            <Badge size="1" color="gray" variant="soft" style={{ flexShrink: 0 }}>
              {t('workspace.mcpServers.personal.yours')}
            </Badge>
          )}
          <McpInstanceBadges instance={instance} />
        </Flex>
        <Text
          size="1"
          truncate
          title={
            instance.toolsCachedAt
              ? t('workspace.mcpServers.toolsCachedAt', { time: new Date(instance.toolsCachedAt).toLocaleString() })
              : undefined
          }
          style={{ color: 'var(--gray-11)' }}
        >
          {details.join(' · ')}
        </Text>
        {instance.toolsError && (state === 'slow' || mcpStatusGroup(state) === 'unavailable') && (
          <Text
            size="1"
            truncate
            title={instance.toolsError}
            // A slow server still works in chat, so it isn't shown as a failure.
            style={{ color: state === 'slow' ? 'var(--gray-11)' : 'var(--red-11)' }}
          >
            {instance.toolsError}
          </Text>
        )}
        {isMobile && (
          <Flex direction="column" align="start" gap="2" style={{ marginTop: 'var(--space-1)' }}>
            {status}
            {fixButton}
          </Flex>
        )}
      </Flex>

      {!isMobile && (
        <>
          <Box style={{ width: 200, flexShrink: 0 }}>{status}</Box>
          <Flex justify="end" style={{ width: 132, flexShrink: 0 }}>
            {fixButton}
          </Flex>
        </>
      )}
      <MaterialIcon name="chevron_right" size={20} color="var(--gray-11)" style={{ flexShrink: 0, alignSelf: 'center' }} />
    </Flex>
  );
}
