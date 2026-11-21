'use client';

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Grid, Text } from '@radix-ui/themes';
import { McpServersApi } from '../api';
import type { McpPopularServer, McpServerTemplate } from '../types';
import { McpServerIcon } from './mcp-server-icon';

interface McpPopularRowProps {
  /** The catalog entries this person may add; a popular server opens its entry. */
  catalog: McpServerTemplate[];
  onOpen: (template: McpServerTemplate) => void;
}

/** The organization's servers most people signed in to. Shows nothing until there are some. */
export function McpPopularRow({ catalog, onOpen }: McpPopularRowProps) {
  const { t } = useTranslation();
  const [servers, setServers] = useState<McpPopularServer[]>([]);

  useEffect(() => {
    let cancelled = false;
    // Only a hint: when it can't load, the catalog below still works.
    McpServersApi.getPopular()
      .then((rows) => !cancelled && setServers(rows))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const byType = new Map(catalog.map((tpl) => [tpl.typeId, tpl]));
  const shown = servers.filter((s) => s.typeId && byType.has(s.typeId));
  if (shown.length === 0) return null;

  return (
    <Flex direction="column" gap="2" data-testid="mcp-popular">
      <Flex align="baseline" gap="2" wrap="wrap">
        <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {t('workspace.mcpServers.discover.popularTitle')}
        </Text>
        <Text size="1" style={{ color: 'var(--gray-10)' }}>
          {t('workspace.mcpServers.discover.popularHint')}
        </Text>
      </Flex>
      <Grid gap="2" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(min(200px, 100%), 1fr))' }}>
        {shown.map((s) => (
          <button
            key={s.instanceId}
            type="button"
            onClick={() => onOpen(byType.get(s.typeId!)!)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--space-2)',
              padding: 'var(--space-2) var(--space-3)',
              border: '1px solid var(--olive-3)',
              borderRadius: 'var(--radius-2)',
              background: 'var(--olive-2)',
              textAlign: 'left',
              cursor: 'pointer',
              minWidth: 0,
              font: 'inherit',
            }}
          >
            <McpServerIcon typeId={s.typeId ?? undefined} name={s.name} size={20} color="var(--gray-11)" />
            <Flex direction="column" style={{ minWidth: 0 }}>
              <Text size="2" weight="medium" truncate style={{ color: 'var(--gray-12)' }}>
                {s.name}
              </Text>
              <Text size="1" style={{ color: 'var(--gray-10)' }}>
                {t('workspace.mcpServers.discover.peopleUse', { count: s.people })}
              </Text>
            </Flex>
          </button>
        ))}
      </Grid>
    </Flex>
  );
}
