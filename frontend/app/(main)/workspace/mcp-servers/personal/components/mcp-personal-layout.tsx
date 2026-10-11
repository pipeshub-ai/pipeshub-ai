'use client';

import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Grid, Heading, SegmentedControl, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { WorkspaceHeaderIconButton } from '../../../components';
import { McpAddServerButton, isMcpInstanceDisabled } from '../../components';
import { offeredForPersonalServers } from '../../catalog-replacement';
import { mcpConnectionState } from '../../connection-state';
import { McpServerCard } from '../../team/components/mcp-server-card';
import type { McpMyServerEntry, McpServerTemplate } from '../../types';
import { isPersonalMcpInstance } from '../../types';
import { McpPersonalServerCard } from './mcp-personal-server-card';

type Tab = 'all' | 'connected' | 'notConnected';
const TABS: Tab[] = ['all', 'connected', 'notConnected'];

interface McpPersonalLayoutProps {
  instances: McpMyServerEntry[];
  /** The catalog: entries nobody here has set up yet are offered as cards to set up. */
  templates: McpServerTemplate[];
  isLoading: boolean;
  searchQuery: string;
  busyInstanceId: string | null;
  onSearchChange: (q: string) => void;
  onRefresh: () => void;
  onAddServer: () => void;
  onSetUp: (template: McpServerTemplate) => void;
  onAuthenticate: (instance: McpMyServerEntry) => void;
  onReauthenticate: (instance: McpMyServerEntry) => void;
  onRemoveCredentials: (instance: McpMyServerEntry) => void;
  onToolApprovals: (instance: McpMyServerEntry) => void;
  onEdit: (instance: McpMyServerEntry) => void;
  onDelete: (instance: McpMyServerEntry) => void;
}

/** Usable now: what the Connected tab shows. A slow server still answers in chat. */
export function isConnected(instance: McpMyServerEntry): boolean {
  if (isMcpInstanceDisabled(instance)) return false;
  const state = mcpConnectionState(instance);
  return state === 'ready' || state === 'slow';
}

/** Like the toolsets page: every server the person can use, then the catalog entries they could
 * set up, in one grid with All / Connected / Not connected tabs. */
export function McpPersonalLayout({
  instances,
  templates,
  isLoading,
  searchQuery,
  busyInstanceId,
  onSearchChange,
  onRefresh,
  onAddServer,
  onSetUp,
  onAuthenticate,
  onReauthenticate,
  onRemoveCredentials,
  onToolApprovals,
  onEdit,
  onDelete,
}: McpPersonalLayoutProps) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>('all');
  const q = searchQuery.trim().toLowerCase();

  const { servers, available } = useMemo(() => {
    const matches = (name: string, description?: string | null) =>
      !q || name.toLowerCase().includes(q) || (description ?? '').toLowerCase().includes(q);
    const byName = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: 'base' });
    // Connected first, then your own before the organization's, then by name.
    const sorted = instances
      .filter((i) => matches(i.name, i.description))
      .sort(
        (a, b) =>
          Number(!isConnected(a)) - Number(!isConnected(b)) ||
          Number(!isPersonalMcpInstance(a)) - Number(!isPersonalMcpInstance(b)) ||
          byName(a.name, b.name)
      );
    const setUpTypes = new Set(instances.map((i) => i.typeId).filter(Boolean));
    const unset = offeredForPersonalServers(templates)
      .filter((tpl) => !setUpTypes.has(tpl.typeId))
      .filter((tpl) => matches(tpl.displayName, tpl.description))
      .sort((a, b) => byName(a.displayName, b.displayName));
    return { servers: sorted, available: unset };
  }, [instances, templates, q]);

  const connected = servers.filter(isConnected);
  const counts: Record<Tab, number> = {
    all: servers.length + available.length,
    connected: connected.length,
    notConnected: servers.length - connected.length + available.length,
  };
  const shownServers = tab === 'all' ? servers : tab === 'connected' ? connected : servers.filter((i) => !isConnected(i));
  const shownTemplates = tab === 'connected' ? [] : available;
  const nothingAtAll = instances.length === 0 && offeredForPersonalServers(templates).length === 0;

  return (
    <Flex
      direction="column"
      gap="5"
      style={{
        width: '100%',
        height: '100%',
        paddingTop: 64,
        paddingBottom: 64,
        paddingLeft: 100,
        paddingRight: 100,
        overflowY: 'auto',
        background: 'linear-gradient(to bottom, var(--olive-2), var(--olive-1))',
      }}
    >
      <Flex justify="between" align="start" gap="2" style={{ width: '100%' }}>
        <Flex direction="column" gap="2" style={{ flex: 1 }}>
          <Heading size="5" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {t('workspace.mcpServers.personal.title')}
          </Heading>
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {t('workspace.mcpServers.personal.subtitle')}
          </Text>
        </Flex>
        <TextField.Root
          size="2"
          placeholder={t('workspace.mcpServers.searchPlaceholder')}
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          style={{ width: 224, flexShrink: 0 }}
        >
          <TextField.Slot>
            <MaterialIcon name="search" size={16} color="var(--gray-9)" />
          </TextField.Slot>
        </TextField.Root>
      </Flex>

      <Flex align="center" justify="between" gap="2" wrap="wrap" style={{ width: '100%' }}>
        <SegmentedControl.Root value={tab} onValueChange={(value) => setTab(value as Tab)} size="2">
          {TABS.map((id) => (
            <SegmentedControl.Item key={id} value={id}>
              {t(`workspace.mcpServers.personal.tabs.${id}`)} ({counts[id]})
            </SegmentedControl.Item>
          ))}
        </SegmentedControl.Root>
        <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
          <McpAddServerButton onClick={onAddServer} />
          <WorkspaceHeaderIconButton icon="refresh" onClick={onRefresh} />
        </Flex>
      </Flex>

      {isLoading ? (
        <Flex align="center" justify="center" style={{ width: '100%', flex: 1 }}>
          <LottieLoader variant="loader" size={48} showLabel label={t('workspace.mcpServers.loading')} />
        </Flex>
      ) : shownServers.length + shownTemplates.length === 0 ? (
        <Flex direction="column" align="center" justify="center" gap="2" style={{ width: '100%', paddingTop: 80 }}>
          <MaterialIcon name="hub" size={48} color="var(--gray-9)" />
          <Text size="2" weight="medium" style={{ color: 'var(--gray-11)' }}>
            {nothingAtAll
              ? t('workspace.mcpServers.personal.emptyTitle')
              : q
                ? t('workspace.mcpServers.noResults')
                : t('workspace.mcpServers.personal.noneInTab')}
          </Text>
          {nothingAtAll && (
            <Text size="2" style={{ color: 'var(--gray-10)' }}>
              {t('workspace.mcpServers.personal.emptyDescription')}
            </Text>
          )}
        </Flex>
      ) : (
        <Grid columns={{ initial: '2', md: '3', lg: '3' }} gap="4" style={{ width: '100%' }}>
          {shownServers.map((instance) => {
            const own = isPersonalMcpInstance(instance);
            return (
              <McpPersonalServerCard
                key={instance._id}
                instance={instance}
                isBusy={busyInstanceId === instance._id}
                onAuthenticate={() => onAuthenticate(instance)}
                onReauthenticate={() => onReauthenticate(instance)}
                onRemoveCredentials={() => onRemoveCredentials(instance)}
                onToolApprovals={() => onToolApprovals(instance)}
                onEdit={own ? () => onEdit(instance) : undefined}
                onDelete={own ? () => onDelete(instance) : undefined}
              />
            );
          })}
          {shownTemplates.map((tpl) => (
            <McpServerCard
              key={`template-${tpl.typeId}`}
              title={tpl.displayName}
              description={tpl.description}
              iconUrl={tpl.icon}
              variant="registry"
              onSetup={() => onSetUp(tpl)}
            />
          ))}
        </Grid>
      )}
    </Flex>
  );
}
