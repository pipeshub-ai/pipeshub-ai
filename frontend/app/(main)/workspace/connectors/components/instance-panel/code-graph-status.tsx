'use client';

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Flex, Text } from '@radix-ui/themes';
import { ConnectorsApi } from '../../api';
import type { CodeGraphRepo, CodeGraphStatus } from '../../types';

type BadgeColor = React.ComponentProps<typeof Badge>['color'];

const STATUS_COLOR: Record<CodeGraphStatus, BadgeColor> = {
  ready: 'green',
  stale: 'amber',
  building: 'blue',
  waiting_on_indexing: 'blue',
  never_built: 'gray',
  retrying: 'amber',
  failed: 'red',
  too_large: 'red',
  unknown: 'gray',
};

/**
 * Each repo's code-graph status (built, pending, failed) for one code
 * connector, read from GET /api/v1/code-graph/repos (O-3).
 */
export function CodeGraphStatus({ connectorId }: { connectorId: string }) {
  const { t } = useTranslation();
  const [repos, setRepos] = useState<CodeGraphRepo[] | null>(null);

  useEffect(() => {
    let live = true;
    ConnectorsApi.getCodeGraphRepos()
      .then((all) => {
        if (live) setRepos(all.filter((r) => r.connector_id === connectorId));
      })
      // The panel is advisory: no code graph service means no panel.
      .catch(() => {
        if (live) setRepos([]);
      });
    return () => {
      live = false;
    };
  }, [connectorId]);

  if (!repos || repos.length === 0) return null;

  return (
    <Flex direction="column" gap="2">
      <Text size="2" weight="medium">
        {t('workspace.connectors.overview.codeGraph.title')}
      </Text>
      {repos.map((repo) => (
        <Flex key={repo.repo} align="center" justify="between" gap="3">
          <Text size="2" style={{ overflowWrap: 'anywhere' }}>
            {repo.repo}
          </Text>
          <Badge
            color={STATUS_COLOR[repo.graph_status] ?? 'gray'}
            variant="soft"
            title={repo.last_error}
          >
            {t(`workspace.connectors.overview.codeGraph.status.${repo.graph_status}`)}
          </Badge>
        </Flex>
      ))}
    </Flex>
  );
}
