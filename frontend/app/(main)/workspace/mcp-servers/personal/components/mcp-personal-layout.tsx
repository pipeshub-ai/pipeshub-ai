'use client';

import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Grid, Heading, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { WorkspaceHeaderIconButton } from '../../../components';
import type { McpMyServerEntry } from '../../types';
import { McpPersonalServerCard } from './mcp-personal-server-card';

interface McpPersonalLayoutProps {
  instances: McpMyServerEntry[];
  isLoading: boolean;
  searchQuery: string;
  busyInstanceId: string | null;
  onSearchChange: (q: string) => void;
  onRefresh: () => void;
  onAuthenticate: (instance: McpMyServerEntry) => void;
  onReauthenticate: (instance: McpMyServerEntry) => void;
  onRemoveCredentials: (instance: McpMyServerEntry) => void;
}

export function McpPersonalLayout({
  instances,
  isLoading,
  searchQuery,
  busyInstanceId,
  onSearchChange,
  onRefresh,
  onAuthenticate,
  onReauthenticate,
  onRemoveCredentials,
}: McpPersonalLayoutProps) {
  const { t } = useTranslation();

  // Ready/authenticated first (like connectors), then alphabetical.
  const filtered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    const list = !q
      ? instances
      : instances.filter(
          (i) => i.name.toLowerCase().includes(q) || (i.description ?? '').toLowerCase().includes(q)
        );
    return [...list].sort((a, b) => {
      const aReady =
        a.authMode === 'none' || a.useAdminAuth || a.isAuthenticated ? 0 : 1;
      const bReady =
        b.authMode === 'none' || b.useAdminAuth || b.isAuthenticated ? 0 : 1;
      if (aReady !== bReady) return aReady - bReady;
      return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
    });
  }, [instances, searchQuery]);

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
        <WorkspaceHeaderIconButton icon="refresh" onClick={onRefresh} />
      </Flex>

      {instances.length > 0 && (
        <TextField.Root
          size="2"
          placeholder={t('workspace.mcpServers.searchPlaceholder')}
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          style={{ maxWidth: 360 }}
        >
          <TextField.Slot>
            <MaterialIcon name="search" size={16} color="var(--gray-9)" />
          </TextField.Slot>
        </TextField.Root>
      )}

      {isLoading ? (
        <Flex align="center" justify="center" style={{ width: '100%', flex: 1 }}>
          <LottieLoader variant="loader" size={48} showLabel label={t('workspace.mcpServers.loading')} />
        </Flex>
      ) : instances.length === 0 ? (
        <Flex direction="column" align="center" justify="center" gap="3" style={{ width: '100%', flex: 1, paddingTop: 80 }}>
          <MaterialIcon name="hub" size={48} color="var(--gray-9)" />
          <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {t('workspace.mcpServers.personal.emptyTitle')}
          </Text>
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {t('workspace.mcpServers.personal.emptyDescription')}
          </Text>
        </Flex>
      ) : filtered.length === 0 ? (
        <Flex align="center" justify="center" style={{ width: '100%', flex: 1 }}>
          <Text size="2" style={{ color: 'var(--gray-10)' }}>{t('workspace.mcpServers.noResults')}</Text>
        </Flex>
      ) : (
        <Grid columns={{ initial: '2', md: '3', lg: '4' }} gap="4" style={{ width: '100%' }}>
          {filtered.map((instance) => (
            <McpPersonalServerCard
              key={instance._id}
              instance={instance}
              isBusy={busyInstanceId === instance._id}
              onAuthenticate={() => onAuthenticate(instance)}
              onReauthenticate={() => onReauthenticate(instance)}
              onRemoveCredentials={() => onRemoveCredentials(instance)}
            />
          ))}
        </Grid>
      )}
    </Flex>
  );
}
