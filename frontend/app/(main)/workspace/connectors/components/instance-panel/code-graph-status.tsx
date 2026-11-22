'use client';

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Flex, Link, Text } from '@radix-ui/themes';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { ConnectorsApi } from '../../api';
import type { CodeGraphRepo, CodeGraphStatus } from '../../types';

/** The code graph explorer, while it is switched on for this build. */
const GRAPH_EXPLORER_ACTIVE = process.env.NEXT_PUBLIC_GRAPH_EXPLORER_ACTIVE === 'true';

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
  const isMobile = useIsMobile();
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
          {/* A repository with a graph (or one being built) can be explored: the page opens with search scoped to it. */}
          {GRAPH_EXPLORER_ACTIVE && !['never_built', 'too_large'].includes(repo.graph_status) ? (
            <Link
              size="2"
              href={`/graph/?repo=${encodeURIComponent(repo.repo)}`}
              aria-label={t('graph.ui.viewGraphOf', { repo: repo.repo })}
              style={isMobile ? { display: 'inline-flex', alignItems: 'center', minHeight: 44 } : undefined}
            >
              {t('graph.ui.viewGraph')}
            </Link>
          ) : null}
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
